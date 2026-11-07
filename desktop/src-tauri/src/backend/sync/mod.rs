pub mod commands;
pub(crate) mod scheduler;
#[cfg(test)]
pub(crate) mod tests;
mod transport;
mod versions;
#[cfg(test)]
mod versions_tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::LearningState;
use super::providers::{self, ProviderState};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use zeroize::{Zeroize, Zeroizing};

static NEXT_JOB: AtomicU64 = AtomicU64::new(0);
static ACTIVE: OnceLock<Mutex<HashMap<PathBuf, Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>>>> =
    OnceLock::new();
#[cfg(test)]
static DEFER: OnceLock<Arc<AtomicBool>> = OnceLock::new();

pub struct SyncService {
    learning: LearningState,
    providers: ProviderState,
}

impl SyncService {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            learning: learning.shared(),
            providers: learning.provider_state(),
        }
    }

    fn shared(&self) -> Self {
        Self {
            learning: self.learning.shared(),
            providers: self.providers.shared(),
        }
    }

    fn store(&self) -> Result<Store> {
        self.learning.store()
    }

    fn active(&self) -> Arc<Mutex<HashMap<String, Arc<AtomicBool>>>> {
        let mut databases = ACTIVE
            .get_or_init(|| Mutex::new(HashMap::new()))
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        Arc::clone(
            databases
                .entry(self.learning.workspace_path().to_path_buf())
                .or_default(),
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SyncConfig {
    pub kind: String,
    pub endpoint: String,
    pub remote_path: String,
    pub region: String,
    pub bucket: String,
    pub auth_kind: String,
    pub username: String,
    pub allow_private: bool,
    #[serde(default)]
    pub auto_sync: bool,
    #[serde(default)]
    pub updated_at: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SyncConfigInput {
    pub kind: String,
    pub endpoint: String,
    pub remote_path: String,
    pub region: String,
    pub bucket: String,
    pub auth_kind: String,
    pub username: String,
    pub allow_private: bool,
    pub auto_sync: bool,
    pub password: Option<String>,
    pub access_key: Option<String>,
    pub secret_key: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncConfigView {
    pub config: SyncConfig,
    pub has_credentials: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncOverview {
    pub config: Option<SyncConfigView>,
    pub jobs: Vec<SyncJobView>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncJobView {
    pub id: String,
    pub kind: String,
    pub status: String,
    pub remote_path: String,
    pub result: Option<Value>,
    pub local_path: Option<String>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
}

pub enum SyncCredentials {
    None,
    Webdav {
        username: String,
        password: Zeroizing<String>,
    },
    S3 {
        access_key: Zeroizing<String>,
        secret_key: Zeroizing<String>,
    },
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS sync_config (
            id INTEGER PRIMARY KEY CHECK(id = 1), config_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, config_json TEXT NOT NULL,
            remote_path TEXT NOT NULL, result_json TEXT, local_path TEXT, error_code TEXT, error_message TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT
        );
        -- 27 信息性#1：原 (id, created_at) 以唯一 PK 前导、排序用不上——
        -- 换成 created_at 单列索引支撑列表 ORDER BY created_at DESC
        DROP INDEX IF EXISTS sync_jobs_created;
        CREATE INDEX IF NOT EXISTS sync_jobs_created ON sync_jobs(created_at);",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM sync_jobs", [])?;
    conn.execute("DELETE FROM sync_config", [])?;
    Ok(())
}

fn valid_remote_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 512
        && !value.starts_with('/')
        && !value.ends_with('/')
        && !value.contains("//")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b'/'))
        && value
            .split('/')
            .all(|segment| !segment.is_empty() && segment != "." && segment != "..")
}

fn validate_input(input: &SyncConfigInput) -> Result<SyncConfig> {
    if !["webdav", "s3"].contains(&input.kind.as_str())
        || !valid_remote_path(&input.remote_path)
        || !input.remote_path.ends_with(".json")
    {
        return Err(BackendError::invalid(
            "invalid_sync_path",
            "同步类型或远端对象路径无效",
        ));
    }
    let scope = if input.allow_private {
        "本地模型"
    } else {
        "官方"
    };
    let _ = providers::openai::endpoint(input.endpoint.trim(), scope, "")?;
    if input.kind == "s3" {
        if input.auth_kind != "aws4"
            || input.region.is_empty()
            || input.region.len() > 64
            || !input
                .region
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
            || !(3..=63).contains(&input.bucket.len())
            || !input.bucket.bytes().all(|byte| {
                byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'-')
            })
        {
            return Err(BackendError::invalid(
                "invalid_s3_config",
                "S3 区域、bucket 或签名方式无效",
            ));
        }
    } else {
        if !["none", "basic", "bearer"].contains(&input.auth_kind.as_str())
            || !input.region.is_empty()
            || !input.bucket.is_empty()
        {
            return Err(BackendError::invalid(
                "invalid_webdav_config",
                "WebDAV 认证方式无效",
            ));
        }
        if input.auth_kind == "basic" && input.username.trim().is_empty() {
            return Err(BackendError::invalid(
                "invalid_sync_username",
                "Basic 认证需要账号",
            ));
        }
    }
    Ok(SyncConfig {
        kind: input.kind.clone(),
        endpoint: input.endpoint.trim().into(),
        remote_path: input.remote_path.clone(),
        region: input.region.clone(),
        bucket: input.bucket.clone(),
        auth_kind: input.auth_kind.clone(),
        username: input.username.trim().into(),
        allow_private: input.allow_private,
        auto_sync: input.auto_sync,
        updated_at: now()?,
    })
}

fn credentials(service: &SyncService, config: &SyncConfig) -> Result<SyncCredentials> {
    let backend = service.providers.credential_backend();
    match config.kind.as_str() {
        "s3" => {
            let access_key = backend.get("sync-s3-access")?.ok_or_else(|| {
                BackendError::new("sync_credentials_missing", "S3 Access Key 未配置")
            })?;
            let secret_key = backend.get("sync-s3-secret")?.ok_or_else(|| {
                BackendError::new("sync_credentials_missing", "S3 Secret Key 未配置")
            })?;
            Ok(SyncCredentials::S3 {
                access_key,
                secret_key,
            })
        }
        _ if config.auth_kind == "none" => Ok(SyncCredentials::None),
        _ => {
            let password = backend.get("sync-webdav")?.ok_or_else(|| {
                BackendError::new("sync_credentials_missing", "WebDAV 凭据未配置")
            })?;
            Ok(SyncCredentials::Webdav {
                username: config.username.clone(),
                password,
            })
        }
    }
}

fn has_credentials(service: &SyncService, config: &SyncConfig) -> Result<bool> {
    Ok(match config.kind.as_str() {
        "s3" => {
            service
                .providers
                .credential_backend()
                .get("sync-s3-access")?
                .is_some()
                && service
                    .providers
                    .credential_backend()
                    .get("sync-s3-secret")?
                    .is_some()
        }
        _ if config.auth_kind == "none" => false,
        _ => service
            .providers
            .credential_backend()
            .get("sync-webdav")?
            .is_some(),
    })
}

fn config(service: &SyncService) -> Result<Option<SyncConfig>> {
    let store = service.store()?;
    let value = store
        .conn
        .query_row(
            "SELECT config_json, updated_at FROM sync_config WHERE id = 1",
            [],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()?;
    value
        .map(|(value, updated_at)| {
            let mut config: SyncConfig = serde_json::from_str(&value)?;
            config.updated_at = updated_at;
            Ok::<_, BackendError>(config)
        })
        .transpose()
}

fn config_view(service: &SyncService, config: SyncConfig) -> Result<SyncConfigView> {
    Ok(SyncConfigView {
        has_credentials: has_credentials(service, &config)?,
        config,
    })
}

fn restore_credentials(
    service: &SyncService,
    previous: &HashMap<&'static str, Option<Zeroizing<String>>>,
) {
    let backend = service.providers.credential_backend();
    for (id, value) in previous {
        let _ = match value {
            Some(value) => backend.set(id, value),
            None => backend.delete(id),
        };
    }
}

pub fn save_config<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    mut input: SyncConfigInput,
) -> Result<SyncConfigView> {
    let config = validate_input(&input)?;
    let backend = service.providers.credential_backend();
    let mut previous = HashMap::new();
    for id in ["sync-webdav", "sync-s3-access", "sync-s3-secret"] {
        previous.insert(id, backend.get(id)?);
    }
    let result = (|| {
        if config.kind == "s3" {
            let clearing = matches!(input.access_key.as_deref(), Some(""))
                || matches!(input.secret_key.as_deref(), Some(""));
            if let Some(value) = input.access_key.as_mut() {
                if value.is_empty() {
                    backend.delete("sync-s3-access")?;
                } else {
                    backend.set("sync-s3-access", value)?;
                }
                value.zeroize();
            }
            if let Some(value) = input.secret_key.as_mut() {
                if value.is_empty() {
                    backend.delete("sync-s3-secret")?;
                } else {
                    backend.set("sync-s3-secret", value)?;
                }
                value.zeroize();
            }
            if !clearing {
                let _ = credentials(service, &config)?;
            }
        } else {
            let clearing = matches!(input.password.as_deref(), Some(""));
            if let Some(value) = input.password.as_mut() {
                if value.is_empty() {
                    backend.delete("sync-webdav")?;
                } else {
                    backend.set("sync-webdav", value)?;
                }
                value.zeroize();
            }
            if config.auth_kind != "none" && !clearing {
                let _ = credentials(service, &config)?;
            }
        }
        let store = service.store()?;
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("INSERT INTO sync_config (id, config_json, updated_at) VALUES (1, ?1, ?2) ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at", params![serde_json::to_string(&config)?, now()?])?;
        let event = Store::event(
            &tx,
            "sync.config_saved",
            json!({"kind": config.kind, "remotePath": config.remote_path}),
        )?;
        tx.commit()?;
        emit(app, &event);
        scheduler::start_scheduler(app.clone());
        Ok(())
    })();
    if let Err(error) = result {
        restore_credentials(service, &previous);
        return Err(error);
    }
    config_view(service, config)
}

pub fn overview(service: &SyncService) -> Result<SyncOverview> {
    let config = config(service)?
        .map(|config| config_view(service, config))
        .transpose()?;
    let store = service.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM sync_jobs ORDER BY created_at DESC, id DESC LIMIT 50")?;
    let ids = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let jobs = ids
        .into_iter()
        .map(|id| job(service, &id))
        .collect::<Result<Vec<_>>>()?;
    Ok(SyncOverview { config, jobs })
}

pub fn job(service: &SyncService, id: &str) -> Result<SyncJobView> {
    let store = service.store()?;
    store.conn.query_row("SELECT kind, status, remote_path, result_json, local_path, error_code, error_message, created_at, updated_at FROM sync_jobs WHERE id = ?1", params![id], |row| {
        let result: Option<String> = row.get(3)?;
        let code: Option<String> = row.get(5)?;
        let message: Option<String> = row.get(6)?;
        Ok(SyncJobView {
            id: id.to_string(),
            kind: row.get(0)?, status: row.get(1)?, remote_path: row.get(2)?,
            result: result.map(|value| serde_json::from_str(&value)).transpose().map_err(|_| rusqlite::Error::InvalidQuery)?,
            local_path: row.get(4)?, error: code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("同步任务失败"))),
            created_at: row.get(7)?, updated_at: row.get(8)?,
        })
    }).optional()?.ok_or_else(|| BackendError::new("not_found", "同步任务不存在"))
}

fn stage<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    id: &str,
    status: &str,
    stage: &str,
) -> Result<()> {
    let store = service.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE sync_jobs SET status = ?1, updated_at = ?2 WHERE id = ?3",
        params![status, now()?, id],
    )?;
    let event = Store::event(
        &tx,
        "sync.progress",
        json!({"jobId": id, "stage": stage, "status": status}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn cancelled(service: &SyncService, id: &str) -> bool {
    service
        .active()
        .lock()
        .ok()
        .and_then(|active| active.get(id).cloned())
        .is_some_and(|flag| flag.load(Ordering::Acquire))
}

fn check_cancelled(service: &SyncService, id: &str) -> Result<()> {
    if cancelled(service, id) {
        Err(BackendError::new(
            "cancelled",
            "同步已取消；远端请求可能已执行，请核对任务与远端状态",
        ))
    } else {
        Ok(())
    }
}

struct Outcome {
    result: Value,
    local_path: Option<String>,
}

fn run<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    id: &str,
    kind: &str,
    config: &SyncConfig,
    destination: Option<PathBuf>,
) -> Result<Outcome> {
    #[cfg(test)]
    if let Some(defer) = DEFER.get() {
        let deadline = Instant::now() + Duration::from_secs(15);
        while defer.load(Ordering::Acquire) {
            if Instant::now() > deadline {
                return Err(BackendError::new(
                    "sync_test_timeout",
                    "测试同步任务未能继续",
                ));
            }
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    let credentials = credentials(service, config)?;
    check_cancelled(service, id)?;
    stage(app, service, id, "running", "request")?;
    match kind {
        "check" => {
            // 13#30：对象尚未创建（新配置首次上传前的正常态）返回 404，
            // 原样折叠为 sync_http_error 会把正常态判为失败并误导用户
            // 以为凭据有误；区分“服务可达但对象不存在”。
            match transport::send(
                config,
                &credentials,
                if config.kind == "s3" {
                    "HEAD"
                } else {
                    "PROPFIND"
                },
                None,
            ) {
                Ok(_) => Ok(Outcome {
                    result: json!({"reachable": true, "objectPresent": true, "note": ""}),
                    local_path: None,
                }),
                Err(error) if error.code == "sync_http_error" && error.message.contains("404") => {
                    Ok(Outcome {
                        result: json!({
                            "reachable": true,
                            "objectPresent": false,
                            "note": "服务连接正常；同步对象尚未创建，首次上传后自动建立",
                        }),
                        local_path: None,
                    })
                }
                Err(error) => Err(error),
            }
        }
        "upload" => {
            let bytes = serde_json::to_vec_pretty(&providers::export_config(&service.providers)?)?;
            let preview = providers::sync_config::preview(&bytes)?;
            versions::upload(app, service, id, config, &credentials, bytes, preview)
        }
        "download" | "import" | "restore" => {
            let bytes = transport::send(config, &credentials, "GET", None)?;
            let preview = providers::sync_config::preview(&bytes)?;
            let import = kind == "import" || (kind == "restore" && destination.is_none());
            let path = match &destination {
                Some(path) => path.clone(),
                None => service
                    .learning
                    .workspace_path()
                    .parent()
                    .unwrap_or_else(|| Path::new("."))
                    .join("sync")
                    .join(format!("{id}.json")),
            };
            super::materials::atomic_export(&path, &bytes)?;
            // 13#29：本地回写文件保留最近 10 份，更早的清理——
            // 原先无清理路径，users/<id>/sync 随任务数无限累积。
            // 15#3：清理只作用于默认 sync 目录（destination 为用户自选路径时
            // 该目录不属于本应用管理，绝不枚举删除）；且只碰 sync- 前缀的
            // 本应用产物，按修改时间排序，避免进程重启后 pid 变化导致
            // 字典序与时间序脱节而误删刚写完的文件
            if destination.is_none() {
                if let Some(parent) = path.parent() {
                    let mut stale: Vec<(std::time::SystemTime, std::path::PathBuf)> =
                        std::fs::read_dir(parent)
                            .map(|entries| {
                                entries
                                    .filter_map(std::result::Result::ok)
                                    .filter(|entry| {
                                        entry.file_name().to_string_lossy().starts_with("sync-")
                                    })
                                    .filter(|entry| {
                                        entry.path().extension().is_some_and(|ext| ext == "json")
                                    })
                                    .filter_map(|entry| {
                                        let modified =
                                            entry.metadata().and_then(|meta| meta.modified()).ok();
                                        modified.map(|time| (time, entry.path()))
                                    })
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default();
                    if stale.len() > 10 {
                        stale.sort_by_key(|(modified, _)| *modified);
                        for (_, old) in &stale[..stale.len() - 10] {
                            let _ = std::fs::remove_file(old);
                        }
                    }
                }
            }
            check_cancelled(service, id)?;
            stage(app, service, id, "running", "verify")?;
            if std::fs::read(&path)? != bytes {
                return Err(BackendError::new(
                    "sync_verify_failed",
                    "本地同步文件写入校验失败",
                ));
            }
            if import {
                let imported = providers::sync_config::import(app, &service.providers, &bytes)?;
                Ok(Outcome {
                    result: serde_json::to_value(imported)?,
                    local_path: Some(path.to_string_lossy().into_owned()),
                })
            } else {
                Ok(Outcome {
                    result: serde_json::to_value(preview)?,
                    local_path: Some(path.to_string_lossy().into_owned()),
                })
            }
        }
        _ => Err(BackendError::invalid(
            "invalid_sync_job",
            "同步任务类型不受支持",
        )),
    }
}

fn finish<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    id: &str,
    result: Result<Outcome>,
) -> Result<()> {
    let (status, code, message, result_json, local_path) = match result {
        Ok(outcome) => ("done", None, None, Some(outcome.result), outcome.local_path),
        Err(error) => {
            let status = if error.code == "cancelled" {
                "cancelled"
            } else {
                "failed"
            };
            (status, Some(error.code), Some(error.message), None, None)
        }
    };
    let store = service.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE sync_jobs SET status = ?1, result_json = ?2, local_path = ?3, error_code = ?4, error_message = ?5, updated_at = ?6, finished_at = ?6 WHERE id = ?7", params![status, result_json.as_ref().map(serde_json::to_string).transpose()?, local_path, code, message, now()?, id])?;
    let event = Store::event(
        &tx,
        if status == "done" {
            "sync.completed"
        } else if status == "cancelled" {
            "sync.cancelled"
        } else {
            "sync.failed"
        },
        json!({"jobId": id, "status": status, "result": result_json, "error": code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("同步任务失败")))}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn start_with_path<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    kind: String,
    destination: Option<String>,
    remote_path: Option<String>,
) -> Result<SyncJobView> {
    if !["check", "upload", "download", "import", "restore"].contains(&kind.as_str()) {
        return Err(BackendError::invalid(
            "invalid_sync_job",
            "同步任务类型不受支持",
        ));
    }
    let mut config = config(service)?
        .ok_or_else(|| BackendError::new("sync_not_configured", "请先保存同步配置"))?;
    if let Some(path) = remote_path {
        config.remote_path = path;
    }
    if kind == "download"
        && !destination.as_deref().is_some_and(|path| {
            Path::new(path)
                .extension()
                .and_then(|value| value.to_str())
                .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
        })
    {
        return Err(BackendError::invalid(
            "invalid_sync_destination",
            "下载配置必须使用 .json 文件",
        ));
    }
    if ["check", "upload", "import"].contains(&kind.as_str()) && destination.is_some() {
        return Err(BackendError::invalid(
            "invalid_sync_destination",
            "该同步任务不接受本地输出路径",
        ));
    }
    let id = format!(
        "sync-{}-{}",
        std::process::id(),
        NEXT_JOB.fetch_add(1, Ordering::Relaxed)
    );
    {
        let active_jobs = service.active();
        let mut active = active_jobs
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "同步任务状态不可用"))?;
        if !active.is_empty() {
            return Err(BackendError::new(
                "sync_busy",
                "已有同步任务正在执行，请等待完成或取消",
            ));
        }
        active.insert(id.clone(), Arc::new(AtomicBool::new(false)));
    }
    let result = (|| {
        let store = service.store()?;
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("INSERT INTO sync_jobs (id, kind, status, config_json, remote_path, created_at, updated_at) VALUES (?1, ?2, 'running', ?3, ?4, ?5, ?5)", params![id, kind, serde_json::to_string(&config)?, config.remote_path, now()?])?;
        let event = Store::event(
            &tx,
            "sync.started",
            json!({"jobId": id, "kind": kind, "remotePath": config.remote_path}),
        )?;
        tx.commit()?;
        Ok(event)
    })();
    let event = match result {
        Ok(event) => event,
        Err(error) => {
            let active_jobs = service.active();
            if let Ok(mut active) = active_jobs.lock() {
                active.remove(&id);
            }
            return Err(error);
        }
    };
    emit(app, &event);
    let worker_service = service.shared();
    let worker_app = app.clone();
    let worker_id = id.clone();
    let destination = destination.map(PathBuf::from);
    let spawn = super::security::thread_builder()
        .name(format!("sync-{id}"))
        .spawn(move || {
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run(
                    &worker_app,
                    &worker_service,
                    &worker_id,
                    &kind,
                    &config,
                    destination,
                )
            }));
            let outcome = match outcome {
                Ok(result) => result,
                Err(_) => Err(BackendError::new("task_panicked", "同步任务异常终止")),
            };
            let _ = finish(&worker_app, &worker_service, &worker_id, outcome);
            if let Ok(mut active) = worker_service.active().lock() {
                active.remove(&worker_id);
            }
        });
    if let Err(_spawn_error) = spawn {
        let active_jobs = service.active();
        if let Ok(mut active) = active_jobs.lock() {
            active.remove(&id);
        }
        let _ = finish(
            app,
            service,
            &id,
            Err(BackendError::new("task_start_failed", "无法启动同步线程")),
        );
        return Err(BackendError::new(
            // 29#3：不把 OS io::Error 原文透给用户界面，技术细节留给日志侧排查
            "task_start_failed",
            "无法启动同步线程",
        ));
    }
    job(service, &id)
}

pub fn start<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    kind: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    start_with_path(app, service, kind, destination, None)
}

pub fn restore<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    version_path: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    let config = config(service)?
        .ok_or_else(|| BackendError::new("sync_not_configured", "请先保存同步配置"))?;
    versions::valid_version_path(&config, &version_path)?;
    if destination.as_deref().is_some_and(|path| {
        !Path::new(path)
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
    }) {
        return Err(BackendError::invalid(
            "invalid_sync_destination",
            "版本下载必须使用 .json 文件",
        ));
    }
    start_with_path(
        app,
        service,
        "restore".into(),
        destination,
        Some(version_path),
    )
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    id: &str,
) -> Result<SyncJobView> {
    let current = job(service, id)?;
    if ["done", "failed", "cancelled", "interrupted"].contains(&current.status.as_str()) {
        return Ok(current);
    }
    let flag = service
        .active()
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "同步任务状态不可用"))?
        .get(id)
        .cloned()
        .ok_or_else(|| BackendError::new("sync_not_active", "该同步任务未在运行"))?;
    flag.store(true, Ordering::Release);
    let store = service.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE sync_jobs SET status = 'cancelling', updated_at = ?1 WHERE id = ?2",
        params![now()?, id],
    )?;
    let event = Store::event(&tx, "sync.cancel_requested", json!({"jobId": id}))?;
    tx.commit()?;
    emit(app, &event);
    job(service, id)
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, service: &SyncService) -> Result<()> {
    let store = service.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id, status FROM sync_jobs WHERE status IN ('running', 'cancelling')")?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, status) in rows {
        // 用户已请求取消的任务重启后落 cancelled，与方案/Agent 等域语义一致；
        // 只有 running 才是"被重启打断"（07#5）
        let (next, code, message, kind) = if status == "cancelling" {
            (
                "cancelled",
                Option::<String>::None,
                "取消请求已保存，重启后按已取消收尾".to_string(),
                "sync.cancelled",
            )
        } else {
            (
                "interrupted",
                Some("sync_interrupted".to_string()),
                "应用重启后未自动重试远端同步，请核对远端后手动执行".to_string(),
                "sync.interrupted",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE sync_jobs SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4, finished_at = ?4 WHERE id = ?5", params![next, code, message, now()?, id])?;
        let event = Store::event(&tx, kind, json!({"jobId": id}))?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn stop(service: &SyncService) -> Result<()> {
    scheduler::stop_scheduler();
    let active_jobs = service.active();
    let flags = active_jobs
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "同步任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in &flags {
        flag.store(true, Ordering::Release);
    }
    let deadline = Instant::now() + Duration::from_secs(50);
    while Instant::now() < deadline {
        if active_jobs
            .lock()
            .map(|active| active.is_empty())
            .unwrap_or(false)
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new(
        "sync_active",
        "同步任务尚未停止，请稍后重试",
    ))
}

#[cfg(test)]
fn defer(value: bool) {
    DEFER
        .get_or_init(|| Arc::new(AtomicBool::new(false)))
        .store(value, Ordering::Release);
}
