use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::store::Store;
use super::{active, list, ChatMessage, ProviderProfile, ProviderRequest, ProviderState};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
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
pub struct CircuitSettings {
    pub failures: u32,
    pub cooldown: u64,
    pub timeout: u64,
    pub retries: u8,
}

impl Default for CircuitSettings {
    fn default() -> Self {
        Self {
            failures: 3,
            cooldown: 60,
            timeout: 45,
            retries: 2,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default, deny_unknown_fields)]
pub struct RoutingSettings {
    pub mode: String,
    pub routes: BTreeMap<String, RouteAssignment>,
    pub aggregate_provider_ids: Vec<String>,
    pub aggregate_default: Option<String>,
    pub failover: Vec<String>,
    pub circuit: CircuitSettings,
}

impl Default for RoutingSettings {
    fn default() -> Self {
        Self {
            mode: "direct".into(),
            routes: BTreeMap::new(),
            aggregate_provider_ids: Vec::new(),
            aggregate_default: None,
            failover: Vec::new(),
            circuit: CircuitSettings::default(),
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteAttemptLog {
    pub attempt: u32,
    pub provider_id: String,
    pub model: String,
    pub upstream_request_id: Option<String>,
    pub status: String,
    pub latency_ms: Option<i64>,
    pub error: Option<BackendError>,
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
    pub attempts: Vec<RouteAttemptLog>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CircuitSnapshot {
    pub provider_id: String,
    pub state: String,
    pub failures: u32,
    pub opened_at: Option<String>,
    pub last_error: Option<BackendError>,
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
            upstream_request_id TEXT UNIQUE,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(request_id, attempt)
        );
        CREATE TABLE IF NOT EXISTS provider_circuits (
            provider_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'closed',
            failures INTEGER NOT NULL DEFAULT 0,
            opened_at TEXT,
            error_code TEXT,
            error_message TEXT
        );",
    )?;
    let legacy = conn
        .query_row(
            "SELECT [notnull] FROM pragma_table_info('provider_route_attempts') WHERE name = 'upstream_request_id'",
            [],
            |row| row.get::<_, bool>(0),
        )
        .optional()?
        .unwrap_or(false);
    if legacy {
        conn.execute_batch(
            "ALTER TABLE provider_route_attempts RENAME TO provider_route_attempts_legacy;
            CREATE TABLE provider_route_attempts (
                request_id TEXT NOT NULL REFERENCES provider_route_runs(request_id) ON DELETE CASCADE,
                attempt INTEGER NOT NULL,
                provider_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                upstream_request_id TEXT UNIQUE,
                status TEXT NOT NULL,
                latency_ms INTEGER,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(request_id, attempt)
            );
            INSERT INTO provider_route_attempts SELECT * FROM provider_route_attempts_legacy;
            DROP TABLE provider_route_attempts_legacy;",
        )?;
    }
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM provider_route_attempts", [])?;
    conn.execute("DELETE FROM provider_route_runs", [])?;
    conn.execute("DELETE FROM provider_circuits", [])?;
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
        || settings.failover.iter().any(|item| item == id)
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
    if !(1..=10).contains(&settings.circuit.failures)
        || !(10..=600).contains(&settings.circuit.cooldown)
        || !(5..=300).contains(&settings.circuit.timeout)
        || settings.circuit.retries > 3
    {
        return Err(BackendError::invalid(
            "invalid_circuit_settings",
            "熔断阈值、冷却、超时或重试次数超出允许范围",
        ));
    }
    if settings.failover.len() > 16 {
        return Err(BackendError::new(
            "quota_exceeded",
            "故障转移队列最多支持 16 个供应商",
        ));
    }
    settings
        .failover
        .iter()
        .map(|id| profile_id(id))
        .collect::<Result<Vec<_>>>()?;
    let failover = settings.failover.iter().cloned().collect::<BTreeSet<_>>();
    if failover.len() != settings.failover.len()
        || settings.failover.iter().any(|id| !enabled.contains(id))
    {
        return Err(BackendError::invalid(
            "invalid_failover_provider",
            "故障转移队列包含重复、不存在或已停用的供应商",
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
) -> Result<(ProviderProfile, String, String)> {
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
    Ok((profile, model, settings.mode))
}

pub fn provider_for_task(state: &ProviderState, task: &str) -> Result<String> {
    Ok(select(state, task, None, None, false)?.0.id)
}

type RouteCancelMap = Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>;

struct RouteGuard {
    cancels: RouteCancelMap,
    request_id: String,
}

impl Drop for RouteGuard {
    fn drop(&mut self) {
        if let Ok(mut cancels) = self.cancels.lock() {
            cancels.remove(&self.request_id);
        }
    }
}

fn register_route(state: &ProviderState, request_id: &str) -> Result<RouteGuard> {
    let mut cancels = state
        .route_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "路由任务状态不可用"))?;
    if cancels.contains_key(request_id) {
        return Err(BackendError::new("request_active", "该路由请求正在执行"));
    }
    cancels.insert(request_id.into(), Arc::new(AtomicBool::new(false)));
    Ok(RouteGuard {
        cancels: Arc::clone(&state.route_cancels),
        request_id: request_id.into(),
    })
}

fn route_cancelled(state: &ProviderState, request_id: &str) -> bool {
    state
        .route_cancels
        .lock()
        .ok()
        .and_then(|cancels| cancels.get(request_id).cloned())
        .is_some_and(|flag| flag.load(Ordering::Acquire))
}

fn failure_request(
    request_id: &str,
    provider_id: &str,
    model: &str,
    status: &str,
    error: BackendError,
) -> Result<ProviderRequest> {
    let timestamp = now()?;
    Ok(ProviderRequest {
        request_id: request_id.into(),
        provider_id: provider_id.into(),
        model: model.into(),
        status: status.into(),
        output: String::new(),
        reasoning: String::new(),
        tool_calls: vec![],
        usage: None,
        finish_reason: None,
        error: Some(error),
        attempts: 0,
        latency_ms: None,
        created_at: timestamp.clone(),
        updated_at: timestamp,
    })
}

fn can_failover(error: &BackendError) -> bool {
    matches!(
        error.code.as_str(),
        "provider_unavailable"
            | "rate_limited"
            | "provider_timeout"
            | "endpoint_unreachable"
            | "incomplete_stream"
    )
}

fn circuit_row(
    conn: &Connection,
    provider_id: i64,
) -> Result<(String, u32, Option<String>, Option<BackendError>)> {
    Ok(conn
        .query_row(
            "SELECT state, failures, opened_at, error_code, error_message FROM provider_circuits WHERE provider_id = ?1",
            params![provider_id],
            |row| {
                let code: Option<String> = row.get(3)?;
                let message: Option<String> = row.get(4)?;
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)? as u32,
                    row.get::<_, Option<String>>(2)?,
                    code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("上游请求失败"))),
                ))
            },
        )
        .optional()
        .map(|row| row.unwrap_or_else(|| ("closed".into(), 0, None, None)))?)
}

fn circuit_permit(
    conn: &Connection,
    provider_id: i64,
    cooldown: u64,
) -> Result<Option<BackendError>> {
    let (state, _, opened_at, _) = circuit_row(conn, provider_id)?;
    match state.as_str() {
        "closed" => Ok(None),
        "half_open" => Ok(Some(BackendError::new(
            "circuit_probing",
            "上游正在进行半开探测，未发起并行请求",
        ))),
        "open" => {
            let opened = opened_at
                .as_deref()
                .map(|value| OffsetDateTime::parse(value, &Rfc3339))
                .transpose()
                .map_err(|_| BackendError::new("invalid_circuit_state", "熔断时间格式无效"))?
                .ok_or_else(|| {
                    BackendError::new("invalid_circuit_state", "熔断状态缺少打开时间")
                })?;
            if OffsetDateTime::now_utc() < opened + time::Duration::seconds(cooldown as i64) {
                return Ok(Some(BackendError::new(
                    "circuit_open",
                    "上游处于熔断冷却期，未发起请求",
                )));
            }
            let changed = conn.execute(
                "UPDATE provider_circuits SET state = 'half_open' WHERE provider_id = ?1 AND state = 'open'",
                params![provider_id],
            )?;
            if changed == 1 {
                Ok(None)
            } else {
                Ok(Some(BackendError::new(
                    "circuit_probing",
                    "上游正在进行半开探测，未发起并行请求",
                )))
            }
        }
        _ => Err(BackendError::new("invalid_circuit_state", "未知的熔断状态")),
    }
}

fn record_circuit<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    provider_id: &str,
    success: bool,
    error: Option<BackendError>,
    settings: &CircuitSettings,
) -> Result<()> {
    let id = profile_id(provider_id)?;
    let store = state.store()?;
    let (previous, failures, opened_at, _) = circuit_row(&store.conn, id)?;
    let eligible = error.as_ref().is_some_and(can_failover);
    let (next, failures, opened_at) = if error
        .as_ref()
        .is_some_and(|error| error.code == "cancelled")
    {
        (
            if previous == "half_open" {
                "open".into()
            } else {
                previous
            },
            failures,
            opened_at,
        )
    } else if success {
        ("closed".into(), 0, None)
    } else if previous == "half_open" {
        ("open".into(), failures.saturating_add(1), Some(now()?))
    } else if eligible {
        let failures = failures.saturating_add(1);
        if failures >= settings.failures {
            ("open".into(), failures, Some(now()?))
        } else {
            ("closed".into(), failures, None)
        }
    } else {
        (previous, failures, opened_at)
    };
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_circuits (provider_id, state, failures, opened_at, error_code, error_message) VALUES (?1, ?2, ?3, ?4, ?5, ?6) ON CONFLICT(provider_id) DO UPDATE SET state = excluded.state, failures = excluded.failures, opened_at = excluded.opened_at, error_code = excluded.error_code, error_message = excluded.error_message",
        params![id, next, failures, opened_at, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message)],
    )?;
    let event = Store::event(
        &tx,
        "provider.circuit_changed",
        json!({"providerId": provider_id, "state": next, "failures": failures, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn save_attempt<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    attempt: u32,
    profile: &ProviderProfile,
    model: &str,
    upstream_request_id: Option<&str>,
    status: &str,
    latency_ms: Option<i64>,
    error: Option<BackendError>,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_route_attempts (request_id, attempt, provider_id, model, upstream_request_id, status, latency_ms, error_code, error_message, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10) ON CONFLICT(request_id, attempt) DO UPDATE SET upstream_request_id = excluded.upstream_request_id, status = excluded.status, latency_ms = excluded.latency_ms, error_code = excluded.error_code, error_message = excluded.error_message",
        params![request_id, attempt, profile_id(&profile.id)?, model, upstream_request_id, status, latency_ms, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message), now()?],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_attempt",
        json!({"requestId": request_id, "attempt": attempt, "providerId": profile.id, "model": model, "upstreamRequestId": upstream_request_id, "status": status, "latencyMs": latency_ms, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn finish<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    result: &ProviderRequest,
) -> Result<()> {
    let store = state.store()?;
    let provider_id = if result.provider_id.is_empty() {
        None
    } else {
        Some(profile_id(&result.provider_id)?)
    };
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE provider_route_runs SET status = ?1, provider_id = ?2, model = ?3, error_code = ?4, error_message = ?5, updated_at = ?6 WHERE request_id = ?7",
        params![result.status, provider_id, result.model, result.error.as_ref().map(|error| &error.code), result.error.as_ref().map(|error| &error.message), now()?, request_id],
    )?;
    let attempts: i64 = tx.query_row(
        "SELECT COUNT(*) FROM provider_route_attempts WHERE request_id = ?1 AND upstream_request_id IS NOT NULL",
        params![request_id],
        |row| row.get(0),
    )?;
    let event = Store::event(
        &tx,
        "provider.route_completed",
        json!({"requestId": request_id, "providerId": result.provider_id, "model": result.model, "status": result.status, "attempts": attempts}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn finish_error<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
    error: &BackendError,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE provider_route_runs SET status = 'failed', error_code = ?1, error_message = ?2, updated_at = ?3 WHERE request_id = ?4",
        params![error.code, error.message, now()?, request_id],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_failed",
        json!({"requestId": request_id, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn latest_chat(state: &ProviderState, request_id: &str) -> Result<ProviderRequest> {
    let store = state.store()?;
    let (status, provider_id, model, code, message, created_at, updated_at): (
        String,
        Option<i64>,
        Option<String>,
        Option<String>,
        Option<String>,
        String,
        String,
    ) = store
        .conn
        .query_row(
            "SELECT status, provider_id, model, error_code, error_message, created_at, updated_at FROM provider_route_runs WHERE request_id = ?1",
            params![request_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?)),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "路由请求不存在"))?;
    let error =
        code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("路由请求失败")));
    let upstream = store
        .conn
        .query_row(
            "SELECT upstream_request_id FROM provider_route_attempts WHERE request_id = ?1 AND upstream_request_id IS NOT NULL ORDER BY attempt DESC LIMIT 1",
            params![request_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if let Some(upstream) = upstream {
        let mut result = super::chat(state, &upstream)?;
        result.request_id = request_id.into();
        if status == "cancelling" || status == "cancelled" {
            result.status = "cancelled".into();
            result.error = Some(BackendError::new("cancelled", "路由请求已取消"));
        }
        return Ok(result);
    }
    let attempts = store.conn.query_row(
        "SELECT COUNT(*) FROM provider_route_attempts WHERE request_id = ?1 AND upstream_request_id IS NOT NULL",
        params![request_id],
        |row| row.get::<_, i64>(0),
    )?;
    Ok(ProviderRequest {
        request_id: request_id.into(),
        provider_id: provider_id.map(|id| format!("p{id}")).unwrap_or_default(),
        model: model.unwrap_or_default(),
        status,
        output: String::new(),
        reasoning: String::new(),
        tool_calls: vec![],
        usage: None,
        finish_reason: None,
        error,
        attempts: attempts as u8,
        latency_ms: None,
        created_at,
        updated_at,
    })
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
    let (profile, model, mode) = select(state, task, preferred_provider, model, pinned)?;
    let settings = settings(state)?;
    if mode == "routing" && request_id.len() > 60 {
        return Err(BackendError::invalid(
            "invalid_request_id",
            "路由请求标识不能超过 60 字节",
        ));
    }
    let store = state.store()?;
    let existing = store
        .conn
        .query_row(
            "SELECT provider_id, model FROM provider_route_attempts WHERE request_id = ?1 AND attempt = 1",
            params![request_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()?;
    if let Some((provider_id, existing_model)) = existing {
        let route_task: String = store.conn.query_row(
            "SELECT task FROM provider_route_runs WHERE request_id = ?1",
            params![request_id],
            |row| row.get(0),
        )?;
        if route_task != task || provider_id != profile_id(&profile.id)? || existing_model != model
        {
            return Err(BackendError::new(
                "request_conflict",
                "请求标识已用于其他任务、供应商或模型，不会覆盖原请求",
            ));
        }
        return latest_chat(state, request_id);
    }
    super::usage::ensure_budget(state, None)?;
    let _guard = register_route(state, request_id)?;
    let cancel = state
        .route_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "路由任务状态不可用"))?
        .get(request_id)
        .cloned()
        .ok_or_else(|| BackendError::new("request_active", "路由状态已改变"))?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO provider_route_runs (request_id, task, mode, status, provider_id, model, created_at, updated_at) VALUES (?1, ?2, ?3, 'running', ?4, ?5, ?6, ?6)",
        params![request_id, task, mode, profile_id(&profile.id)?, model, timestamp],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_started",
        json!({"requestId": request_id, "task": task, "providerId": profile.id, "model": model}),
    )?;
    tx.commit()?;
    emit(app, &event);
    let mut candidates = vec![(profile.clone(), model.clone())];
    if mode == "routing" {
        let profiles = list(state)?;
        for id in &settings.failover {
            if id != &profile.id {
                if let Some(profile) = profiles
                    .iter()
                    .find(|profile| &profile.id == id && profile.enabled)
                {
                    let model = if profile.models.contains(&model) {
                        model.clone()
                    } else {
                        profile.default_model.clone()
                    };
                    candidates.push((profile.clone(), model));
                }
            }
        }
    }
    let mut last_result = None;
    for (index, (candidate, model)) in candidates.into_iter().enumerate() {
        let attempt = index as u32 + 1;
        if route_cancelled(state, request_id) {
            let mut result = match last_result {
                Some(result) => result,
                None => failure_request(
                    request_id,
                    &candidate.id,
                    &model,
                    "cancelled",
                    BackendError::new("cancelled", "路由请求已取消"),
                )?,
            };
            result.status = "cancelled".into();
            result.error = Some(BackendError::new("cancelled", "路由请求已取消"));
            result.request_id = request_id.into();
            finish(app, state, request_id, &result)?;
            return Ok(result);
        }
        if mode == "routing" {
            if let Some(error) = circuit_permit(
                &store.conn,
                profile_id(&candidate.id)?,
                settings.circuit.cooldown,
            )? {
                let status = error.code.clone();
                save_attempt(
                    app,
                    state,
                    request_id,
                    attempt,
                    &candidate,
                    &model,
                    None,
                    &status,
                    None,
                    Some(error),
                )?;
                continue;
            }
        }
        let upstream = if index == 0 {
            request_id.to_string()
        } else {
            format!("{request_id}-f{index}")
        };
        save_attempt(
            app,
            state,
            request_id,
            attempt,
            &candidate,
            &model,
            Some(&upstream),
            "running",
            None,
            None,
        )?;
        let result = super::stream_chat_with_policy(
            app,
            state,
            &upstream,
            &candidate.id,
            messages,
            max_tokens,
            Some(&model),
            tools,
            super::openai::StreamPolicy {
                max_attempts: settings.circuit.retries + 1,
                request_timeout: Duration::from_secs(settings.circuit.timeout),
                chunk_timeout: Duration::from_secs(settings.circuit.timeout),
            },
            Some(Arc::clone(&cancel)),
        );
        match result {
            Err(error) => {
                save_attempt(
                    app,
                    state,
                    request_id,
                    attempt,
                    &candidate,
                    &model,
                    Some(&upstream),
                    "failed",
                    None,
                    Some(error.clone()),
                )?;
                finish_error(app, state, request_id, &error)?;
                return Err(error);
            }
            Ok(mut result) => {
                save_attempt(
                    app,
                    state,
                    request_id,
                    attempt,
                    &candidate,
                    &model,
                    Some(&upstream),
                    &result.status,
                    result.latency_ms,
                    result.error.clone(),
                )?;
                record_circuit(
                    app,
                    state,
                    &candidate.id,
                    result.status == "done",
                    result.error.clone(),
                    &settings.circuit,
                )?;
                let retry = mode == "routing"
                    && result.status == "failed"
                    && result.output.is_empty()
                    && result.reasoning.is_empty()
                    && result.tool_calls.is_empty()
                    && result.error.as_ref().is_some_and(can_failover);
                result.request_id = request_id.into();
                if result.status == "done" || result.status == "cancelled" || !retry {
                    finish(app, state, request_id, &result)?;
                    return Ok(result);
                }
                last_result = Some(result);
            }
        }
    }
    let result = match last_result {
        Some(result) => result,
        None => failure_request(
            request_id,
            "",
            &model,
            "failed",
            BackendError::new(
                "all_providers_unavailable",
                "路由队列中的所有上游均处于熔断状态，未发起新的上游请求",
            ),
        )?,
    };
    finish(app, state, request_id, &result)?;
    Ok(result)
}

pub fn cancel(state: &ProviderState, request_id: &str) -> Result<bool> {
    cancel_inner::<tauri::Wry>(None, state, request_id)
}

pub fn cancel_with_app<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    request_id: &str,
) -> Result<bool> {
    cancel_inner(Some(app), state, request_id)
}

fn cancel_inner<R: Runtime>(
    app: Option<&AppHandle<R>>,
    state: &ProviderState,
    request_id: &str,
) -> Result<bool> {
    let flag = state
        .route_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "路由任务状态不可用"))?
        .get(request_id)
        .cloned();
    let Some(flag) = flag else {
        return Ok(false);
    };
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let status = store
        .conn
        .query_row(
            "SELECT status FROM provider_route_runs WHERE request_id = ?1",
            params![request_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if status.as_deref() != Some("running") {
        return Err(BackendError::new(
            "no_active_request",
            "该路由请求没有正在执行的任务",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE provider_route_runs SET status = 'cancelling', updated_at = ?1 WHERE request_id = ?2 AND status = 'running'",
        params![now()?, request_id],
    )?;
    let event = Store::event(
        &tx,
        "provider.route_cancel_requested",
        json!({"requestId": request_id}),
    )?;
    tx.commit()?;
    if let Some(app) = app {
        emit(app, &event);
    }
    let upstream = store
        .conn
        .query_row(
            "SELECT upstream_request_id FROM provider_route_attempts WHERE request_id = ?1 AND upstream_request_id IS NOT NULL ORDER BY attempt DESC LIMIT 1",
            params![request_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    if let Some(upstream) = upstream {
        if let Err(error) = super::cancel_direct_chat(state, &upstream) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    Ok(true)
}

pub fn stop(state: &ProviderState) -> Result<()> {
    let flags = state
        .route_cancels
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "路由任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags {
        flag.store(true, Ordering::Release);
    }
    super::stop_direct_chats(state)?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if state
            .route_cancels
            .lock()
            .map(|cancels| cancels.is_empty())
            .unwrap_or(false)
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new(
        "request_active",
        "路由请求尚未停止，请稍后重试",
    ))
}

pub fn circuits(state: &ProviderState) -> Result<Vec<CircuitSnapshot>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT provider_id, state, failures, opened_at, error_code, error_message FROM provider_circuits ORDER BY provider_id")?;
    let rows = statement.query_map([], |row| {
        let code: Option<String> = row.get(4)?;
        let message: Option<String> = row.get(5)?;
        Ok(CircuitSnapshot {
            provider_id: format!("p{}", row.get::<_, i64>(0)?),
            state: row.get(1)?,
            failures: row.get::<_, i64>(2)? as u32,
            opened_at: row.get(3)?,
            last_error: code
                .map(|code| BackendError::new(&code, message.as_deref().unwrap_or("上游请求失败"))),
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

fn attempt_logs(conn: &Connection, request_id: &str) -> Result<Vec<RouteAttemptLog>> {
    let mut statement = conn.prepare("SELECT attempt, provider_id, model, upstream_request_id, status, latency_ms, error_code, error_message FROM provider_route_attempts WHERE request_id = ?1 ORDER BY attempt")?;
    let rows = statement.query_map(params![request_id], |row| {
        let code: Option<String> = row.get(6)?;
        let message: Option<String> = row.get(7)?;
        Ok(RouteAttemptLog {
            attempt: row.get::<_, i64>(0)? as u32,
            provider_id: format!("p{}", row.get::<_, i64>(1)?),
            model: row.get(2)?,
            upstream_request_id: row.get(3)?,
            status: row.get(4)?,
            latency_ms: row.get(5)?,
            error: code
                .map(|code| BackendError::new(&code, message.as_deref().unwrap_or("上游请求失败"))),
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
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
            attempts: vec![],
            created_at: row.get(8)?,
            updated_at: row.get(9)?,
        })
    })?;
    let mut logs = rows.collect::<std::result::Result<Vec<_>, _>>()?;
    for log in &mut logs {
        log.attempts = attempt_logs(&store.conn, &log.request_id)?;
    }
    Ok(logs)
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &ProviderState) -> Result<()> {
    let store = state.store()?;
    store.conn.execute(
        "UPDATE provider_circuits SET state = 'open' WHERE state = 'half_open'",
        [],
    )?;
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
