use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::store::Store;
use super::{active, list, ChatMessage, ProviderProfile, ProviderRequest, ProviderState};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

pub const TASKS: [&str; 7] = [
    "agent",
    "plan",
    "lecture",
    "practice",
    "wiki",
    "resources",
    "embedding",
];
const SETTINGS_KEY: &str = "routing_settings";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteAssignment {
    pub provider_id: Option<String>,
    pub model: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RoutingSettings {
    pub mode: String,
    pub routes: BTreeMap<String, RouteAssignment>,
    pub aggregate_provider_ids: Vec<String>,
    pub aggregate_default: Option<String>,
}

impl Default for RoutingSettings {
    fn default() -> Self {
        Self {
            mode: "direct".into(),
            routes: BTreeMap::new(),
            aggregate_provider_ids: Vec::new(),
            aggregate_default: None,
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteLog {
    pub request_id: String,
    pub task: String,
    pub mode: String,
    pub status: String,
    pub provider_id: Option<String>,
    pub model: Option<String>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS provider_route_runs (
            request_id TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            provider_id INTEGER,
            model TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_route_attempts (
            request_id TEXT NOT NULL REFERENCES provider_route_runs(request_id) ON DELETE CASCADE,
            attempt INTEGER NOT NULL,
            provider_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            upstream_request_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(request_id, attempt)
        );",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM provider_route_attempts", [])?;
    conn.execute("DELETE FROM provider_route_runs", [])?;
    conn.execute(
        "DELETE FROM provider_meta WHERE key = ?1",
        params![SETTINGS_KEY],
    )?;
    Ok(())
}

fn profile_id(value: &str) -> Result<i64> {
    let id = value.strip_prefix('p').unwrap_or(value);
    id.parse::<i64>()
        .ok()
        .filter(|id| *id > 0)
        .ok_or_else(|| BackendError::invalid("invalid_provider_id", "供应商档案标识无效"))
}

fn valid_model(model: &str) -> bool {
    !model.trim().is_empty() && model.len() <= 200
}

pub fn settings(state: &ProviderState) -> Result<RoutingSettings> {
    let store = state.store()?;
    let saved = store
        .conn
        .query_row(
            "SELECT value FROM provider_meta WHERE key = ?1",
            params![SETTINGS_KEY],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    match saved {
        Some(value) => Ok(serde_json::from_str(&value)?),
        None => Ok(RoutingSettings::default()),
    }
}

pub fn provider_in_use(state: &ProviderState, id: &str) -> Result<bool> {
    let settings = settings(state)?;
    Ok(settings
        .aggregate_provider_ids
        .iter()
        .any(|item| item == id)
        || settings.aggregate_default.as_deref() == Some(id)
        || settings
            .routes
            .values()
            .any(|route| route.provider_id.as_deref() == Some(id)))
}

pub fn save_settings<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    mut settings: RoutingSettings,
) -> Result<RoutingSettings> {
    if !["direct", "routing", "aggregate"].contains(&settings.mode.as_str()) {
        return Err(BackendError::invalid(
            "invalid_routing_mode",
            "路由模式无效",
        ));
    }
    if settings.routes.len() > TASKS.len()
        || settings
            .routes
            .keys()
            .any(|task| !TASKS.contains(&task.as_str()))
    {
        return Err(BackendError::invalid(
            "invalid_route_task",
            "任务路由包含未知任务",
        ));
    }
    let profiles = list(state)?;
    let enabled = profiles
        .iter()
        .filter(|profile| profile.enabled)
        .map(|profile| profile.id.clone())
        .collect::<BTreeSet<_>>();
    for route in settings.routes.values_mut() {
        if route.provider_id.as_deref() == Some("default") {
            route.provider_id = None;
        }
        route.model = route
            .model
            .take()
            .map(|model| model.trim().to_string())
            .filter(|model| !model.is_empty());
        if let Some(id) = &route.provider_id {
            let profile = profiles
                .iter()
                .find(|profile| &profile.id == id && profile.enabled)
                .ok_or_else(|| {
                    BackendError::invalid(
                        "invalid_route_provider",
                        "任务路由引用了不存在或已停用的供应商",
                    )
                })?;
            if let Some(model) = &route.model {
                if !valid_model(model) || !profile.models.contains(model) {
                    return Err(BackendError::invalid(
                        "invalid_route_model",
                        "任务模型不在所选供应商的模型列表中",
                    ));
                }
            }
        } else if route.model.is_some() {
            return Err(BackendError::invalid(
                "route_model_without_provider",
                "跟随当前供应商时不能固定其他模型",
            ));
        }
    }
    let aggregate = settings
        .aggregate_provider_ids
        .iter()
        .map(|id| (id.clone(), profile_id(id)))
        .collect::<Vec<_>>();
    if aggregate.iter().any(|(_, result)| result.is_err()) {
        return Err(BackendError::invalid(
            "invalid_provider_id",
            "聚合供应商标识无效",
        ));
    }
    let ids = settings
        .aggregate_provider_ids
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>();
    if ids.len() != settings.aggregate_provider_ids.len()
        || settings
            .aggregate_provider_ids
            .iter()
            .any(|id| !enabled.contains(id))
    {
        return Err(BackendError::invalid(
            "invalid_aggregate_provider",
            "聚合包含重复、不存在或已停用的供应商",
        ));
    }
    if settings.mode == "aggregate" {
        let Some(default) = &settings.aggregate_default else {
            return Err(BackendError::invalid(
                "aggregate_default_required",
                "聚合模式需要默认供应商",
            ));
        };
        if !ids.contains(default) {
            return Err(BackendError::invalid(
                "invalid_aggregate_default",
                "聚合默认供应商必须属于聚合列表",
            ));
        }
    }
    if settings
        .aggregate_default
        .as_deref()
        .is_some_and(|id| !ids.contains(id))
    {
        return Err(BackendError::invalid(
            "invalid_aggregate_default",
            "聚合默认供应商必须属于聚合列表",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_meta (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![SETTINGS_KEY, serde_json::to_string(&settings)?],
    )?;
    let event = Store::event(
        &tx,
        "provider.routing_saved",
        json!({"mode": settings.mode}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(settings)
}

fn assignment<'a>(settings: &'a RoutingSettings, task: &str) -> Option<&'a RouteAssignment> {
    settings.routes.get(task)
}

fn select(
    state: &ProviderState,
    task: &str,
    preferred_provider: Option<&str>,
    requested_model: Option<&str>,
    pinned: bool,
) -> Result<(ProviderProfile, String)> {
    if !TASKS.contains(&task) {
        return Err(BackendError::invalid(
            "invalid_route_task",
            "任务类型不受支持",
        ));
    }
    let settings = settings(state)?;
    let route = assignment(&settings, task).filter(|route| {
        !pinned || route.provider_id.is_none() || route.provider_id.as_deref() == preferred_provider
    });
    let profiles = list(state)?;
    let enabled = |id: &str| {
        profiles
            .iter()
            .find(|profile| profile.id == id && profile.enabled)
            .cloned()
    };
    let fallback = || -> Result<ProviderProfile> {
        if let Some(id) = preferred_provider {
            return enabled(id)
                .ok_or_else(|| BackendError::new("provider_disabled", "首选供应商不存在或已停用"));
        }
        let active = active(state)?.ok_or_else(|| {
            BackendError::new("provider_not_configured", "请先配置并选择模型供应商")
        })?;
        enabled(&active)
            .ok_or_else(|| BackendError::new("provider_disabled", "当前供应商不存在或已停用"))
    };
    let (profile, model) = match settings.mode.as_str() {
        "direct" => {
            let profile = fallback()?;
            let model = requested_model
                .unwrap_or(&profile.default_model)
                .to_string();
            (profile, model)
        }
        "routing" => {
            let profile = if pinned {
                fallback()?
            } else if let Some(id) = route.and_then(|route| route.provider_id.as_deref()) {
                enabled(id).ok_or_else(|| {
                    BackendError::new("invalid_route_provider", "任务路由供应商不存在或已停用")
                })?
            } else {
                fallback()?
            };
            let model = requested_model
                .map(str::to_string)
                .or_else(|| route.and_then(|route| route.model.clone()))
                .unwrap_or_else(|| profile.default_model.clone());
            (profile, model)
        }
        "aggregate" => {
            let route_model = requested_model
                .map(str::to_string)
                .or_else(|| route.and_then(|route| route.model.clone()));
            let candidates = settings
                .aggregate_provider_ids
                .iter()
                .filter_map(|id| enabled(id))
                .collect::<Vec<_>>();
            let preferred = if pinned { preferred_provider } else { None };
            let profile = if let Some(model) = &route_model {
                candidates
                    .iter()
                    .find(|profile| {
                        preferred.is_some_and(|id| id == profile.id)
                            && profile.models.contains(model)
                    })
                    .or_else(|| {
                        candidates
                            .iter()
                            .find(|profile| !pinned && profile.models.contains(model))
                    })
                    .cloned()
            } else {
                preferred.and_then(|id| candidates.iter().find(|profile| profile.id == id).cloned())
            };
            let profile = match profile {
                Some(profile) => profile,
                None if route_model.is_some() => {
                    return Err(BackendError::new(
                        "aggregate_model_unavailable",
                        "所选模型不在已启用的聚合供应商中",
                    ));
                }
                None => {
                    let default = settings.aggregate_default.as_deref().ok_or_else(|| {
                        BackendError::new("aggregate_default_required", "请配置聚合默认供应商")
                    })?;
                    enabled(default).ok_or_else(|| {
                        BackendError::new("provider_disabled", "聚合默认供应商不存在或已停用")
                    })?
                }
            };
            let model = route_model.unwrap_or_else(|| profile.default_model.clone());
            (profile, model)
        }
        _ => {
            return Err(BackendError::invalid(
                "invalid_routing_mode",
                "路由模式无效",
            ))
        }
    };
    if !valid_model(&model) || !profile.models.contains(&model) {
        return Err(BackendError::invalid(
            "route_model_unavailable",
            "所选模型不在实际上游供应商的模型列表中",
        ));
    }
    Ok((profile, model))
}

pub fn provider_for_task(state: &ProviderState, task: &str) -> Result<String> {
    Ok(select(state, task, None, None, false)?.0.id)
}

pub fn stream_chat<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    task: &str,
    preferred_provider: Option<&str>,
    messages: &[ChatMessage],
    max_tokens: u32,
    model: Option<&str>,
    tools: Option<&[super::tools::ToolDefinition]>,
    pinned: bool,
) -> Result<ProviderRequest> {
    let (profile, model) = select(state, task, preferred_provider, model, pinned)?;
    let store = state.store()?;
    let existing = store
        .conn
        .query_row(
            "SELECT task FROM provider_route_runs WHERE request_id = ?1",
            params![request_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if let Some(existing_task) = existing {
        let result = super::get_chat(state, request_id)?;
        if existing_task != task || result.provider_id != profile.id || result.model != model {
            return Err(BackendError::new(
                "request_conflict",
                "请求标识已用于其他任务、供应商或模型，不会覆盖原请求",
            ));
        }
        return Ok(result);
    }
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_route_runs (request_id, task, mode, status, provider_id, model, created_at, updated_at) VALUES (?1, ?2, ?3, 'running', ?4, ?5, ?6, ?6)",
        params![request_id, task, settings(state)?.mode, profile_id(&profile.id)?, model, timestamp],
    )?;
    tx.execute(
        "INSERT INTO provider_route_attempts (request_id, attempt, provider_id, model, upstream_request_id, status, created_at) VALUES (?1, 1, ?2, ?3, ?4, 'running', ?5)",
        params![request_id, profile_id(&profile.id)?, model, request_id, timestamp],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_started",
        json!({"requestId": request_id, "task": task, "providerId": profile.id, "model": model}),
    )?;
    tx.commit()?;
    emit(app, &event);
    let result = super::stream_chat_with_model(
        app,
        state,
        request_id,
        &profile.id,
        messages,
        max_tokens,
        Some(&model),
        tools,
    );
    match result {
        Ok(result) => {
            finish(app, state, request_id, &result)?;
            Ok(result)
        }
        Err(error) => {
            let tx = store.conn.unchecked_transaction()?;
            tx.execute("UPDATE provider_route_attempts SET status = 'failed', error_code = ?1, error_message = ?2 WHERE request_id = ?3 AND attempt = 1", params![error.code, error.message, request_id])?;
            tx.execute("UPDATE provider_route_runs SET status = 'failed', error_code = ?1, error_message = ?2, updated_at = ?3 WHERE request_id = ?4", params![error.code, error.message, now()?, request_id])?;
            let event = Store::event(
                &tx,
                "provider.route_failed",
                json!({"requestId": request_id, "error": error}),
            )?;
            tx.commit()?;
            emit(app, &event);
            Err(error)
        }
    }
}

fn finish<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    result: &ProviderRequest,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE provider_route_attempts SET status = ?1, latency_ms = ?2, error_code = ?3, error_message = ?4 WHERE request_id = ?5 AND attempt = 1",
        params![result.status, result.latency_ms, result.error.as_ref().map(|error| &error.code), result.error.as_ref().map(|error| &error.message), request_id],
    )?;
    tx.execute(
        "UPDATE provider_route_runs SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE request_id = ?5",
        params![result.status, result.error.as_ref().map(|error| &error.code), result.error.as_ref().map(|error| &error.message), now()?, request_id],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_completed",
        json!({"requestId": request_id, "providerId": result.provider_id, "model": result.model, "status": result.status, "attempts": 1}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn logs(state: &ProviderState) -> Result<Vec<RouteLog>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT request_id, task, mode, status, provider_id, model, error_code, error_message, created_at, updated_at FROM provider_route_runs ORDER BY created_at DESC, rowid DESC LIMIT 100")?;
    let rows = statement.query_map([], |row| {
        let code: Option<String> = row.get(6)?;
        let message: Option<String> = row.get(7)?;
        Ok(RouteLog {
            request_id: row.get(0)?,
            task: row.get(1)?,
            mode: row.get(2)?,
            status: row.get(3)?,
            provider_id: row.get::<_, Option<i64>>(4)?.map(|id| format!("p{id}")),
            model: row.get(5)?,
            error: code
                .map(|code| BackendError::new(&code, message.as_deref().unwrap_or("路由请求失败"))),
            created_at: row.get(8)?,
            updated_at: row.get(9)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &ProviderState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT request_id FROM provider_route_runs WHERE status IN ('running', 'cancelling')",
    )?;
    let ids = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for id in ids {
        let current = super::get_chat(state, &id).ok();
        let latency = current.as_ref().and_then(|request| request.latency_ms);
        let (status, error) = match current {
            Some(request) if request.status != "running" => (request.status, request.error),
            _ => (
                "interrupted".into(),
                Some(BackendError::new(
                    "request_interrupted",
                    "应用重启后未自动重试路由请求",
                )),
            ),
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE provider_route_runs SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE request_id = ?5", params![status, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message), now()?, id])?;
        tx.execute("UPDATE provider_route_attempts SET status = ?1, latency_ms = ?2, error_code = ?3, error_message = ?4 WHERE request_id = ?5 AND status = 'running'", params![status, latency, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message), id])?;
        let event = Store::event(
            &tx,
            if status == "interrupted" {
                "provider.route_interrupted"
            } else {
                "provider.route_recovered"
            },
            json!({"requestId": id, "status": status}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn export(state: &ProviderState) -> Result<Value> {
    Ok(serde_json::to_value(settings(state)?)?)
}
