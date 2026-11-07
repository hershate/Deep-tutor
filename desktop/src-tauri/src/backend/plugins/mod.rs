pub mod commands;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::json;
#[cfg(test)]
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

static TEMP_ID: AtomicU64 = AtomicU64::new(0);

pub struct PluginState {
    database_path: PathBuf,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
}

impl PluginState {
    pub fn new(path: &Path) -> Result<Self> {
        let state = Self {
            database_path: path.to_path_buf(),
            #[cfg(test)]
            _guard: None,
        };
        fs::create_dir_all(state.root())?;
        Ok(state)
    }

    fn root(&self) -> PathBuf {
        super::security::workspace_path(&self.database_path)
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("plugins")
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        let mut state = Self::new(&guard.path().join("app.db"))?;
        state._guard = Some(guard);
        Ok(state)
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PluginManifest {
    id: String,
    name: String,
    version: String,
    description: String,
    kind: String,
    #[serde(default)]
    subscriptions: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginView {
    pub id: String,
    pub name: String,
    pub version: String,
    pub description: String,
    pub kind: String,
    pub enabled: bool,
    pub generation: u32,
    pub subscriptions: Vec<String>,
    pub event_count: u32,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginEventView {
    pub plugin_id: String,
    pub plugin_name: String,
    pub event_id: i64,
    pub kind: String,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AssetInfo {
    pub id: String,
    pub title: String,
    pub file_name: String,
    pub kind: String,
    pub bytes: usize,
    pub sha256: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AssetContent {
    pub id: String,
    pub title: String,
    pub file_name: String,
    pub content: String,
    pub sha256: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PluginOverview {
    pub plugins: Vec<PluginView>,
    pub events: Vec<PluginEventView>,
    pub assets: Vec<AssetInfo>,
    pub enabled_count: u32,
    pub event_count: u32,
}

const OPERATIONS_ASSET: &str = "# Deep Tutor 原生应用运维\n\n- 交付物是 Tauri 2 的 Windows NSIS 安装包。\n- 应用数据使用本机 SQLite，开启 WAL，结构化迁移在启动时执行。\n- 原生事件先写入 outbox，再发送到应用界面；事件编号用于续接。\n- 模型密钥由系统密钥库保管，不写入插件清单、备份或日志。\n- 插件使用声明式 JSON 清单，不执行 Python 或任意脚本。\n- 重置与卸载应保留可核对的错误状态，不把文件清理失败伪报为成功。\n";

fn assets() -> Vec<(&'static str, &'static str, &'static str, &'static str)> {
    vec![
        (
            "deep-tutor-intent",
            "意图澄清（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-intent/SKILL.md"),
        ),
        (
            "deep-tutor-knowledge-map",
            "知识图谱（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-knowledge-map/SKILL.md"),
        ),
        (
            "deep-tutor-stages",
            "阶段设计（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-stages/SKILL.md"),
        ),
        (
            "deep-tutor-validate",
            "五维校验（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-validate/SKILL.md"),
        ),
        (
            "deep-tutor-quiz",
            "测验出题（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-quiz/SKILL.md"),
        ),
        (
            "deep-tutor-resources",
            "资源筛选（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/deep-tutor-resources/SKILL.md"),
        ),
        (
            "ppt-slide-builder",
            "幻灯片模板（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/ppt-slide-builder/SKILL.md"),
        ),
        (
            "self-study-guide",
            "学习方法论（历史 Skill 资产）",
            "legacy-skill",
            include_str!("../../../assets/skills/self-study-guide/SKILL.md"),
        ),
        (
            "native-operations",
            "原生应用运维说明",
            "operations",
            OPERATIONS_ASSET,
        ),
    ]
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS plugins (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            description TEXT NOT NULL,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            generation INTEGER NOT NULL DEFAULT 0,
            manifest_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plugin_subscriptions (
            plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
            pattern TEXT NOT NULL,
            PRIMARY KEY (plugin_id, pattern)
        );
        CREATE TABLE IF NOT EXISTS plugin_deliveries (
            plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plugin_id, event_id)
        );
        CREATE TABLE IF NOT EXISTS plugin_reloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
            generation INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL
        );",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    for table in [
        "plugin_deliveries",
        "plugin_reloads",
        "plugin_subscriptions",
        "plugins",
    ] {
        conn.execute(&format!("DELETE FROM {table}"), [])?;
    }
    Ok(())
}

fn validate_id(id: &str) -> Result<()> {
    if id.is_empty()
        || id.len() > 64
        || id.starts_with(['.', '-', '_'])
        || id.ends_with(['.', '-', '_'])
        || !id.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'-' | b'_')
        })
    {
        return Err(BackendError::invalid(
            "invalid_plugin_id",
            "插件 ID 只能包含小写字母、数字、点、短横线与下划线，且不能以符号开头或结尾",
        ));
    }
    Ok(())
}

fn validate(manifest: &PluginManifest) -> Result<()> {
    validate_id(&manifest.id)?;
    if manifest.name.trim().is_empty()
        || manifest.name.len() > 100
        || manifest.description.len() > 2000
        || manifest.kind != "events"
    {
        return Err(BackendError::invalid(
            "invalid_plugin_manifest",
            "当前仅支持声明式事件插件，名称与说明必须符合长度限制",
        ));
    }
    let valid_version = manifest.version.len() <= 30
        && manifest.version.split('.').count() == 3
        && manifest
            .version
            .split('.')
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()));
    if !valid_version {
        return Err(BackendError::invalid(
            "invalid_plugin_version",
            "插件版本须为 major.minor.patch 数字形式",
        ));
    }
    if manifest.subscriptions.len() > 32 {
        return Err(BackendError::invalid(
            "invalid_plugin_subscriptions",
            "单个插件最多订阅 32 个事件模式",
        ));
    }
    let mut seen = std::collections::HashSet::new();
    for pattern in &manifest.subscriptions {
        let valid = if pattern == "*" {
            true
        } else if let Some(prefix) = pattern.strip_suffix(".*") {
            [
                "plan",
                "practice",
                "review",
                "material",
                "agent",
                "knowledge",
                "community",
                "provider",
                "plugin",
                "app",
                "share",
                "content",
                "backup",
                "sync",
                "maintenance",
            ]
            .contains(&prefix)
        } else {
            false
        };
        if !valid || pattern.len() > 100 || !seen.insert(pattern) {
            return Err(BackendError::invalid(
                "invalid_plugin_subscriptions",
                "事件订阅必须是允许的原生命名空间或 *，且不能重复",
            ));
        }
    }
    Ok(())
}

fn parse_manifest(content: &str) -> Result<PluginManifest> {
    if content.is_empty() || content.len() > 200_000 {
        return Err(BackendError::invalid(
            "invalid_plugin_manifest",
            "插件清单须为 1 到 200000 字节",
        ));
    }
    let manifest: PluginManifest = serde_json::from_str(content).map_err(|_| {
        BackendError::invalid("invalid_plugin_manifest", "插件清单不是受支持的 JSON 结构")
    })?;
    validate(&manifest)?;
    Ok(manifest)
}

pub(crate) fn validate_manifest_content(content: &str) -> Result<()> {
    parse_manifest(content).map(|_| ())
}

fn manifest_path(state: &PluginState, id: &str) -> Result<PathBuf> {
    validate_id(id)?;
    Ok(state.root().join(id).join("manifest.json"))
}

fn write_manifest(state: &PluginState, id: &str, content: &str) -> Result<PathBuf> {
    let path = manifest_path(state, id)?;
    let directory = path
        .parent()
        .ok_or_else(|| BackendError::new("invalid_plugin_path", "插件路径无效"))?;
    fs::create_dir_all(directory)?;
    let temporary = directory.join(format!(
        ".manifest-{}.tmp",
        TEMP_ID.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    // 14#15：写坏即删 tmp，不留半截文件被孤儿扫描收走
    if let Err(error) =
        std::io::Write::write_all(&mut file, content.as_bytes()).and_then(|()| file.sync_all())
    {
        drop(file);
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    drop(file);
    if let Err(error) = fs::rename(&temporary, &path) {
        let _ = fs::remove_file(temporary);
        return Err(error.into());
    }
    Ok(path)
}

fn subscriptions(conn: &Connection, id: &str) -> Result<Vec<String>> {
    let mut statement = conn.prepare(
        "SELECT pattern FROM plugin_subscriptions WHERE plugin_id = ?1 ORDER BY pattern",
    )?;
    let patterns = statement
        .query_map(params![id], |row| row.get(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    Ok(patterns)
}

fn plugin_row(conn: &Connection, id: &str) -> Result<PluginView> {
    let (name, version, description, kind, enabled, generation, created_at, updated_at): (String, String, String, String, bool, u32, String, String) = conn.query_row(
        "SELECT name, version, description, kind, enabled, generation, created_at, updated_at FROM plugins WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?)),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "插件不存在"))?;
    let event_count = conn.query_row(
        "SELECT COUNT(*) FROM plugin_deliveries WHERE plugin_id = ?1",
        params![id],
        |row| row.get(0),
    )?;
    Ok(PluginView {
        id: id.into(),
        name,
        version,
        description,
        kind,
        enabled,
        generation,
        subscriptions: subscriptions(conn, id)?,
        event_count,
        created_at,
        updated_at,
    })
}

pub fn list(state: &PluginState) -> Result<Vec<PluginView>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT id FROM plugins ORDER BY id")?;
    let ids = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    ids.into_iter()
        .map(|id| plugin_row(&store.conn, &id))
        .collect()
}

pub fn install<R: Runtime>(
    app: &AppHandle<R>,
    state: &PluginState,
    content: String,
) -> Result<PluginView> {
    let manifest = parse_manifest(&content)?;
    let store = state.store()?;
    // 14#15：存在性检查与 INSERT、事件写入同事务，消除“事务外双查双插”
    // 竞态；清单在提交前落盘，失败整体回滚，清理只针对本次写入
    let mut wrote_manifest = false;
    let outcome = (|| -> Result<PluginView> {
        let tx = store.conn.unchecked_transaction()?;
        let exists: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM plugins WHERE id = ?1)",
            params![manifest.id],
            |row| row.get(0),
        )?;
        if exists {
            return Err(BackendError::new(
                "plugin_already_installed",
                "插件已安装；更新磁盘清单后可重载，旧配置不会被覆盖",
            ));
        }
        let timestamp = now()?;
        tx.execute("INSERT INTO plugins (id, name, version, description, kind, manifest_json, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7)", params![manifest.id, manifest.name, manifest.version, manifest.description, manifest.kind, content, timestamp])?;
        for pattern in &manifest.subscriptions {
            tx.execute(
                "INSERT INTO plugin_subscriptions (plugin_id, pattern) VALUES (?1, ?2)",
                params![manifest.id, pattern],
            )?;
        }
        write_manifest(state, &manifest.id, &content)?;
        wrote_manifest = true;
        let event = Store::event(
            &tx,
            "plugin.installed",
            json!({"pluginId": manifest.id, "version": manifest.version}),
        )?;
        tx.commit()?;
        emit(app, &event);
        plugin_row(&store.conn, &manifest.id)
    })();
    if outcome.is_err() {
        // 14#15：仅当“本次确实落过盘且库里仍无此插件”才清理——并发同 id
        // 安装的胜者拥有清单，败者不得误删；目录只删空目录
        if wrote_manifest {
            let installed_now: bool = store
                .conn
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM plugins WHERE id = ?1)",
                    params![manifest.id],
                    |row| row.get(0),
                )
                .unwrap_or(false);
            if !installed_now {
                if let Ok(path) = manifest_path(state, &manifest.id) {
                    let _ = fs::remove_file(&path);
                    if let Some(directory) = path.parent() {
                        let _ = fs::remove_dir(directory);
                    }
                }
            }
        } else if let Ok(path) = manifest_path(state, &manifest.id) {
            // 清单未落成：write_manifest 可能已建目录或遗留 .manifest-N.tmp
            if let Some(directory) = path.parent() {
                if let Ok(entries) = fs::read_dir(directory) {
                    for entry in entries.flatten() {
                        let name = entry.file_name().to_string_lossy().into_owned();
                        if name.starts_with(".manifest-") && name.ends_with(".tmp") {
                            let _ = fs::remove_file(entry.path());
                        }
                    }
                }
                let _ = fs::remove_dir(directory);
            }
        }
    }
    outcome
}

pub fn set_enabled<R: Runtime>(
    app: &AppHandle<R>,
    state: &PluginState,
    id: String,
    enabled: bool,
) -> Result<PluginView> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE plugins SET enabled = ?1, updated_at = ?2 WHERE id = ?3",
        params![enabled, now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "插件不存在"));
    }
    let event = Store::event(
        &tx,
        if enabled {
            "plugin.enabled"
        } else {
            "plugin.disabled"
        },
        json!({"pluginId": id, "enabled": enabled}),
    )?;
    tx.commit()?;
    emit(app, &event);
    plugin_row(&store.conn, &id)
}

fn record_reload<R: Runtime>(
    app: &AppHandle<R>,
    state: &PluginState,
    id: &str,
    status: &str,
    error: Option<&BackendError>,
) -> Result<()> {
    let store = state.store()?;
    let generation: u32 = store
        .conn
        .query_row(
            "SELECT generation FROM plugins WHERE id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "插件不存在"))?;
    record_reload_with(app, state, id, status, error, generation)
}

/// 14#17：generation 由调用方传入（成功路径来自本次 UPDATE 写入值）
fn record_reload_with<R: Runtime>(
    app: &AppHandle<R>,
    state: &PluginState,
    id: &str,
    status: &str,
    error: Option<&BackendError>,
    generation: u32,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO plugin_reloads (plugin_id, generation, status, error_code, error_message, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)", params![id, generation, status, error.map(|error| &error.code), error.map(|error| &error.message), now()?])?;
    let event = Store::event(
        &tx,
        if status == "done" {
            "plugin.reloaded"
        } else {
            "plugin.reload_failed"
        },
        json!({"pluginId": id, "generation": generation, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn reload<R: Runtime>(
    app: &AppHandle<R>,
    state: &PluginState,
    id: String,
) -> Result<PluginView> {
    let path = manifest_path(state, &id)?;
    let result = fs::read_to_string(path)
        .map_err(|_| {
            BackendError::new(
                "plugin_manifest_missing",
                "插件清单文件不存在，未改变已保存的配置",
            )
        })
        .and_then(|content| {
            parse_manifest(&content).and_then(|manifest| {
                if manifest.id == id {
                    Ok(manifest)
                } else {
                    Err(BackendError::new(
                        "plugin_id_changed",
                        "清单 ID 不可在重载时改变",
                    ))
                }
            })
        });
    let manifest = match result {
        Ok(manifest) => manifest,
        Err(error) => {
            record_reload(app, state, &id, "failed", Some(&error))?;
            return Err(error);
        }
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE plugins SET name = ?1, version = ?2, description = ?3, kind = ?4, manifest_json = ?5, generation = generation + 1, updated_at = ?6 WHERE id = ?7", params![manifest.name, manifest.version, manifest.description, manifest.kind, serde_json::to_string(&manifest)?, now()?, id])?;
    tx.execute(
        "DELETE FROM plugin_subscriptions WHERE plugin_id = ?1",
        params![id],
    )?;
    for pattern in &manifest.subscriptions {
        tx.execute(
            "INSERT INTO plugin_subscriptions (plugin_id, pattern) VALUES (?1, ?2)",
            params![id, pattern],
        )?;
    }
    // 14#17：代数取本次 UPDATE 写入值，不用新连接事后重读（并发 reload
    // 下会把他人递增后的代数记进审计表）
    let generation: u32 = tx.query_row(
        "SELECT generation FROM plugins WHERE id = ?1",
        params![id],
        |row| row.get(0),
    )?;
    tx.commit()?;
    // 记录失败不再把已生效的 reload 报为失败（UI 会误导重试）
    if let Err(error) = record_reload_with(app, state, &id, "done", None, generation) {
        eprintln!("plugin reload audit record failed: {}", error.message);
    }
    plugin_row(&store.conn, &id)
}

pub fn uninstall<R: Runtime>(app: &AppHandle<R>, state: &PluginState, id: String) -> Result<()> {
    let path = manifest_path(state, &id)?;
    let store = state.store()?;
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM plugins WHERE id = ?1)",
        params![id],
        |row| row.get(0),
    )?;
    // 14#16：先删目录再提交 DB 删除——目录删除失败（Windows 文件占用）时
    // 保持两边一致可重试；原顺序会“已卸载却报错”，重试即 not_found 且
    // 磁盘残留无入口清理。DB 无记录但目录存在时顺手清掉残留目录。
    if let Some(directory) = path.parent() {
        if directory.exists() {
            fs::remove_dir_all(directory)?;
        }
    }
    if !exists {
        return Err(BackendError::new("not_found", "插件不存在"));
    }
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("DELETE FROM plugins WHERE id = ?1", params![id])?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "插件不存在"));
    }
    let event = Store::event(&tx, "plugin.uninstalled", json!({"pluginId": id}))?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn reset_files(state: &PluginState) -> Result<()> {
    if state.root().exists() {
        fs::remove_dir_all(&state.root())?;
    }
    Ok(())
}

fn matches(pattern: &str, kind: &str) -> bool {
    pattern == "*"
        || pattern.strip_suffix(".*").is_some_and(|prefix| {
            kind.strip_prefix(prefix)
                .is_some_and(|suffix| suffix.starts_with('.'))
        })
}

pub fn dispatch(conn: &Connection, event_id: i64, kind: &str) -> Result<()> {
    let mut statement = conn.prepare("SELECT plugin_id, pattern FROM plugin_subscriptions JOIN plugins ON plugins.id = plugin_subscriptions.plugin_id WHERE plugins.enabled = 1")?;
    let subscriptions = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (plugin_id, pattern) in subscriptions {
        if matches(&pattern, kind) {
            conn.execute("INSERT INTO plugin_deliveries (plugin_id, event_id, created_at) VALUES (?1, ?2, ?3) ON CONFLICT(plugin_id, event_id) DO NOTHING", params![plugin_id, event_id, now()?])?;
        }
    }
    Ok(())
}

pub fn ping<R: Runtime>(app: &AppHandle<R>, state: &PluginState) -> Result<i64> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let event = Store::event(&tx, "plugin.ping", json!({"source": "native-command"}))?;
    tx.commit()?;
    emit(app, &event);
    Ok(event.id)
}

/// 14#18：before 游标分页（event_id < before），早于最新 100 条的
/// 历史投递仍有入口可查
pub fn events(
    state: &PluginState,
    plugin_id: Option<String>,
    before: Option<i64>,
) -> Result<Vec<PluginEventView>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT d.plugin_id, p.name, d.event_id, e.kind, d.created_at FROM plugin_deliveries d JOIN plugins p ON p.id = d.plugin_id JOIN events e ON e.id = d.event_id WHERE (?1 IS NULL OR d.plugin_id = ?1) AND (?2 IS NULL OR d.event_id < ?2) ORDER BY d.event_id DESC LIMIT 100")?;
    let rows = statement.query_map(params![plugin_id, before], |row| {
        Ok(PluginEventView {
            plugin_id: row.get(0)?,
            plugin_name: row.get(1)?,
            event_id: row.get(2)?,
            kind: row.get(3)?,
            created_at: row.get(4)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

fn asset_info(id: &str, title: &str, kind: &str, content: &str) -> AssetInfo {
    AssetInfo {
        id: id.into(),
        title: title.into(),
        file_name: format!("{id}.md"),
        kind: kind.into(),
        bytes: content.len(),
        sha256: format!("{:x}", Sha256::digest(content.as_bytes())),
    }
}

pub fn asset(state: &PluginState, id: &str) -> Result<AssetContent> {
    let _ = state;
    let (_, title, _, content) = assets()
        .into_iter()
        .find(|asset| asset.0 == id)
        .ok_or_else(|| BackendError::new("not_found", "开发者资产不存在"))?;
    Ok(AssetContent {
        id: id.into(),
        title: title.into(),
        file_name: format!("{id}.md"),
        content: content.into(),
        sha256: format!("{:x}", Sha256::digest(content.as_bytes())),
    })
}

pub fn overview(state: &PluginState) -> Result<PluginOverview> {
    let plugins = list(state)?;
    let enabled_count = plugins.iter().filter(|plugin| plugin.enabled).count() as u32;
    let event_count = plugins.iter().map(|plugin| plugin.event_count).sum();
    Ok(PluginOverview {
        plugins,
        events: events(state, None, None)?,
        assets: assets()
            .into_iter()
            .map(|(id, title, kind, content)| asset_info(id, title, kind, content))
            .collect(),
        enabled_count,
        event_count,
    })
}

#[cfg(test)]
mod tests;
