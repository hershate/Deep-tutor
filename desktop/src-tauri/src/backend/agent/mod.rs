pub mod commands;
pub(crate) mod tools;

#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{self, LearningState};
use super::providers::tools::{ToolCall, ToolCallFunction, ToolCallRequest};
use super::providers::{self, ChatMessage, ProviderState};
use super::store::Store;
use rusqlite::{params, OptionalExtension};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const AGENT_SYSTEM: &str = "你是知舟 Deep Tutor 的 Agent。你必须依据真实工具结果回答，不得声称未执行的读取、写入、检索或提升已经完成。只在需要时调用工具；严格使用工具给定的结构化参数。工作区修改先保存为 scratch，正式提升必须等待用户批准。记忆只通过工具按需保存、召回和删除，不要自动注入。无法执行的能力必须明确报告，不得编造链接、引用、成本或学习效果。";

pub struct AgentState {
    pub(crate) database_path: PathBuf,
    pub(crate) providers: ProviderState,
    pub(crate) learning: LearningState,
    pub(crate) active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
    #[cfg(test)]
    defer: Arc<AtomicBool>,
}

impl AgentState {
    pub fn new(path: &Path, providers: &ProviderState, learning: &LearningState) -> Self {
        Self {
            database_path: path.to_path_buf(),
            providers: providers.shared(),
            learning: learning.shared(),
            active: Arc::new(Mutex::new(HashMap::new())),
            #[cfg(test)]
            defer: Arc::new(AtomicBool::new(false)),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let learning = LearningState::for_tests()?;
        let providers = learning.provider_state();
        let path = learning.database_path().to_path_buf();
        Ok(Self {
            database_path: path,
            providers,
            learning,
            active: Arc::new(Mutex::new(HashMap::new())),
            defer: Arc::new(AtomicBool::new(false)),
        })
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            providers: self.providers.shared(),
            learning: self.learning.shared(),
            active: Arc::clone(&self.active),
            #[cfg(test)]
            defer: Arc::clone(&self.defer),
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentMessage {
    pub id: i64,
    pub role: String,
    pub content: String,
    pub reasoning: String,
    pub tool_calls: Vec<ToolCall>,
    pub tool_call_id: Option<String>,
    pub name: Option<String>,
    pub step_index: u32,
    pub partial: bool,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentRun {
    pub id: i64,
    pub status: String,
    pub steps: u32,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub current_request_id: Option<String>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentArtifact {
    pub id: i64,
    pub path: String,
    pub plan_id: String,
    pub stage_id: String,
    pub unit_index: usize,
    pub status: String,
    pub content: Value,
    pub created_at: String,
    pub expires_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentApproval {
    pub id: i64,
    pub kind: String,
    pub payload: Value,
    pub status: String,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSession {
    pub id: i64,
    pub title: String,
    pub plan_id: String,
    pub provider_id: String,
    pub budget_tokens: u64,
    pub output_tokens_used: u64,
    pub status: String,
    pub messages: Vec<AgentMessage>,
    pub runs: Vec<AgentRun>,
    pub artifacts: Vec<AgentArtifact>,
    pub approvals: Vec<AgentApproval>,
    pub memory: Vec<(String, String)>,
    pub updated_at: String,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub(crate) fn numeric_id(value: &str) -> Result<i64> {
    let value = value.strip_prefix("agent-").unwrap_or(value);
    let id = value
        .parse::<i64>()
        .map_err(|_| BackendError::invalid("invalid_agent_id", "Agent 会话标识无效"))?;
    if id <= 0 {
        return Err(BackendError::invalid(
            "invalid_agent_id",
            "Agent 会话标识无效",
        ));
    }
    Ok(id)
}

pub fn migrate(conn: &rusqlite::Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            budget_tokens INTEGER NOT NULL DEFAULT 16000,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            steps INTEGER NOT NULL DEFAULT 0,
            attempt INTEGER NOT NULL DEFAULT 1,
            current_request_id TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            step_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            reasoning TEXT NOT NULL DEFAULT '',
            tool_calls_json TEXT,
            tool_call_id TEXT,
            name TEXT,
            partial INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_tool_calls (
            id TEXT PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            step_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            unit_index INTEGER NOT NULL,
            content_json TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );",
    )?;
    Ok(())
}

pub fn clear_all(conn: &rusqlite::Connection) -> Result<()> {
    for table in [
        "agent_messages",
        "agent_tool_calls",
        "agent_runs",
        "agent_artifacts",
        "agent_approvals",
        "agent_memory",
        "agent_sessions",
    ] {
        conn.execute(&format!("DELETE FROM {table}"), [])?;
    }
    Ok(())
}

pub fn new_session<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    title: Option<String>,
    plan_id: Option<String>,
    budget_tokens: Option<u64>,
) -> Result<AgentSession> {
    let title = title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .unwrap_or("新学习会话");
    if title.len() > 160 {
        return Err(BackendError::invalid(
            "invalid_session_title",
            "会话标题不能超过 160 字节",
        ));
    }
    let budget = budget_tokens.unwrap_or(16_000);
    if !(1024..=65_536).contains(&budget) {
        return Err(BackendError::invalid(
            "invalid_token_budget",
            "会话 Token 预算必须位于 1024 到 65536",
        ));
    }
    let plan_id = match plan_id {
        Some(id) => id,
        None => learning::list(&state.learning)?
            .into_iter()
            .find(|plan| plan.adopted_project_id.is_some())
            .map(|plan| plan.id)
            .ok_or_else(|| {
                BackendError::new(
                    "workspace_unavailable",
                    "请先采用一个学习方案，再创建 Agent 会话",
                )
            })?,
    };
    let plan = learning::get(&state.learning, &plan_id)?;
    if plan.adopted_project_id.is_none() {
        return Err(BackendError::new(
            "workspace_unavailable",
            "Agent 工作区必须来自已采用的方案",
        ));
    }
    let provider_id = providers::routing::provider_for_task(&state.providers, "agent")?;
    let store = state.store()?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO agent_sessions (title, plan_id, provider_id, budget_tokens, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?5)", params![title, plan_id, provider_id, budget as i64, timestamp])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "agent.session_created",
        json!({"sessionId": id, "planId": plan_id, "providerId": provider_id, "budgetTokens": budget}),
    )?;
    tx.commit()?;
    emit(app, &event);
    get(state, &id.to_string())
}

pub fn get(state: &AgentState, id: &str) -> Result<AgentSession> {
    let id = numeric_id(id)?;
    let store = state.store()?;
    let (title, plan_id, provider_id, budget_tokens, status, updated_at) = store.conn.query_row("SELECT title, plan_id, provider_id, budget_tokens, status, updated_at FROM agent_sessions WHERE id = ?1", params![id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, i64>(3)? as u64, row.get::<_, String>(4)?, row.get::<_, String>(5)?))).optional()?.ok_or_else(|| BackendError::new("not_found", "Agent 会话不存在"))?;
    let mut statement = store.conn.prepare("SELECT id, role, content, reasoning, tool_calls_json, tool_call_id, name, step_index, partial, created_at FROM agent_messages WHERE session_id = ?1 ORDER BY id DESC LIMIT 100")?;
    let rows = statement.query_map(params![id], |row| {
        let calls: Option<String> = row.get(4)?;
        Ok(AgentMessage {
            id: row.get(0)?,
            role: row.get(1)?,
            content: row.get(2)?,
            reasoning: row.get(3)?,
            tool_calls: calls
                .map(|value| serde_json::from_str(&value))
                .transpose()
                .unwrap_or_default()
                .unwrap_or_default(),
            tool_call_id: row.get(5)?,
            name: row.get(6)?,
            step_index: row.get::<_, i64>(7)? as u32,
            partial: row.get(8)?,
            created_at: row.get(9)?,
        })
    })?;
    let mut messages = rows.collect::<std::result::Result<Vec<_>, _>>()?;
    messages.reverse();
    let mut statement = store.conn.prepare("SELECT id, status, steps, input_tokens, output_tokens, current_request_id, error_code, error_message, created_at, updated_at FROM agent_runs WHERE session_id = ?1 ORDER BY id DESC LIMIT 10")?;
    let runs = statement
        .query_map(params![id], |row| {
            let code: Option<String> = row.get(6)?;
            let message: Option<String> = row.get(7)?;
            Ok(AgentRun {
                id: row.get(0)?,
                status: row.get(1)?,
                steps: row.get::<_, i64>(2)? as u32,
                input_tokens: row.get::<_, i64>(3)? as u64,
                output_tokens: row.get::<_, i64>(4)? as u64,
                current_request_id: row.get(5)?,
                error: code.map(|code| {
                    BackendError::new(&code, message.as_deref().unwrap_or("Agent 运行失败"))
                }),
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut statement = store.conn.prepare("SELECT id, path, plan_id, stage_id, unit_index, status, content_json, created_at, expires_at FROM agent_artifacts WHERE session_id = ?1 ORDER BY id DESC")?;
    let artifacts = statement
        .query_map(params![id], |row| {
            let content: String = row.get(6)?;
            Ok(AgentArtifact {
                id: row.get(0)?,
                path: row.get(1)?,
                plan_id: row.get(2)?,
                stage_id: row.get(3)?,
                unit_index: row.get::<_, i64>(4)? as usize,
                status: row.get(5)?,
                content: serde_json::from_str(&content).map_err(|error| {
                    rusqlite::Error::FromSqlConversionFailure(
                        6,
                        rusqlite::types::Type::Text,
                        Box::new(error),
                    )
                })?,
                created_at: row.get(7)?,
                expires_at: row.get(8)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut statement = store.conn.prepare("SELECT id, kind, payload_json, status, created_at FROM agent_approvals WHERE session_id = ?1 AND status = 'pending' ORDER BY id")?;
    let approvals = statement
        .query_map(params![id], |row| {
            let payload: String = row.get(2)?;
            Ok(AgentApproval {
                id: row.get(0)?,
                kind: row.get(1)?,
                payload: serde_json::from_str(&payload).unwrap_or(Value::Null),
                status: row.get(3)?,
                created_at: row.get(4)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut statement = store
        .conn
        .prepare("SELECT key, value FROM agent_memory ORDER BY key LIMIT 100")?;
    let memory = statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    Ok(AgentSession {
        id,
        title,
        plan_id,
        provider_id,
        budget_tokens,
        output_tokens_used: session_output_tokens(state, id)?,
        status,
        messages,
        runs,
        artifacts,
        approvals,
        memory,
        updated_at,
    })
}

pub fn list(state: &AgentState) -> Result<Vec<AgentSession>> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM agent_sessions ORDER BY updated_at DESC LIMIT 50")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    ids.into_iter()
        .map(|id| get(state, &id.to_string()))
        .collect()
}

fn save_message<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session_id: i64,
    run_id: i64,
    step: u32,
    role: &str,
    content: String,
    reasoning: String,
    calls: &[ToolCall],
    tool_call_id: Option<String>,
    name: Option<String>,
    partial: bool,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO agent_messages (session_id, run_id, step_index, role, content, reasoning, tool_calls_json, tool_call_id, name, partial, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)", params![session_id, run_id, step, role, content, reasoning, serde_json::to_string(calls)?, tool_call_id, name, partial, now()?])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "agent.message_saved",
        json!({"sessionId": session_id, "runId": run_id, "messageId": id, "role": role, "partial": partial}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn launch<R: Runtime>(
    app: AppHandle<R>,
    state: &AgentState,
    run_id: i64,
    session_id: i64,
) -> Result<()> {
    #[cfg(test)]
    if state.defer.load(Ordering::Acquire) {
        return Ok(());
    }
    let worker_app = app.clone();
    let worker_state = state.shared();
    let worker_active = Arc::clone(&state.active);
    let result = std::thread::Builder::new()
        .name(format!("agent-run-{run_id}"))
        .spawn(move || {
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run_loop(&worker_app, &worker_state, run_id, session_id)
            }));
            match outcome {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = finish_run(
                        &worker_app,
                        &worker_state,
                        run_id,
                        if error.code == "cancelled" {
                            "cancelled"
                        } else {
                            "failed"
                        },
                        Some(error),
                    );
                }
                Err(_) => {
                    let _ = finish_run(
                        &worker_app,
                        &worker_state,
                        run_id,
                        "failed",
                        Some(BackendError::new(
                            "task_panicked",
                            "Agent 运行异常终止，请从已保存事件恢复",
                        )),
                    );
                }
            }
            if let Ok(mut active) = worker_active.lock() {
                active.remove(&run_id);
            }
        });
    if result.is_err() {
        if let Ok(mut active) = state.active.lock() {
            active.remove(&run_id);
        }
        let _ = finish_run(
            &app,
            state,
            run_id,
            "failed",
            Some(BackendError::new(
                "task_start_failed",
                "Agent 任务未能启动，可恢复运行",
            )),
        );
        return Err(BackendError::new(
            "task_start_failed",
            "无法启动 Agent 任务",
        ));
    }
    Ok(())
}

pub fn send<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session_id: &str,
    content: String,
) -> Result<AgentRun> {
    let session_id = numeric_id(session_id)?;
    let content = content.trim().to_string();
    if content.is_empty() || content.len() > 32_000 {
        return Err(BackendError::invalid(
            "invalid_agent_message",
            "消息不能为空且不能超过 32000 字节",
        ));
    }
    let session = get(state, &session_id.to_string())?;
    if session.output_tokens_used >= session.budget_tokens {
        return Err(BackendError::new(
            "budget_exceeded",
            "已达到本会话 Token 预算，未创建新的运行",
        ));
    }
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?;
    let mut store = state.store()?;
    let timestamp = now()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let running: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM agent_runs WHERE session_id = ?1 AND status IN ('running', 'cancelling'))",
        params![session_id],
        |row| row.get(0),
    )?;
    if running {
        return Err(BackendError::new(
            "run_active",
            "该 Agent 会话已有正在执行的任务",
        ));
    }
    tx.execute("INSERT INTO agent_runs (session_id, status, created_at, updated_at) VALUES (?1, 'running', ?2, ?2)", params![session_id, timestamp])?;
    let run_id = tx.last_insert_rowid();
    tx.execute("INSERT INTO agent_messages (session_id, run_id, step_index, role, content, created_at) VALUES (?1, ?2, 0, 'user', ?3, ?4)", params![session_id, run_id, content, timestamp])?;
    tx.execute("UPDATE agent_sessions SET title = CASE WHEN title = '新学习会话' THEN ?1 ELSE title END, updated_at = ?2 WHERE id = ?3", params![content.chars().take(60).collect::<String>(), timestamp, session_id])?;
    let event = Store::event(
        &tx,
        "agent.run_started",
        json!({"sessionId": session_id, "runId": run_id}),
    )?;
    active.insert(run_id, Arc::new(AtomicBool::new(false)));
    if let Err(error) = tx.commit() {
        active.remove(&run_id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, run_id, session_id)?;
    get_run(state, run_id)
}

fn session_output_tokens(state: &AgentState, session_id: i64) -> Result<u64> {
    let store = state.store()?;
    Ok(store.conn.query_row(
        "SELECT COALESCE(SUM(CAST(json_extract(provider_requests.usage_json, '$.outputTokens') AS INTEGER)), 0) FROM agent_runs JOIN provider_requests ON provider_requests.request_id GLOB 'agent-' || agent_runs.id || '-*' WHERE agent_runs.session_id = ?1",
        params![session_id],
        |row| row.get::<_, i64>(0),
    )? as u64)
}

fn get_run(state: &AgentState, id: i64) -> Result<AgentRun> {
    let store = state.store()?;
    store.conn.query_row("SELECT id, status, steps, input_tokens, output_tokens, current_request_id, error_code, error_message, created_at, updated_at FROM agent_runs WHERE id = ?1", params![id], |row| {
        let code: Option<String> = row.get(6)?;
        let message: Option<String> = row.get(7)?;
        Ok(AgentRun { id: row.get(0)?, status: row.get(1)?, steps: row.get::<_, i64>(2)? as u32, input_tokens: row.get::<_, i64>(3)? as u64, output_tokens: row.get::<_, i64>(4)? as u64, current_request_id: row.get(5)?, error: code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("Agent 运行失败"))), created_at: row.get(8)?, updated_at: row.get(9)? })
    }).optional()?.ok_or_else(|| BackendError::new("not_found", "Agent 运行不存在"))
}

fn history(session: &AgentSession) -> Result<Vec<ChatMessage>> {
    let mut messages = vec![
        ChatMessage {
            role: "system".into(),
            content: AGENT_SYSTEM.into(),
            ..Default::default()
        },
        ChatMessage {
            role: "user".into(),
            content: format!(
                "当前工作区方案为 {}。只在需要时读取资料或编辑产物，不能凭空声称已修改。",
                session.plan_id
            ),
            ..Default::default()
        },
    ];
    for message in &session.messages {
        let calls = message
            .tool_calls
            .iter()
            .map(|call| ToolCallRequest {
                id: call.id.clone(),
                kind: "function".into(),
                function: ToolCallFunction {
                    name: call.name.clone(),
                    arguments: call.arguments.clone(),
                },
            })
            .collect::<Vec<_>>();
        messages.push(ChatMessage {
            role: message.role.clone(),
            content: message.content.clone(),
            tool_calls: (!calls.is_empty()).then_some(calls),
            tool_call_id: message.tool_call_id.clone(),
            name: message.name.clone(),
        });
    }
    Ok(messages)
}

fn begin_step<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    run: &AgentRun,
) -> Result<(u32, String)> {
    let store = state.store()?;
    let completed: bool = store.conn.query_row("SELECT EXISTS(SELECT 1 FROM agent_messages WHERE run_id = ?1 AND step_index = ?2 AND role = 'assistant')", params![run.id, run.steps], |row| row.get(0))?;
    let step = if completed {
        run.steps + 1
    } else {
        run.steps.max(1)
    };
    if step > 25 {
        return Err(BackendError::new(
            "step_limit",
            "Agent 已达到 25 步工具循环上限",
        ));
    }
    let (current, status): (Option<String>, String) = store.conn.query_row(
        "SELECT current_request_id, status FROM agent_runs WHERE id = ?1",
        params![run.id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if status != "running" {
        return Err(BackendError::new("cancelled", "Agent 运行已取消"));
    }
    let previous_attempt = store.conn.query_row(
        "SELECT attempt FROM agent_runs WHERE id = ?1",
        params![run.id],
        |row| row.get::<_, i64>(0),
    )? as u32;
    let retry = !completed && current.is_some();
    let reusable = retry
        && current
            .as_deref()
            .map(|request| {
                providers::get_chat(&state.providers, request)
                    .map(|result| result.status == "done")
                    .unwrap_or(false)
            })
            .unwrap_or(false);
    let attempt = if retry && !reusable {
        previous_attempt + 1
    } else {
        previous_attempt
    };
    let request_id = if reusable {
        current.unwrap()
    } else {
        format!("agent-{}-{attempt}-{step}", run.id)
    };
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE agent_runs SET steps = ?1, attempt = ?2, current_request_id = ?3, updated_at = ?4 WHERE id = ?5", params![step, attempt, request_id, now()?, run.id])?;
    let event = Store::event(
        &tx,
        "agent.step_started",
        json!({"runId": run.id, "stepIndex": step, "requestId": request_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok((step, request_id))
}

fn finish_run<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    id: i64,
    status: &str,
    error: Option<BackendError>,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE agent_runs SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.as_ref().map(|error| &error.code), error.as_ref().map(|error| &error.message), now()?, id])?;
    let event = Store::event(
        &tx,
        match status {
            "done" => "agent.run_completed",
            "cancelled" => "agent.run_cancelled",
            "degraded" => "agent.run_degraded",
            _ => "agent.run_failed",
        },
        json!({"runId": id, "status": status, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn run_loop<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    run_id: i64,
    session_id: i64,
) -> Result<()> {
    let session = get(state, &session_id.to_string())?;
    let mut messages = history(&session)?;
    let catalog = tools::catalog();
    loop {
        let run = get_run(state, run_id)?;
        if state
            .active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?
            .get(&run_id)
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            return Err(BackendError::new("cancelled", "Agent 运行已取消"));
        }
        if session_output_tokens(state, session_id)? >= session.budget_tokens {
            return Err(BackendError::new(
                "budget_exceeded",
                "已达到本会话 Token 预算，未再发起新的模型请求",
            ));
        }
        let (step, request_id) = begin_step(app, state, &run)?;
        let request = providers::routing::stream_chat(
            app,
            &state.providers,
            &request_id,
            "agent",
            Some(&session.provider_id),
            &messages,
            4096,
            None,
            Some(&catalog),
            true,
        )?;
        let store = state.store()?;
        let (input_tokens, output_tokens): (u64, u64) = store.conn.query_row(
            "SELECT COALESCE(SUM(CAST(json_extract(usage_json, '$.inputTokens') AS INTEGER)), 0), COALESCE(SUM(CAST(json_extract(usage_json, '$.outputTokens') AS INTEGER)), 0) FROM provider_requests WHERE request_id GLOB ?1",
            params![format!("agent-{run_id}-*")],
            |row| Ok((row.get::<_, i64>(0)? as u64, row.get::<_, i64>(1)? as u64)),
        )?;
        store.conn.execute("UPDATE agent_runs SET input_tokens = ?1, output_tokens = ?2, updated_at = ?3 WHERE id = ?4", params![input_tokens as i64, output_tokens as i64, now()?, run_id])?;
        if request.status != "done" {
            save_message(
                app,
                state,
                session_id,
                run_id,
                step,
                "assistant",
                request.output.clone(),
                request.reasoning.clone(),
                &[],
                None,
                None,
                true,
            )?;
            return Err(request
                .error
                .unwrap_or_else(|| BackendError::new("provider_error", "模型请求未完成")));
        }
        save_message(
            app,
            state,
            session_id,
            run_id,
            step,
            "assistant",
            request.output.clone(),
            request.reasoning.clone(),
            &request.tool_calls,
            None,
            None,
            false,
        )?;
        if session_output_tokens(state, session_id)? >= session.budget_tokens {
            return Err(BackendError::new(
                "budget_exceeded",
                "已达到本会话 Token 预算，未再发起新的模型请求",
            ));
        }
        if request.tool_calls.is_empty() {
            return finish_run(app, state, run_id, "done", None);
        }
        if request.tool_calls.len() > 8 {
            return Err(BackendError::new(
                "too_many_tool_calls",
                "单步工具调用超过 8 个上限",
            ));
        }
        for call in &request.tool_calls {
            if state
                .active
                .lock()
                .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?
                .get(&run_id)
                .is_some_and(|flag| flag.load(Ordering::Acquire))
            {
                return Err(BackendError::new("cancelled", "Agent 运行已取消"));
            }
            let outcome = tools::execute(app, state, run_id, &session, call, step)?;
            let content = serde_json::to_string(&outcome)?;
            save_message(
                app,
                state,
                session_id,
                run_id,
                step,
                "tool",
                content.clone(),
                String::new(),
                &[],
                Some(call.id.clone()),
                Some(call.name.clone()),
                false,
            )?;
            messages.push(ChatMessage {
                role: "tool".into(),
                content,
                tool_call_id: Some(call.id.clone()),
                name: Some(call.name.clone()),
                ..Default::default()
            });
        }
        if session_output_tokens(state, session_id)? >= session.budget_tokens {
            return Err(BackendError::new(
                "budget_exceeded",
                "已达到本会话 Token 预算，未再发起新的模型请求",
            ));
        }
    }
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    run_id: &str,
) -> Result<AgentRun> {
    let run_id = numeric_id(run_id)?;
    let flag = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?
        .get(&run_id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_run", "该 Agent 任务没有正在执行"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE agent_runs SET status = 'cancelling', updated_at = ?1 WHERE id = ?2",
        params![now()?, run_id],
    )?;
    let event = Store::event(&tx, "agent.run_cancel_requested", json!({"runId": run_id}))?;
    tx.commit()?;
    emit(app, &event);
    let request = store.conn.query_row(
        "SELECT current_request_id FROM agent_runs WHERE id = ?1",
        params![run_id],
        |row| row.get::<_, Option<String>>(0),
    )?;
    if let Some(request) = request {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    get_run(state, run_id)
}

pub fn resume<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    run_id: &str,
) -> Result<AgentRun> {
    let run_id = numeric_id(run_id)?;
    let run = get_run(state, run_id)?;
    if !["failed", "cancelled", "interrupted"].contains(&run.status.as_str()) {
        return Ok(run);
    }
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let session_id = tx.query_row(
        "SELECT session_id FROM agent_runs WHERE id = ?1 AND status IN ('failed', 'cancelled', 'interrupted')",
        params![run_id],
        |row| row.get::<_, i64>(0),
    ).optional()?.ok_or_else(|| BackendError::new("run_active", "Agent 任务状态已改变，不能重复恢复"))?;
    let running: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM agent_runs WHERE session_id = ?1 AND status IN ('running', 'cancelling'))",
        params![session_id],
        |row| row.get(0),
    )?;
    if running || active.contains_key(&run_id) {
        return Err(BackendError::new(
            "run_active",
            "该 Agent 会话已有正在执行的任务",
        ));
    }
    active.insert(run_id, Arc::new(AtomicBool::new(false)));
    tx.execute("UPDATE agent_runs SET status = 'running', error_code = NULL, error_message = NULL, updated_at = ?1 WHERE id = ?2", params![now()?, run_id])?;
    let event = Store::event(&tx, "agent.run_resumed", json!({"runId": run_id}))?;
    if let Err(error) = tx.commit() {
        active.remove(&run_id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, run_id, session_id)?;
    get_run(state, run_id)
}

pub fn stop(state: &AgentState) -> Result<()> {
    let flags = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Agent 任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT current_request_id FROM agent_runs WHERE status IN ('running', 'cancelling') AND current_request_id IS NOT NULL")?;
    let requests = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for request in requests {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if state
            .active
            .lock()
            .map(|active| active.is_empty())
            .unwrap_or(false)
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new(
        "run_active",
        "Agent 任务尚未停止，请稍后重试",
    ))
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &AgentState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id, status FROM agent_runs WHERE status IN ('running', 'cancelling')")?;
    let runs = statement
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, previous) in runs {
        let cancelled = previous == "cancelling";
        let status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let error = if cancelled {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的 Agent 任务")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试 Agent 任务，可恢复运行",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE agent_runs SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
        let event = Store::event(
            &tx,
            if cancelled {
                "agent.run_cancelled"
            } else {
                "agent.run_interrupted"
            },
            json!({"runId": id, "error": error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn delete_session<R: Runtime>(app: &AppHandle<R>, state: &AgentState, id: &str) -> Result<()> {
    let id = numeric_id(id)?;
    let store = state.store()?;
    let running: bool = store.conn.query_row("SELECT EXISTS(SELECT 1 FROM agent_runs WHERE session_id = ?1 AND status IN ('running', 'cancelling'))", params![id], |row| row.get(0))?;
    if running {
        return Err(BackendError::new(
            "run_active",
            "请先取消并等待 Agent 任务结束，再删除会话",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("DELETE FROM agent_sessions WHERE id = ?1", params![id])?;
    let event = Store::event(&tx, "agent.session_deleted", json!({"sessionId": id}))?;
    tx.commit()?;
    emit(app, &event);
    let root = state
        .database_path
        .parent()
        .unwrap_or(Path::new("."))
        .join("agent-workspaces")
        .join(id.to_string());
    if root.exists() {
        std::fs::remove_dir_all(root).map_err(|_| {
            BackendError::new("cleanup_failed", "会话已删除，但 scratch 文件清理失败")
        })?;
    }
    Ok(())
}
