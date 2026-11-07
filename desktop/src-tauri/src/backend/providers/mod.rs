pub mod commands;
pub mod openai;
pub mod protocol;
pub mod routing;
pub(crate) mod sync_config;
pub mod tools;
pub mod usage;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use zeroize::Zeroizing;

const SERVICE: &str = "deep-tutor";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProviderProfile {
    pub id: String,
    pub name: String,
    pub preset: String,
    pub kind: String,
    pub protocol: String,
    pub base_url: String,
    pub models: Vec<String>,
    pub default_model: String,
    pub has_key: bool,
    pub health: String,
    pub latency_ms: Option<i64>,
    pub enabled: bool,
    pub note: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileInput {
    pub id: Option<String>,
    pub name: String,
    pub preset: Option<String>,
    pub kind: String,
    pub protocol: String,
    pub base_url: String,
    pub default_model: String,
    pub models: Vec<String>,
    pub enabled: Option<bool>,
    pub note: Option<String>,
    pub api_key: Option<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<tools::ToolCallRequest>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderTestResult {
    pub profile_id: String,
    pub kind: String,
    pub ok: bool,
    pub latency_ms: i64,
    pub models: Option<Vec<String>>,
    pub result: Option<openai::ChatResult>,
}

pub(crate) trait CredentialBackend: Send + Sync {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>>;
    fn set(&self, id: &str, value: &str) -> Result<()>;
    fn delete(&self, id: &str) -> Result<()>;
}

struct SystemCredentials;

fn credential_name(id: &str) -> String {
    format!(
        "provider-u{}-{id}",
        super::security::principal_id().unwrap_or(1)
    )
}

fn read_credential(name: &str) -> Result<Option<Zeroizing<String>>> {
    let entry = keyring::Entry::new(SERVICE, name)
        .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?;
    match entry.get_password() {
        Ok(value) => Ok(Some(Zeroizing::new(value))),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err(BackendError::new("credential_error", "无法读取系统密钥库")),
    }
}

impl CredentialBackend for SystemCredentials {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>> {
        if let Some(value) = read_credential(&credential_name(id))? {
            return Ok(Some(value));
        }
        if super::security::principal_id().unwrap_or(1) == 1 {
            return read_credential(&format!("provider-{id}"));
        }
        Ok(None)
    }

    fn set(&self, id: &str, value: &str) -> Result<()> {
        keyring::Entry::new(SERVICE, &credential_name(id))
            .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?
            .set_password(value)
            .map_err(|_| BackendError::new("credential_error", "无法写入系统密钥库"))
    }

    fn delete(&self, id: &str) -> Result<()> {
        let mut names = vec![credential_name(id)];
        if super::security::principal_id().unwrap_or(1) == 1 {
            names.push(format!("provider-{id}"));
        }
        for name in names {
            let entry = keyring::Entry::new(SERVICE, &name)
                .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?;
            match entry.delete_credential() {
                Ok(_) | Err(keyring::Error::NoEntry) => {}
                Err(_) => {
                    return Err(BackendError::new(
                        "credential_cleanup_failed",
                        "无法删除系统密钥库中的凭据",
                    ))
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
#[derive(Default)]
struct MemoryCredentials(Mutex<HashMap<String, Zeroizing<String>>>);

#[cfg(test)]
impl CredentialBackend for MemoryCredentials {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>> {
        Ok(self.0.lock().unwrap().get(id).cloned())
    }

    fn set(&self, id: &str, value: &str) -> Result<()> {
        self.0
            .lock()
            .unwrap()
            .insert(id.to_string(), Zeroizing::new(value.to_string()));
        Ok(())
    }

    fn delete(&self, id: &str) -> Result<()> {
        self.0.lock().unwrap().remove(id);
        Ok(())
    }
}

pub struct ProviderState {
    database_path: PathBuf,
    credentials: Arc<dyn CredentialBackend>,
    active_tests: Arc<Mutex<HashSet<String>>>,
    chat_cancels: Arc<Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>>,
    route_cancels: Arc<Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>>,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
}

impl ProviderState {
    pub fn new(database_path: &Path) -> Result<Self> {
        Ok(Self {
            database_path: database_path.to_path_buf(),
            credentials: Arc::new(SystemCredentials),
            active_tests: Arc::new(Mutex::new(HashSet::new())),
            chat_cancels: Arc::new(Mutex::new(HashMap::new())),
            route_cancels: Arc::new(Mutex::new(HashMap::new())),
            #[cfg(test)]
            _guard: None,
        })
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        Ok(Self {
            database_path: guard.path().join("app.db"),
            credentials: Arc::new(MemoryCredentials::default()),
            active_tests: Arc::new(Mutex::new(HashSet::new())),
            chat_cancels: Arc::new(Mutex::new(HashMap::new())),
            route_cancels: Arc::new(Mutex::new(HashMap::new())),
            _guard: Some(guard),
        })
    }

    #[cfg(test)]
    pub fn for_tests_at(database_path: &Path) -> Self {
        Self {
            database_path: database_path.to_path_buf(),
            credentials: Arc::new(MemoryCredentials::default()),
            active_tests: Arc::new(Mutex::new(HashSet::new())),
            chat_cancels: Arc::new(Mutex::new(HashMap::new())),
            route_cancels: Arc::new(Mutex::new(HashMap::new())),
            _guard: None,
        }
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            credentials: Arc::clone(&self.credentials),
            active_tests: Arc::clone(&self.active_tests),
            chat_cancels: Arc::clone(&self.chat_cancels),
            route_cancels: Arc::clone(&self.route_cancels),
            #[cfg(test)]
            _guard: None,
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }

    pub(crate) fn credential_backend(&self) -> Arc<dyn CredentialBackend> {
        Arc::clone(&self.credentials)
    }
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS provider_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            preset TEXT NOT NULL,
            kind TEXT NOT NULL,
            protocol TEXT NOT NULL,
            base_url TEXT NOT NULL,
            default_model TEXT NOT NULL,
            models_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            health TEXT NOT NULL DEFAULT 'unknown',
            latency_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_requests (
            request_id TEXT PRIMARY KEY,
            provider_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            usage_json TEXT,
            finish_reason TEXT,
            error_code TEXT,
            error_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 1,
            latency_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reasoning TEXT NOT NULL DEFAULT '',
            tool_calls_json TEXT
        );",
    )?;
    let mut statement = conn.prepare("PRAGMA table_info(provider_requests)")?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if !columns.iter().any(|column| column == "reasoning") {
        conn.execute(
            "ALTER TABLE provider_requests ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''",
            [],
        )?;
    }
    if !columns.iter().any(|column| column == "tool_calls_json") {
        conn.execute(
            "ALTER TABLE provider_requests ADD COLUMN tool_calls_json TEXT",
            [],
        )?;
    }
    routing::migrate(conn)?;
    usage::migrate(conn)?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    routing::clear_all(conn)?;
    usage::clear_all(conn)?;
    conn.execute("DELETE FROM provider_requests", [])?;
    conn.execute("DELETE FROM provider_profiles", [])?;
    conn.execute("DELETE FROM provider_meta", [])?;
    Ok(())
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

fn numeric_id(id: &str) -> Result<i64> {
    let id = id.strip_prefix('p').unwrap_or(id);
    let id = id
        .parse::<i64>()
        .map_err(|_| BackendError::invalid("invalid_provider_id", "供应商档案标识无效"))?;
    if id <= 0 {
        return Err(BackendError::invalid(
            "invalid_provider_id",
            "供应商档案标识无效",
        ));
    }
    Ok(id)
}

fn profile(row: &Row<'_>, state: &ProviderState) -> Result<ProviderProfile> {
    let id = format!("p{}", row.get::<_, i64>(0)?);
    Ok(ProviderProfile {
        has_key: state.credentials.get(&id)?.is_some(),
        id,
        name: row.get(1)?,
        preset: row.get(2)?,
        kind: row.get(3)?,
        protocol: row.get(4)?,
        base_url: row.get(5)?,
        default_model: row.get(6)?,
        models: serde_json::from_str(&row.get::<_, String>(7)?)?,
        enabled: row.get(8)?,
        note: row.get(9)?,
        health: row.get(10)?,
        latency_ms: row.get(11)?,
    })
}

const PROFILE_COLUMNS: &str = "id, name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, health, latency_ms";

pub(crate) fn get(state: &ProviderState, id: &str) -> Result<ProviderProfile> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(&format!(
        "SELECT {PROFILE_COLUMNS} FROM provider_profiles WHERE id = ?1"
    ))?;
    let mut rows = statement.query(params![numeric_id(id)?])?;
    let row = rows
        .next()?
        .ok_or_else(|| BackendError::new("not_found", "供应商档案不存在"))?;
    profile(row, state)
}

pub fn list(state: &ProviderState) -> Result<Vec<ProviderProfile>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(&format!(
        "SELECT {PROFILE_COLUMNS} FROM provider_profiles ORDER BY id"
    ))?;
    let mut rows = statement.query([])?;
    let mut profiles = Vec::new();
    while let Some(row) = rows.next()? {
        profiles.push(profile(row, state)?);
    }
    Ok(profiles)
}

pub(crate) fn validate_input(input: &ProfileInput) -> Result<ProfileInput> {
    if input.name.trim().is_empty() || input.name.len() > 100 {
        return Err(BackendError::invalid(
            "invalid_provider_name",
            "档案名称不能为空且不能超过 100 字符",
        ));
    }
    if !["官方", "兼容服务", "本地模型"].contains(&input.kind.as_str()) {
        return Err(BackendError::invalid(
            "invalid_provider_kind",
            "供应商类型无效",
        ));
    }
    if ![
        "OpenAI Chat",
        "OpenAI Responses",
        "Anthropic Messages",
        "Gemini Native",
    ]
    .contains(&input.protocol.as_str())
    {
        return Err(BackendError::invalid(
            "unsupported_protocol",
            "上游协议不受支持",
        ));
    }
    if input.default_model.trim().is_empty() || input.default_model.len() > 200 {
        return Err(BackendError::invalid(
            "invalid_model",
            "默认模型不能为空且不能超过 200 字符",
        ));
    }
    if input.models.len() > 200
        || input
            .models
            .iter()
            .any(|model| model.trim().is_empty() || model.len() > 200)
    {
        return Err(BackendError::invalid(
            "invalid_model",
            "模型列表为空项、过长或超过 200 项",
        ));
    }
    if input.note.as_deref().is_some_and(|note| note.len() > 1000) {
        return Err(BackendError::invalid(
            "invalid_note",
            "备注不能超过 1000 字符",
        ));
    }
    if input.api_key.as_deref().is_some_and(|key| key.len() > 4096) {
        return Err(BackendError::invalid(
            "invalid_credential",
            "API Key 不能超过 4096 字节",
        ));
    }
    let _ = openai::endpoint(&input.base_url, &input.kind, "models")?;
    Ok(ProfileInput {
        id: input.id.clone(),
        name: input.name.trim().to_string(),
        preset: Some(input.preset.clone().unwrap_or_else(|| "custom".into())),
        kind: input.kind.clone(),
        protocol: input.protocol.clone(),
        base_url: input.base_url.trim().to_string(),
        default_model: input.default_model.trim().to_string(),
        models: input.models.clone(),
        enabled: input.enabled,
        note: input.note.clone(),
        api_key: input.api_key.clone(),
    })
}

pub fn upsert<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    input: ProfileInput,
) -> Result<ProviderProfile> {
    let input = validate_input(&input)?;
    let store = state.store()?;
    let id = input.id.as_deref().map(numeric_id).transpose()?;
    let previous = id.map(|id| format!("p{id}"));
    if input.enabled == Some(false)
        && previous.is_some()
        && active(state)?.as_deref() == previous.as_deref()
    {
        return Err(BackendError::new(
            "provider_in_use",
            "当前供应商须先切换才能停用",
        ));
    }
    if input.enabled == Some(false) {
        if let Some(id) = previous.as_deref() {
            if routing::provider_in_use(state, id)? {
                return Err(BackendError::new(
                    "provider_in_use",
                    "请先移除任务路由或聚合中的供应商引用，再停用档案",
                ));
            }
        }
    }
    let previous_key = previous
        .as_deref()
        .map(|id| state.credentials.get(id))
        .transpose()?
        .flatten();
    let mut models = input.models.clone();
    if !models.contains(&input.default_model) {
        models.push(input.default_model.clone());
    }
    let tx = store.conn.unchecked_transaction()?;
    let id = if let Some(id) = id {
        let changed = tx.execute(
            "UPDATE provider_profiles SET name = ?1, preset = ?2, kind = ?3, protocol = ?4, base_url = ?5, default_model = ?6, models_json = ?7, enabled = ?8, note = ?9, health = 'unknown', latency_ms = NULL, updated_at = ?10 WHERE id = ?11",
            params![input.name, input.preset, input.kind, input.protocol, input.base_url, input.default_model, serde_json::to_string(&models)?, input.enabled.unwrap_or(true), input.note, now()?, id],
        )?;
        if changed == 0 {
            return Err(BackendError::new("not_found", "供应商档案不存在"));
        }
        id
    } else {
        tx.execute(
            "INSERT INTO provider_profiles (name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)",
            params![input.name, input.preset, input.kind, input.protocol, input.base_url, input.default_model, serde_json::to_string(&models)?, input.enabled.unwrap_or(true), input.note, now()?],
        )?;
        tx.last_insert_rowid()
    };
    let id_string = format!("p{id}");
    if let Some(key) = input.api_key.as_deref() {
        let result = if key.is_empty() {
            state.credentials.delete(&id_string)
        } else {
            state.credentials.set(&id_string, key)
        };
        if let Err(error) = result {
            return Err(error);
        }
    }
    let result = (|| -> Result<_> {
        let has_key = state.credentials.get(&id_string)?.is_some();
        let event = Store::event(
            &tx,
            "provider.profile_saved",
            json!({"providerId": id_string, "hasKey": has_key}),
        )?;
        tx.commit()?;
        Ok(event)
    })();
    let event = match result {
        Ok(event) => event,
        Err(error) => {
            if input.api_key.is_some() {
                restore_credential(state, &id_string, previous_key)?;
            }
            return Err(error);
        }
    };
    emit(app, &event);
    get(state, &id_string)
}

fn restore_credential(
    state: &ProviderState,
    id: &str,
    previous: Option<Zeroizing<String>>,
) -> Result<()> {
    let result = match previous {
        Some(key) => state.credentials.set(id, &key),
        None => state.credentials.delete(id),
    };
    result.map_err(|_| {
        BackendError::new(
            "credential_cleanup_failed",
            "档案保存未完成，且无法恢复系统密钥库中的原凭据，请检查系统密钥库",
        )
    })
}

pub fn active(state: &ProviderState) -> Result<Option<String>> {
    let store = state.store()?;
    store
        .conn
        .query_row(
            "SELECT value FROM provider_meta WHERE key = 'active_provider'",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(Into::into)
}

pub fn set_active<R: Runtime>(app: &AppHandle<R>, state: &ProviderState, id: &str) -> Result<()> {
    let profile = get(state, id)?;
    if !profile.enabled {
        return Err(BackendError::new(
            "provider_disabled",
            "已停用的供应商不能作为当前供应商",
        ));
    }
    let id = profile.id;
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_meta (key, value) VALUES ('active_provider', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![id],
    )?;
    let event = Store::event(&tx, "provider.active_changed", json!({"providerId": id}))?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn delete<R: Runtime>(app: &AppHandle<R>, state: &ProviderState, id: &str) -> Result<()> {
    let id = format!("p{}", numeric_id(id)?);
    if active(state)?.as_deref() == Some(id.as_str()) {
        return Err(BackendError::new(
            "provider_in_use",
            "当前供应商正在使用，请先切换到其他档案",
        ));
    }
    if routing::provider_in_use(state, &id)? {
        return Err(BackendError::new(
            "provider_in_use",
            "请先移除任务路由或聚合中的供应商引用，再删除档案",
        ));
    }
    let previous_key = state.credentials.get(&id)?;
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "DELETE FROM provider_profiles WHERE id = ?1",
        params![numeric_id(&id)?],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "供应商档案不存在"));
    }
    state.credentials.delete(&id)?;
    let result = (|| -> Result<_> {
        let event = Store::event(&tx, "provider.deleted", json!({"providerId": id}))?;
        tx.commit()?;
        Ok(event)
    })();
    match result {
        Ok(event) => {
            emit(app, &event);
            Ok(())
        }
        Err(error) => {
            restore_credential(state, &id, previous_key)?;
            Err(error)
        }
    }
}

pub fn export_config(state: &ProviderState) -> Result<Value> {
    Ok(json!({
        "version": 1,
        "activeProvider": active(state)?,
        "routing": routing::export(state)?,
        "providers": list(state)?,
        "notice": "API keys are never exported."
    }))
}

pub fn deep_link<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    link: &str,
) -> Result<ProviderProfile> {
    let url = reqwest::Url::parse(link)
        .map_err(|_| BackendError::invalid("invalid_deep_link", "Deep Link 格式无效"))?;
    if url.scheme() != "deeptutor" || url.host_str() != Some("provider") {
        return Err(BackendError::invalid(
            "invalid_deep_link",
            "Deep Link 必须使用 deeptutor://provider",
        ));
    }
    let mut name = None;
    let mut base_url = None;
    let mut protocol = None;
    let mut model = None;
    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "name" => name = Some(value.into_owned()),
            "baseUrl" => base_url = Some(value.into_owned()),
            "protocol" => protocol = Some(value.into_owned()),
            "model" => model = Some(value.into_owned()),
            _ => {}
        }
    }
    let model = model.unwrap_or_else(|| "custom-chat-model".into());
    let base_url = base_url
        .ok_or_else(|| BackendError::invalid("invalid_deep_link", "Deep Link 缺少 baseUrl"))?;
    let kind = if openai::endpoint(&base_url, "本地模型", "models").is_ok() {
        "本地模型"
    } else {
        "兼容服务"
    };
    upsert(
        app,
        state,
        ProfileInput {
            id: None,
            name: name
                .ok_or_else(|| BackendError::invalid("invalid_deep_link", "Deep Link 缺少 name"))?,
            preset: Some("custom".into()),
            kind: kind.into(),
            protocol: protocol.unwrap_or_else(|| "OpenAI Chat".into()),
            base_url,
            default_model: model.clone(),
            models: vec![model],
            enabled: Some(true),
            note: None,
            api_key: None,
        },
    )
}

struct TestGuard {
    state: Arc<Mutex<HashSet<String>>>,
    key: String,
}

impl Drop for TestGuard {
    fn drop(&mut self) {
        if let Ok(mut tests) = self.state.lock() {
            tests.remove(&self.key);
        }
    }
}

fn begin_test(state: &ProviderState, id: &str) -> Result<TestGuard> {
    let mut tests = state
        .active_tests
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "供应商测试状态不可用"))?;
    let key = id.to_string();
    if !tests.insert(key.clone()) {
        return Err(BackendError::new(
            "test_active",
            "该供应商正在检测中，请勿重复执行",
        ));
    }
    Ok(TestGuard {
        state: Arc::clone(&state.active_tests),
        key,
    })
}

pub fn test<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    id: &str,
    real_request: bool,
) -> Result<ProviderTestResult> {
    let _guard = begin_test(state, id)?;
    let profile = get(state, id)?;
    let key = state.credentials.get(id)?;
    let started = Instant::now();
    let result = if real_request {
        protocol::chat_once(
            &profile,
            key,
            &[ChatMessage {
                role: "user".into(),
                content: "Reply with one word.".into(),
                ..Default::default()
            }],
            1,
        )
        .map(|result| ProviderTestResult {
            profile_id: id.into(),
            kind: "chat".into(),
            ok: true,
            latency_ms: started.elapsed().as_millis() as i64,
            models: None,
            result: Some(result),
        })
    } else {
        protocol::list_models(&profile, key).map(|models| ProviderTestResult {
            profile_id: id.into(),
            kind: "models".into(),
            ok: true,
            latency_ms: started.elapsed().as_millis() as i64,
            models: Some(models),
            result: None,
        })
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let latency = started.elapsed().as_millis() as i64;
    let health = if result.is_ok() { "healthy" } else { "error" };
    tx.execute(
        "UPDATE provider_profiles SET health = ?1, latency_ms = ?2, updated_at = ?3 WHERE id = ?4",
        params![health, latency, now()?, numeric_id(id)?],
    )?;
    let event = Store::event(
        &tx,
        "provider.tested",
        json!({"providerId": id, "ok": result.is_ok(), "latencyMs": latency, "kind": if real_request { "chat" } else { "models" }, "error": result.as_ref().err() }),
    )?;
    tx.commit()?;
    emit(app, &event);
    result
}

pub fn credential_ids(state: &ProviderState) -> Result<Vec<String>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT id FROM provider_profiles")?;
    let rows = statement.query_map([], |row| row.get::<_, i64>(0))?;
    let mut ids = Vec::new();
    for row in rows {
        ids.push(format!("p{}", row?));
    }
    ids.extend(["sync-webdav", "sync-s3-access", "sync-s3-secret"].map(str::to_string));
    Ok(ids)
}

pub fn purge_credentials(state: &ProviderState, ids: &[String]) -> Result<()> {
    let mut failed = false;
    for id in ids {
        if state.credentials.delete(id).is_err() {
            failed = true;
        }
    }
    if failed {
        return Err(BackendError::new(
            "credential_cleanup_failed",
            "部分凭据未能从系统密钥库删除",
        ));
    }
    Ok(())
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderRequest {
    pub request_id: String,
    pub provider_id: String,
    pub model: String,
    pub status: String,
    pub output: String,
    pub reasoning: String,
    pub tool_calls: Vec<tools::ToolCall>,
    pub usage: Option<openai::TokenUsage>,
    pub finish_reason: Option<String>,
    pub error: Option<BackendError>,
    pub attempts: u8,
    pub latency_ms: Option<i64>,
    pub created_at: String,
    pub updated_at: String,
}

struct ChatGuard {
    cancels: Arc<Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>>,
    request_id: String,
}

impl Drop for ChatGuard {
    fn drop(&mut self) {
        if let Ok(mut cancels) = self.cancels.lock() {
            cancels.remove(&self.request_id);
        }
    }
}

fn validate_request_id(request_id: &str) -> Result<()> {
    if request_id.is_empty()
        || request_id.len() > 64
        || !request_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(BackendError::invalid(
            "invalid_request_id",
            "请求标识须为 1 到 64 位字母、数字、短横线或下划线",
        ));
    }
    Ok(())
}

fn chat(state: &ProviderState, request_id: &str) -> Result<ProviderRequest> {
    validate_request_id(request_id)?;
    let store = state.store()?;
    let mut request = store
        .conn
        .query_row(
            "SELECT request_id, provider_id, model, status, output, usage_json, finish_reason, error_code, error_message, attempts, latency_ms, created_at, updated_at, reasoning, tool_calls_json FROM provider_requests WHERE request_id = ?1",
            params![request_id],
            |row| {
                let error_code: Option<String> = row.get(7)?;
                let error_message: Option<String> = row.get(8)?;
                let usage_json: Option<String> = row.get(5)?;
                let tool_calls: Option<String> = row.get(14)?;
                Ok(ProviderRequest {
                    request_id: row.get(0)?,
                    provider_id: format!("p{}", row.get::<_, i64>(1)?),
                    model: row.get(2)?,
                    status: row.get(3)?,
                    output: row.get(4)?,
                    reasoning: row.get(13)?,
                    tool_calls: tool_calls.map(|value| serde_json::from_str(&value)).transpose().unwrap_or_default().unwrap_or_default(),
                    usage: match usage_json {
                        Some(value) => serde_json::from_str(&value).unwrap_or(None),
                        None => None,
                    },
                    finish_reason: row.get(6)?,
                    error: error_code.map(|code| BackendError::new(&code, error_message.as_deref().unwrap_or("模型请求失败"))),
                    attempts: row.get::<_, i64>(9)? as u8,
                    latency_ms: row.get(10)?,
                    created_at: row.get(11)?,
                    updated_at: row.get(12)?,
                })
            },
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "模型请求不存在"))?;
    if request.status != "done" {
        let rebuild_output = request.output.is_empty();
        let rebuild_reasoning = request.reasoning.is_empty();
        if rebuild_output || rebuild_reasoning {
            let mut statement = store.conn.prepare(
                "SELECT payload FROM events WHERE kind = 'provider.chat_delta' AND json_extract(payload, '$.requestId') = ?1 ORDER BY id",
            )?;
            let rows = statement.query_map(params![request_id], |row| row.get::<_, String>(0))?;
            for row in rows {
                let payload: Value = serde_json::from_str(&row?)?;
                if let Some(text) = payload.get("text").and_then(Value::as_str) {
                    if payload.get("reasoning").and_then(Value::as_bool) == Some(true) {
                        if rebuild_reasoning {
                            request.reasoning.push_str(text);
                        }
                    } else if rebuild_output {
                        request.output.push_str(text);
                    }
                }
            }
        }
    }
    Ok(request)
}

pub fn get_chat(state: &ProviderState, request_id: &str) -> Result<ProviderRequest> {
    match routing::latest_chat(state, request_id) {
        Ok(request) => Ok(request),
        Err(error) if error.code == "not_found" => chat(state, request_id),
        Err(error) => Err(error),
    }
}

pub fn completed_chat(state: &ProviderState, request_id: Option<&str>) -> Result<bool> {
    let Some(request_id) = request_id else {
        return Ok(false);
    };
    match get_chat(state, request_id) {
        Ok(request) => Ok(request.status == "done"),
        Err(error) if error.code == "not_found" => Ok(false),
        Err(error) => Err(error),
    }
}

fn begin_chat<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    provider_id: &str,
    model: Option<&str>,
) -> Result<(ProviderRequest, Option<ChatGuard>)> {
    validate_request_id(request_id)?;
    let provider_id = format!("p{}", numeric_id(provider_id)?);
    let model = match model.map(str::trim).filter(|model| !model.is_empty()) {
        Some(model) if model.len() <= 200 => model.to_string(),
        Some(_) => {
            return Err(BackendError::invalid(
                "invalid_model",
                "模型名不能超过 200 字节",
            ))
        }
        None => get(state, &provider_id)?.default_model,
    };
    let mut cancels = state
        .chat_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "模型请求状态不可用"))?;
    match chat(state, request_id) {
        Ok(existing) => {
            if existing.provider_id != provider_id || existing.model != model {
                return Err(BackendError::new(
                    "request_conflict",
                    "请求标识已用于其他供应商或模型，不会覆盖原请求",
                ));
            }
            if existing.status == "running" && cancels.contains_key(request_id) {
                return Err(BackendError::new(
                    "request_active",
                    "该请求正在执行，不会重复调用模型",
                ));
            }
            return Ok((existing, None));
        }
        Err(error) if error.code == "not_found" => {}
        Err(error) => return Err(error),
    }
    let store = state.store()?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_requests (request_id, provider_id, model, status, created_at, updated_at) VALUES (?1, ?2, ?3, 'running', ?4, ?4)",
        params![request_id, numeric_id(&provider_id)?, model, timestamp],
    )?;
    let event = Store::event(
        &tx,
        "provider.chat_started",
        json!({"requestId": request_id, "providerId": provider_id}),
    )?;
    cancels.insert(
        request_id.to_string(),
        Arc::new(std::sync::atomic::AtomicBool::new(false)),
    );
    if let Err(error) = tx.commit() {
        cancels.remove(request_id);
        return Err(error.into());
    }
    drop(cancels);
    emit(app, &event);
    Ok((
        chat(state, request_id)?,
        Some(ChatGuard {
            cancels: Arc::clone(&state.chat_cancels),
            request_id: request_id.to_string(),
        }),
    ))
}

pub fn stream_chat<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    provider_id: &str,
    messages: &[ChatMessage],
    max_tokens: u32,
) -> Result<ProviderRequest> {
    stream_chat_with_tools(
        app,
        state,
        request_id,
        provider_id,
        messages,
        max_tokens,
        None,
    )
}

pub fn stream_chat_with_tools<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    provider_id: &str,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[tools::ToolDefinition]>,
) -> Result<ProviderRequest> {
    stream_chat_with_model(
        app,
        state,
        request_id,
        provider_id,
        messages,
        max_tokens,
        None,
        tools,
    )
}

pub fn stream_chat_with_model<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    provider_id: &str,
    messages: &[ChatMessage],
    max_tokens: u32,
    model: Option<&str>,
    tools: Option<&[tools::ToolDefinition]>,
) -> Result<ProviderRequest> {
    stream_chat_with_policy(
        app,
        state,
        request_id,
        provider_id,
        messages,
        max_tokens,
        model,
        tools,
        openai::StreamPolicy::default(),
        None,
    )
}

pub fn stream_chat_with_policy<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    provider_id: &str,
    messages: &[ChatMessage],
    max_tokens: u32,
    model: Option<&str>,
    tools: Option<&[tools::ToolDefinition]>,
    policy: openai::StreamPolicy,
    parent_cancel: Option<Arc<std::sync::atomic::AtomicBool>>,
) -> Result<ProviderRequest> {
    let (pending, guard) = begin_chat(app, state, request_id, provider_id, model)?;
    let Some(_guard) = guard else {
        return chat(state, request_id);
    };
    let store = state.store()?;
    let cancel = state
        .chat_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "模型请求状态不可用"))?
        .get(request_id)
        .cloned()
        .ok_or_else(|| BackendError::new("request_active", "请求状态已改变"))?;
    // 直连路径同样受用量预算约束，与路由路径保持一致，不留绕过防线
    usage::ensure_budget(state, None)?;
    let started = Instant::now();
    let result = get(state, provider_id)
        .and_then(|profile| {
            if !profile.enabled {
                return Err(BackendError::new("provider_disabled", "已停用的供应商不能发起请求"));
            }
            let key = state.credentials.get(provider_id)?;
            Ok((profile, key))
        })
        .map_err(openai::ChatError::from)
        .and_then(|(mut profile, key)| {
            profile.default_model = pending.model;
            protocol::stream_chat_with_policy(&profile, key, messages, max_tokens, tools, cancel, parent_cancel, policy, |delta| {
                let event = Store::event(
                    &store.conn,
                    "provider.chat_delta",
                    json!({"requestId": request_id, "text": delta.text, "reasoning": delta.reasoning}),
                )?;
                emit(app, &event);
                Ok(())
            }, request_id)
        });
    let (status, output, reasoning, tool_calls, usage, finish_reason, error, attempts) =
        match result {
            Ok(result) => (
                "done",
                result.text,
                result.reasoning,
                result.tool_calls,
                result.usage,
                result.finish_reason,
                None,
                result.attempts,
            ),
            Err(error) => {
                let status = if error.error.code == "cancelled" {
                    "cancelled"
                } else {
                    "failed"
                };
                (
                    status,
                    error.partial_text,
                    error.partial_reasoning,
                    error.partial_tool_calls,
                    error.usage,
                    None,
                    Some(error.error),
                    error.attempts,
                )
            }
        };
    let latency = started.elapsed().as_millis() as i64;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE provider_requests SET status = ?1, output = ?2, usage_json = ?3, finish_reason = ?4, error_code = ?5, error_message = ?6, attempts = ?7, latency_ms = ?8, updated_at = ?9, reasoning = ?10, tool_calls_json = ?11 WHERE request_id = ?12",
        params![status, output, serde_json::to_string(&usage)?, finish_reason, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message), attempts, latency, now()?, reasoning, serde_json::to_string(&tool_calls)?, request_id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "request_deleted",
            "请求已被删除，未提交新的模型结果",
        ));
    }
    let event = Store::event(
        &tx,
        if status == "done" {
            "provider.chat_completed"
        } else if status == "cancelled" {
            "provider.chat_cancelled"
        } else {
            "provider.chat_failed"
        },
        json!({"requestId": request_id, "status": status, "output": output, "reasoning": reasoning, "toolCalls": tool_calls, "usage": usage, "finishReason": finish_reason, "attempts": attempts, "latencyMs": latency, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    chat(state, request_id)
}

pub fn cancel_chat(state: &ProviderState, request_id: &str) -> Result<()> {
    if routing::cancel(state, request_id)? {
        return Ok(());
    }
    cancel_direct_chat(state, request_id)
}

pub fn cancel_chat_with_app<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
) -> Result<()> {
    if routing::cancel_with_app(app, state, request_id)? {
        return Ok(());
    }
    cancel_direct_chat(state, request_id)
}

pub(crate) fn cancel_direct_chat(state: &ProviderState, request_id: &str) -> Result<()> {
    let flag = state
        .chat_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "模型请求状态不可用"))?
        .get(request_id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_request", "该请求没有正在执行的任务"))?;
    flag.store(true, std::sync::atomic::Ordering::Release);
    Ok(())
}

pub fn recover_chats<R: Runtime>(app: &AppHandle<R>, state: &ProviderState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT request_id FROM provider_requests WHERE status = 'running'")?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    let mut ids = Vec::new();
    for row in rows {
        ids.push(row?);
    }
    drop(statement);
    for request_id in ids {
        let tx = store.conn.unchecked_transaction()?;
        tx.execute(
            "UPDATE provider_requests SET status = 'interrupted', error_code = 'request_interrupted', error_message = '应用重启后未自动重试模型请求', updated_at = ?1 WHERE request_id = ?2",
            params![now()?, request_id],
        )?;
        let event = Store::event(
            &tx,
            "provider.chat_interrupted",
            json!({"requestId": request_id}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn stop_chats(state: &ProviderState) -> Result<()> {
    routing::stop(state)
}

pub(crate) fn stop_direct_chats(state: &ProviderState) -> Result<()> {
    let cancels = state
        .chat_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "模型请求状态不可用"))?;
    for flag in cancels.values() {
        flag.store(true, std::sync::atomic::Ordering::Release);
    }
    drop(cancels);
    let deadline = Instant::now() + std::time::Duration::from_secs(15);
    while Instant::now() < deadline {
        if state
            .chat_cancels
            .lock()
            .map(|cancels| cancels.is_empty())
            .unwrap_or(false)
        {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Err(BackendError::new(
        "request_active",
        "模型请求尚未停止，请稍后重试",
    ))
}

#[cfg(test)]
pub(crate) mod test_support;

#[cfg(test)]
mod protocol_tests;
#[cfg(test)]
mod tests;
