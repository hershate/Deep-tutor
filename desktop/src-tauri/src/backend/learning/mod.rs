pub(crate) mod actions;
pub mod commands;
pub(crate) mod knowledge;
pub(crate) mod lecture;
pub(crate) mod pipeline;
mod types;
mod validate;

#[cfg(test)]
mod tests;

pub use types::*;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::materials::MaterialState;
use super::providers::{self, ProviderState};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

pub struct LearningState {
    database_path: PathBuf,
    providers: ProviderState,
    materials: MaterialState,
    active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
    lecture_active: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
    #[cfg(test)]
    defer: Arc<AtomicBool>,
}

impl LearningState {
    pub fn new(database_path: &Path, providers: &ProviderState, materials: &MaterialState) -> Self {
        Self {
            database_path: database_path.to_path_buf(),
            providers: providers.shared(),
            materials: materials.shared(),
            active: Arc::new(Mutex::new(HashMap::new())),
            lecture_active: Arc::new(Mutex::new(HashMap::new())),
            #[cfg(test)]
            _guard: None,
            #[cfg(test)]
            defer: Arc::new(AtomicBool::new(false)),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        let path = guard.path().join("app.db");
        let providers = ProviderState::for_tests_at(&path);
        let materials = MaterialState::new(&path)?;
        Ok(Self {
            database_path: path,
            providers,
            materials,
            active: Arc::new(Mutex::new(HashMap::new())),
            lecture_active: Arc::new(Mutex::new(HashMap::new())),
            _guard: Some(guard),
            defer: Arc::new(AtomicBool::new(false)),
        })
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            providers: self.providers.shared(),
            materials: self.materials.shared(),
            active: Arc::clone(&self.active),
            lecture_active: Arc::clone(&self.lecture_active),
            #[cfg(test)]
            _guard: None,
            #[cfg(test)]
            defer: Arc::clone(&self.defer),
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }

    pub(crate) fn provider_state(&self) -> ProviderState {
        self.providers.shared()
    }

    pub(crate) fn database_path(&self) -> &Path {
        &self.database_path
    }

    pub(crate) fn workspace_path(&self) -> PathBuf {
        super::security::workspace_path(&self.database_path)
    }

    pub(crate) fn material_state(&self) -> MaterialState {
        self.materials.shared()
    }
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub(crate) fn plan_id(value: &str) -> Result<i64> {
    let id = value.strip_prefix("plan-").unwrap_or(value);
    let id = id
        .parse::<i64>()
        .map_err(|_| BackendError::invalid("invalid_plan_id", "学习方案标识无效"))?;
    if id <= 0 {
        return Err(BackendError::invalid("invalid_plan_id", "学习方案标识无效"));
    }
    Ok(id)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS learning_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            input_json TEXT NOT NULL,
            artifacts_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            current_step INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_plan_steps (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            step_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,
            output_json TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, step_index)
        );
        CREATE TABLE IF NOT EXISTS learning_plan_materials (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
            PRIMARY KEY (plan_id, material_id)
        );
        CREATE INDEX IF NOT EXISTS learning_plan_steps_requests ON learning_plan_steps(request_id);
        CREATE TABLE IF NOT EXISTS learning_plan_adoptions (
            plan_id INTEGER PRIMARY KEY REFERENCES learning_plans(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_plan_schedule_tasks (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            task_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            node_ids_json TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            PRIMARY KEY (plan_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS learning_plan_versions (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, version)
        );
        CREATE TABLE IF NOT EXISTS learning_node_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            score REAL NOT NULL,
            max_score REAL NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_reading_progress (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            bookmarked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, stage_id)
        );
        CREATE TABLE IF NOT EXISTS learning_node_tasks (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, node_id)
        );
        CREATE TABLE IF NOT EXISTS learning_lectures (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            current_unit INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, stage_id)
        );
        CREATE TABLE IF NOT EXISTS learning_lecture_units (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            unit_index INTEGER NOT NULL,
            node_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,
            content_json TEXT,
            error_code TEXT,
            error_message TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, stage_id, version, unit_index)
        );
        CREATE TABLE IF NOT EXISTS learning_lecture_versions (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plan_id, stage_id, version)
        );
        CREATE TABLE IF NOT EXISTS learning_annotations (
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (note_id)
        );",
    )?;
    let mut statement = conn.prepare("PRAGMA table_info(learning_node_evidence)")?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if !columns.iter().any(|column| column == "evidence_key") {
        conn.execute(
            "ALTER TABLE learning_node_evidence ADD COLUMN evidence_key TEXT",
            [],
        )?;
    }
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS learning_evidence_key ON learning_node_evidence(plan_id, node_id, evidence_key)",
        [],
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM learning_annotations", [])?;
    conn.execute("DELETE FROM learning_lecture_units", [])?;
    conn.execute("DELETE FROM learning_lecture_versions", [])?;
    conn.execute("DELETE FROM learning_lectures", [])?;
    conn.execute("DELETE FROM learning_node_evidence", [])?;
    conn.execute("DELETE FROM learning_reading_progress", [])?;
    conn.execute("DELETE FROM learning_node_tasks", [])?;
    conn.execute("DELETE FROM learning_plan_schedule_tasks", [])?;
    conn.execute("DELETE FROM learning_plan_adoptions", [])?;
    conn.execute("DELETE FROM learning_plan_versions", [])?;
    conn.execute("DELETE FROM learning_plan_steps", [])?;
    conn.execute("DELETE FROM learning_plan_materials", [])?;
    conn.execute("DELETE FROM learning_plans", [])?;
    Ok(())
}

fn validate_input(state: &LearningState, input: &PlanInput) -> Result<PlanInput> {
    if input.goal.trim().is_empty()
        || input.goal.len() > 1000
        || input.level.trim().is_empty()
        || input.level.len() > 1000
    {
        return Err(BackendError::invalid(
            "invalid_learning_goal",
            "目标与当前基础不能为空且各不能超过 1000 字节",
        ));
    }
    if !(10..=600).contains(&input.daily_minutes) {
        return Err(BackendError::invalid(
            "invalid_daily_minutes",
            "每日时长必须位于 10 到 600 分钟",
        ));
    }
    let _ = validate::deadline(&input.deadline)?;
    if input.material_ids.is_empty() || input.material_ids.len() > 8 {
        return Err(BackendError::invalid(
            "invalid_materials",
            "每个方案须选择 1 到 8 份已解析资料",
        ));
    }
    if input
        .milestone
        .as_deref()
        .is_some_and(|value| value.len() > 1000)
    {
        return Err(BackendError::invalid(
            "invalid_milestone",
            "里程碑说明不能超过 1000 字节",
        ));
    }
    let milestone_date = input
        .milestone_date
        .as_deref()
        .map(validate::deadline)
        .transpose()?
        .map(|date| date.to_string());
    let provider_id = match &input.provider_id {
        Some(id) => id.clone(),
        None => providers::routing::provider_for_task(&state.providers, "plan")?,
    };
    let provider = providers::get(&state.providers, &provider_id)?;
    if !provider.enabled {
        return Err(BackendError::new(
            "provider_disabled",
            "已停用的供应商不能用于生成方案",
        ));
    }
    let mut material_ids = input.material_ids.clone();
    material_ids.sort_unstable();
    material_ids.dedup();
    for id in &material_ids {
        let _ = super::materials::detail(&state.materials, *id)?;
    }
    Ok(PlanInput {
        goal: input.goal.trim().to_string(),
        level: input.level.trim().to_string(),
        daily_minutes: input.daily_minutes,
        deadline: input.deadline.clone(),
        material_ids,
        provider_id: Some(provider.id),
        milestone: input
            .milestone
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .or_else(|| milestone_date.as_ref().map(|_| "最后阶段里程碑".into())),
        milestone_date,
    })
}

fn register(state: &LearningState, id: i64) -> Result<()> {
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "学习生成任务状态不可用"))?;
    if active.contains_key(&id) {
        return Err(BackendError::new(
            "plan_active",
            "该学习方案正在生成，请勿重复启动",
        ));
    }
    active.insert(id, Arc::new(AtomicBool::new(false)));
    Ok(())
}

fn launch<R: Runtime>(app: AppHandle<R>, state: &LearningState, id: i64) -> Result<()> {
    #[cfg(test)]
    if state.defer.load(Ordering::Acquire) {
        return Ok(());
    }
    let worker_state = state.shared();
    let worker_app = app.clone();
    let active = Arc::clone(&state.active);
    super::security::thread_builder()
        .name(format!("learning-plan-{id}"))
        .spawn(move || {
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                pipeline::run(&worker_app, &worker_state, id)
            }));
            match outcome {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = fail_plan(&worker_app, &worker_state, id, error);
                }
                Err(_) => {
                    let error = BackendError::new(
                        "task_panicked",
                        "学习生成任务异常终止，请从已保存步骤恢复",
                    );
                    let _ = fail_plan(&worker_app, &worker_state, id, error);
                }
            }
            if let Ok(mut jobs) = active.lock() {
                jobs.remove(&id);
            }
        })
        .map(|_| ())
        .map_err(|_| BackendError::new("task_start_failed", "无法启动学习生成线程"))
}

pub fn start<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    input: PlanInput,
) -> Result<Plan> {
    let input = validate_input(state, &input)?;
    let store = state.store()?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO learning_plans (status, provider_id, input_json, created_at, updated_at) VALUES ('running', ?1, ?2, ?3, ?3)",
        params![input.provider_id, serde_json::to_string(&input)?, timestamp],
    )?;
    let id = tx.last_insert_rowid();
    for (index, _) in STEP_NAMES.iter().enumerate() {
        tx.execute(
            "INSERT INTO learning_plan_steps (plan_id, step_index, status, created_at, updated_at) VALUES (?1, ?2, 'pending', ?3, ?3)",
            params![id, index as i64, timestamp],
        )?;
    }
    for material_id in &input.material_ids {
        tx.execute(
            "INSERT INTO learning_plan_materials (plan_id, material_id) VALUES (?1, ?2)",
            params![id, material_id],
        )?;
    }
    let event = Store::event(
        &tx,
        "plan.started",
        json!({"planId": format!("plan-{id}"), "providerId": input.provider_id}),
    )?;
    register(state, id)?;
    if let Err(error) = tx.commit() {
        if let Ok(mut active) = state.active.lock() {
            active.remove(&id);
        }
        return Err(error.into());
    }
    emit(app, &event);
    if let Err(error) = launch(app.clone(), state, id) {
        if let Ok(mut active) = state.active.lock() {
            active.remove(&id);
        }
        let task_error = BackendError::new("task_start_failed", "学习生成任务未能启动");
        let _ = fail_plan(app, state, id, task_error);
        return Err(error);
    }
    get(state, &format!("plan-{id}"))
}

pub fn resume<R: Runtime>(app: &AppHandle<R>, state: &LearningState, id: &str) -> Result<Plan> {
    let id = plan_id(id)?;
    let existing = get(state, &format!("plan-{id}"))?;
    if ["done", "degraded"].contains(&existing.status.as_str()) {
        return Ok(existing);
    }
    if !["failed", "cancelled", "interrupted"].contains(&existing.status.as_str()) {
        return Err(BackendError::new(
            "plan_active",
            "只有失败、已取消或中断的方案可以恢复",
        ));
    }
    register(state, id)?;
    let result = (|| -> Result<_> {
        let store = state.store()?;
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE learning_plans SET status = 'running', error_code = NULL, error_message = NULL, updated_at = ?1 WHERE id = ?2", params![now()?, id])?;
        let event = Store::event(&tx, "plan.resumed", json!({"planId": format!("plan-{id}")}))?;
        tx.commit()?;
        Ok(event)
    })();
    match result {
        Ok(event) => {
            emit(app, &event);
            if let Err(error) = launch(app.clone(), state, id) {
                if let Ok(mut active) = state.active.lock() {
                    active.remove(&id);
                }
                let task_error = BackendError::new("task_start_failed", "学习生成任务未能恢复");
                let _ = fail_plan(app, state, id, task_error);
                return Err(error);
            }
            get(state, &format!("plan-{id}"))
        }
        Err(error) => {
            if let Ok(mut active) = state.active.lock() {
                active.remove(&id);
            }
            Err(error)
        }
    }
}

pub fn get(state: &LearningState, id: &str) -> Result<Plan> {
    let id = plan_id(id)?;
    let store = state.store()?;
    let row = store.conn.query_row(
        "SELECT status, provider_id, input_json, artifacts_json, result_json, current_step, version, error_code, error_message, created_at, updated_at FROM learning_plans WHERE id = ?1",
        params![id],
        |row| {
            let input: String = row.get(2)?;
            let artifacts: String = row.get(3)?;
            let result: Option<String> = row.get(4)?;
            let error_code: Option<String> = row.get(7)?;
            let error_message: Option<String> = row.get(8)?;
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                serde_json::from_str::<PlanInput>(&input),
                serde_json::from_str::<Value>(&artifacts),
                result.map(|value| serde_json::from_str::<PlanResult>(&value)),
                row.get::<_, i64>(5)?,
                row.get::<_, i64>(6)?,
                error_code.map(|code| BackendError::new(&code, error_message.as_deref().unwrap_or("学习方案生成失败"))),
                row.get::<_, String>(9)?,
                row.get::<_, String>(10)?,
            ))
        },
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "学习方案不存在"))?;
    let input = row.2?;
    let result = row.4.transpose()?;
    let adopted_project_id = store
        .conn
        .query_row(
            "SELECT project_id FROM learning_plan_adoptions WHERE plan_id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?;
    let mut statement = store.conn.prepare(
        "SELECT step_index, status, attempt, request_id, output_json, error_code, error_message,
            COALESCE((SELECT usage_json FROM provider_requests WHERE request_id = learning_plan_steps.request_id), (SELECT provider_requests.usage_json FROM provider_route_attempts JOIN provider_requests ON provider_requests.request_id = provider_route_attempts.upstream_request_id WHERE provider_route_attempts.request_id = learning_plan_steps.request_id ORDER BY attempt DESC LIMIT 1)),
            COALESCE((SELECT attempts FROM provider_requests WHERE request_id = learning_plan_steps.request_id), (SELECT provider_requests.attempts FROM provider_route_attempts JOIN provider_requests ON provider_requests.request_id = provider_route_attempts.upstream_request_id WHERE provider_route_attempts.request_id = learning_plan_steps.request_id ORDER BY attempt DESC LIMIT 1)),
            COALESCE((SELECT latency_ms FROM provider_requests WHERE request_id = learning_plan_steps.request_id), (SELECT provider_requests.latency_ms FROM provider_route_attempts JOIN provider_requests ON provider_requests.request_id = provider_route_attempts.upstream_request_id WHERE provider_route_attempts.request_id = learning_plan_steps.request_id ORDER BY attempt DESC LIMIT 1))
         FROM learning_plan_steps WHERE plan_id = ?1 ORDER BY step_index",
    )?;
    let rows = statement.query_map(params![id], |row| {
        let index = row.get::<_, i64>(0)? as usize;
        let error_code: Option<String> = row.get(5)?;
        let error_message: Option<String> = row.get(6)?;
        let usage: Option<String> = row.get(7)?;
        let output: Option<String> = row.get(4)?;
        Ok((
            PlanStep {
                index: index as u8,
                name: STEP_NAMES.get(index).unwrap_or(&"未知步骤").to_string(),
                status: row.get(1)?,
                attempt: row.get::<_, i64>(2)? as u32,
                request_id: row.get(3)?,
                output: None,
                error: error_code.map(|code| {
                    BackendError::new(&code, error_message.as_deref().unwrap_or("步骤执行失败"))
                }),
                usage: None,
                upstream_attempts: row.get::<_, Option<i64>>(8)?.map(|value| value as u8),
                latency_ms: row.get(9)?,
            },
            output,
            usage,
        ))
    })?;
    let mut steps = Vec::new();
    for row in rows {
        let (mut step, output, usage) = row?;
        step.output = output
            .map(|value| serde_json::from_str(&value))
            .transpose()?;
        step.usage = usage
            .map(|value| serde_json::from_str(&value))
            .transpose()?
            .flatten();
        steps.push(step);
    }
    let completed = steps.iter().filter(|step| step.status == "done").count();
    Ok(Plan {
        id: format!("plan-{id}"),
        status: row.0,
        provider_id: row.1,
        goal: input.goal,
        level: input.level,
        daily_minutes: input.daily_minutes,
        deadline: input.deadline,
        material_ids: input.material_ids,
        current_step: row.5 as u8,
        progress: (completed * 100 / STEP_NAMES.len()) as u8,
        version: row.6 as u32,
        adopted_project_id,
        steps,
        artifacts: row.3?,
        result,
        error: row.7,
        created_at: row.8,
        updated_at: row.9,
    })
}

pub fn list(state: &LearningState) -> Result<Vec<Plan>> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM learning_plans ORDER BY id DESC LIMIT 100")?;
    let rows = statement.query_map([], |row| row.get::<_, i64>(0))?;
    let mut plans = Vec::new();
    for row in rows {
        plans.push(get(state, &format!("plan-{}", row?))?);
    }
    Ok(plans)
}

fn input(state: &LearningState, id: i64) -> Result<PlanInput> {
    let store = state.store()?;
    let input = store
        .conn
        .query_row(
            "SELECT input_json FROM learning_plans WHERE id = ?1",
            params![id],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "学习方案不存在"))?;
    Ok(serde_json::from_str(&input)?)
}

fn step_state(
    state: &LearningState,
    id: i64,
    index: usize,
) -> Result<(String, u32, Option<String>)> {
    let store = state.store()?;
    store.conn.query_row(
        "SELECT status, attempt, request_id FROM learning_plan_steps WHERE plan_id = ?1 AND step_index = ?2",
        params![id, index as i64],
        |row| Ok((row.get(0)?, row.get::<_, i64>(1)? as u32, row.get(2)?)),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "学习方案步骤不存在"))
}

fn begin_step<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    index: usize,
) -> Result<(u32, Option<String>)> {
    let (status, previous_attempt, previous_request) = step_state(state, id, index)?;
    if status == "done" {
        return Err(BackendError::new(
            "step_completed",
            "该步骤已经完成，不会重复执行",
        ));
    }
    let can_reuse = status == "interrupted"
        && providers::completed_chat(&state.providers, previous_request.as_deref())?;
    let attempt = if status == "running" || can_reuse {
        previous_attempt
    } else {
        previous_attempt
            .checked_add(1)
            .ok_or_else(|| BackendError::new("too_many_attempts", "步骤重试次数已用尽"))?
    };
    let request_id = if [2, 3, 6].contains(&index) {
        if (status == "running" || can_reuse) && previous_request.is_some() {
            previous_request
        } else {
            Some(format!("plan-{id}-{}-{attempt}", index + 1))
        }
    } else {
        None
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE learning_plan_steps SET status = 'running', attempt = ?1, request_id = ?2, output_json = NULL, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE plan_id = ?4 AND step_index = ?5",
        params![attempt, request_id, now()?, id, index as i64],
    )?;
    tx.execute("UPDATE learning_plans SET status = 'running', current_step = ?1, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE id = ?3", params![index as i64, now()?, id])?;
    let event = Store::event(
        &tx,
        "plan.step_started",
        json!({
            "planId": format!("plan-{id}"), "stepIndex": index, "step": STEP_NAMES[index],
            "attempt": attempt, "requestId": request_id, "progress": index * 100 / STEP_NAMES.len(),
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok((attempt, request_id))
}

fn complete_step<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    index: usize,
    output: Value,
) -> Result<()> {
    let store = state.store()?;
    let serialized = serde_json::to_string(&output)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE learning_plan_steps SET status = 'done', output_json = ?1, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE plan_id = ?3 AND step_index = ?4",
        params![serialized, now()?, id, index as i64],
    )?;
    tx.execute("UPDATE learning_plans SET artifacts_json = json_set(artifacts_json, ?1, json(?2)), updated_at = ?3 WHERE id = ?4", params![format!("$.step{index}"), serialized, now()?, id])?;
    let event = Store::event(
        &tx,
        "plan.step_completed",
        json!({
            "planId": format!("plan-{id}"), "stepIndex": index, "step": STEP_NAMES[index],
            "progress": (index + 1) * 100 / STEP_NAMES.len(),
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn fail_step<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    index: usize,
    error: &BackendError,
) -> Result<()> {
    let status = if error.code == "cancelled" {
        "cancelled"
    } else {
        "failed"
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE learning_plan_steps SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND step_index = ?6",
        params![status, error.code, error.message, now()?, id, index as i64],
    )?;
    let event = Store::event(
        &tx,
        "plan.step_failed",
        json!({
            "planId": format!("plan-{id}"), "stepIndex": index, "step": STEP_NAMES[index], "error": error,
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn fail_plan<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    error: BackendError,
) -> Result<()> {
    let status = if error.code == "cancelled" {
        "cancelled"
    } else {
        "failed"
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_plans SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "plan.cancelled"
        } else {
            "plan.failed"
        },
        json!({"planId": format!("plan-{id}"), "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn complete_plan<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    result: &PlanResult,
) -> Result<()> {
    let status = if result.validation.passed && result.warnings.is_empty() {
        "done"
    } else {
        "degraded"
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_plans SET status = ?1, result_json = ?2, current_step = 7, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE id = ?4", params![status, serde_json::to_string(result)?, now()?, id])?;
    let event = Store::event(
        &tx,
        if status == "done" {
            "plan.completed"
        } else {
            "plan.degraded"
        },
        json!({
            "planId": format!("plan-{id}"), "status": status, "progress": 100,
            "validation": result.validation, "warnings": result.warnings,
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel<R: Runtime>(app: &AppHandle<R>, state: &LearningState, id: &str) -> Result<Plan> {
    let id = plan_id(id)?;
    let flag = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "学习生成任务状态不可用"))?
        .get(&id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_plan", "该方案没有正在执行的任务"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    // 只把运行中的方案翻成 cancelling：终态竞态窗口内不覆盖结果（与 practice/knowledge 守卫一致）
    let changed = tx.execute(
        "UPDATE learning_plans SET status = 'cancelling', updated_at = ?1 WHERE id = ?2 AND status = 'running'",
        params![now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "no_active_plan",
            "该方案没有正在执行的任务，不能重复取消",
        ));
    }
    let event = Store::event(
        &tx,
        "plan.cancel_requested",
        json!({"planId": format!("plan-{id}")}),
    )?;
    tx.commit()?;
    emit(app, &event);
    let request = store.conn.query_row("SELECT request_id FROM learning_plan_steps WHERE plan_id = ?1 AND status = 'running' AND request_id IS NOT NULL", params![id], |row| row.get::<_, String>(0)).optional()?;
    if let Some(request) = request {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    get(state, &format!("plan-{id}"))
}

pub fn stop(state: &LearningState) -> Result<()> {
    let flags = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "学习生成任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    let lecture_flags = state
        .lecture_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "讲义任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags.into_iter().chain(lecture_flags) {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT request_id FROM learning_plan_steps WHERE status = 'running' AND request_id IS NOT NULL")?;
    let requests = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut statement = store.conn.prepare("SELECT request_id FROM learning_lecture_units WHERE status = 'running' AND request_id IS NOT NULL")?;
    let lecture_requests = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for request in requests.into_iter().chain(lecture_requests) {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        let plans_idle = state
            .active
            .lock()
            .map(|active| active.is_empty())
            .unwrap_or(false);
        let lectures_idle = state
            .lecture_active
            .lock()
            .map(|active| active.is_empty())
            .unwrap_or(false);
        if plans_idle && lectures_idle {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new(
        "plan_active",
        "学习生成或讲义任务尚未停止，请稍后重试",
    ))
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &LearningState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id, status FROM learning_plans WHERE status IN ('running', 'cancelling')",
    )?;
    let plans = statement
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, previous_status) in plans {
        let cancelled = previous_status == "cancelling";
        let status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let error = if cancelled {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的生成任务")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试生成任务，可从已保存步骤恢复",
            )
        };
        let step_status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE learning_plan_steps SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND status = 'running'", params![step_status, error.code, error.message, now()?, id])?;
        tx.execute("UPDATE learning_plans SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
        let event = Store::event(
            &tx,
            if cancelled {
                "plan.cancelled"
            } else {
                "plan.interrupted"
            },
            json!({"planId": format!("plan-{id}"), "error": error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    lecture::recover(app, state)
}
