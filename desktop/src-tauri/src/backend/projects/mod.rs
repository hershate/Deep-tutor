pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{self, LearningState};
use super::providers::{self, ChatMessage, ProviderState};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[cfg(test)]
use std::path::Path;
use std::path::PathBuf;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const FEEDBACK_SYSTEM: &str = "你是项目量规评估器。只能依据用户提交的项目说明、成果正文与给定资料评估，不得声称运行代码或验证未提供的事实。只输出一个 JSON 对象，不要 Markdown 或额外解释。";

pub struct ProjectState {
    database_path: PathBuf,
    providers: ProviderState,
}

impl ProjectState {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            database_path: learning.database_path().to_path_buf(),
            providers: learning.provider_state(),
        }
    }

    #[cfg(test)]
    pub fn for_tests(path: &Path, providers: &ProviderState) -> Self {
        Self {
            database_path: path.to_path_buf(),
            providers: providers.shared(),
        }
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            providers: self.providers.shared(),
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }

    fn materials(&self) -> Result<super::materials::MaterialState> {
        super::materials::MaterialState::new(&self.database_path)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProjectFeedback {
    pub concept: u8,
    pub implementation: u8,
    pub verification: u8,
    pub summary: String,
    pub suggestions: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectVersion {
    pub id: i64,
    pub project_id: i64,
    pub version_number: u32,
    pub content: String,
    pub milestone_id: Option<i64>,
    pub milestone_title: Option<String>,
    pub material_id: Option<i64>,
    pub status: String,
    pub attempt: u32,
    pub provider_id: String,
    pub request_id: Option<String>,
    pub feedback: Option<ProjectFeedback>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectWorkspace {
    pub versions: Vec<ProjectVersion>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SubmissionInput {
    pub project_id: i64,
    pub content: String,
    pub milestone_id: Option<i64>,
    pub material_id: Option<i64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct IterationNoteInput {
    pub version_id: i64,
    pub content: String,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS project_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            content TEXT NOT NULL,
            milestone_id INTEGER,
            milestone_title TEXT,
            material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            provider_id TEXT NOT NULL,
            request_id TEXT,
            feedback_json TEXT,
            error_code TEXT,
            error_message TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(project_id, version_number)
        );
        CREATE INDEX IF NOT EXISTS project_versions_project ON project_versions(project_id, version_number DESC);
        CREATE INDEX IF NOT EXISTS project_versions_requests ON project_versions(request_id);",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM project_versions", [])?;
    Ok(())
}

fn version(conn: &Connection, id: i64) -> Result<ProjectVersion> {
    let (mut version, feedback): (ProjectVersion, Option<String>) = conn.query_row(
        "SELECT id, project_id, version_number, content, milestone_id, milestone_title, material_id, status, attempt, provider_id, request_id, feedback_json, error_code, error_message, created_at, updated_at, completed_at FROM project_versions WHERE id = ?1",
        params![id],
        |row| {
            let code: Option<String> = row.get(12)?;
            let message: Option<String> = row.get(13)?;
            Ok((ProjectVersion {
                id: row.get(0)?, project_id: row.get(1)?, version_number: row.get(2)?,
                content: row.get(3)?, milestone_id: row.get(4)?, milestone_title: row.get(5)?,
                material_id: row.get(6)?, status: row.get(7)?, attempt: row.get(8)?,
                provider_id: row.get(9)?, request_id: row.get(10)?, feedback: None,
                error: code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("项目评价失败"))),
                created_at: row.get(14)?, updated_at: row.get(15)?, completed_at: row.get(16)?,
            }, row.get(11)?))
        },
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "项目版本不存在"))?;
    version.feedback = feedback
        .map(|value| serde_json::from_str(&value))
        .transpose()?;
    Ok(version)
}

pub fn workspace(state: &ProjectState) -> Result<ProjectWorkspace> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM project_versions ORDER BY id DESC LIMIT 200")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    // 98#3：坏版本行（feedback_json 断电窗口等）只跳过并计数——原先 Err
    // 传播使 workspace 整页失败，而 submit/cancel/resume/iteration_note
    // 的返回值均为 workspace，恢复与重看路径一并瘫痪；单行 version()
    // 直调仍返回类型化错误可定位（75#3 坏行降级同口径）
    let mut versions = Vec::new();
    let mut skipped = 0_u64;
    for id in ids {
        match version(&store.conn, id) {
            Ok(version) => versions.push(version),
            Err(error) => {
                if error.code == "not_found" {
                    continue;
                }
                skipped += 1;
                eprintln!("project version row unreadable, skipped: {}", error.code);
            }
        }
    }
    if skipped > 0 {
        eprintln!("project workspace: {skipped} 行损坏已跳过");
    }
    Ok(ProjectWorkspace { versions })
}

fn project(state: &ProjectState, id: i64) -> Result<Value> {
    if id <= 0 {
        return Err(BackendError::invalid("invalid_project", "项目标识无效"));
    }
    let projects = state.store()?.collection("projects")?.value;
    projects
        .as_array()
        .into_iter()
        .flatten()
        .find(|project| project.get("id").and_then(Value::as_i64) == Some(id))
        .cloned()
        .ok_or_else(|| BackendError::new("not_found", "项目不存在"))
}

fn milestone(project: &Value, id: Option<i64>) -> Result<Option<(i64, String)>> {
    match id {
        Some(id) => {
            let milestone = project
                .get("milestones")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|item| item.get("id").and_then(Value::as_i64) == Some(id))
                .ok_or_else(|| BackendError::new("not_found", "项目里程碑不存在"))?;
            let title = milestone
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("项目里程碑")
                .to_string();
            Ok(Some((id, title)))
        }
        None => Ok(None),
    }
}

fn ensure_available(state: &ProjectState, project_id: i64, except: Option<i64>) -> Result<()> {
    let store = state.store()?;
    let running: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM project_versions WHERE project_id = ?1 AND status IN ('running', 'cancelling') AND id != COALESCE(?2, -1))",
        params![project_id, except],
        |row| row.get(0),
    )?;
    if running {
        return Err(BackendError::new(
            "project_feedback_active",
            "当前项目已有评价任务，请等待完成或先取消",
        ));
    }
    Ok(())
}

fn launch<R: Runtime>(
    app: AppHandle<R>,
    state: &ProjectState,
    id: i64,
) -> Result<ProjectWorkspace> {
    let worker_app = app.clone();
    let worker_state = state.shared();
    let result = super::security::thread_builder()
        .name(format!("project-feedback-{id}"))
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                evaluate(&worker_app, &worker_state, id)
            }));
            match result {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = fail(&worker_app, &worker_state, id, error);
                }
                Err(_) => {
                    let _ = fail(
                        &worker_app,
                        &worker_state,
                        id,
                        BackendError::new("task_panicked", "项目评价任务异常终止"),
                    );
                }
            }
        });
    if result.is_err() {
        let error = BackendError::new("task_start_failed", "项目评价任务未能启动，可恢复");
        fail(&app, state, id, error)?;
        return Err(BackendError::new(
            "task_start_failed",
            "项目评价任务未能启动",
        ));
    }
    workspace(state)
}

pub fn submit<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
    input: SubmissionInput,
) -> Result<ProjectWorkspace> {
    let content = input.content.trim();
    if content.is_empty() || content.len() > 100_000 || content.contains('\0') {
        return Err(BackendError::invalid(
            "invalid_project_submission",
            "成果正文须为 1 到 100000 字节且不含 NUL",
        ));
    }
    let project = project(state, input.project_id)?;
    let milestone = milestone(&project, input.milestone_id)?;
    if let Some(material_id) = input.material_id {
        let detail = super::materials::detail(&state.materials()?, material_id)?;
        if !["parsed", "degraded"].contains(&detail.material.status.as_str())
            || detail
                .text
                .as_deref()
                .is_none_or(|text| text.trim().is_empty())
        {
            return Err(BackendError::new(
                "material_not_parsed",
                "关联资料必须先完成文本解析",
            ));
        }
    }
    ensure_available(state, input.project_id, None)?;
    let provider_id = providers::routing::provider_for_task(&state.providers, "practice")?;
    let mut store = state.store()?;
    let timestamp = now()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    ensure_available(state, input.project_id, None)?;
    let number: u32 = tx.query_row(
        "SELECT COALESCE(MAX(version_number), 0) + 1 FROM project_versions WHERE project_id = ?1",
        params![input.project_id],
        |row| row.get(0),
    )?;
    tx.execute(
        "INSERT INTO project_versions (project_id, version_number, content, milestone_id, milestone_title, material_id, status, attempt, provider_id, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'running', 1, ?7, ?8, ?8)",
        params![input.project_id, number, content, milestone.as_ref().map(|item| item.0), milestone.as_ref().map(|item| item.1.clone()), input.material_id, provider_id, timestamp],
    )?;
    let id = tx.last_insert_rowid();
    let request_id = format!("project-feedback-{id}-1");
    tx.execute(
        "UPDATE project_versions SET request_id = ?1 WHERE id = ?2",
        params![request_id, id],
    )?;
    let event = Store::event(
        &tx,
        "project.feedback_started",
        json!({"projectId":input.project_id,"versionId":id,"versionNumber":number,"requestId":request_id,"attempt":1}),
    )?;
    tx.commit()?;
    emit(app, &event);
    launch(app.clone(), state, id)
}

fn cancelled(state: &ProjectState, id: i64) -> Result<bool> {
    let store = state.store()?;
    store
        .conn
        .query_row(
            "SELECT cancel_requested FROM project_versions WHERE id = ?1",
            params![id],
            |row| row.get(0),
        )
        .map_err(Into::into)
}

fn validate_feedback(feedback: &ProjectFeedback) -> Result<()> {
    if [
        feedback.concept,
        feedback.implementation,
        feedback.verification,
    ]
    .iter()
    .any(|score| *score > 100)
        || feedback.summary.trim().is_empty()
        || feedback.summary.len() > 6000
        || feedback.suggestions.is_empty()
        || feedback.suggestions.len() > 5
        || feedback
            .suggestions
            .iter()
            .any(|suggestion| suggestion.trim().is_empty() || suggestion.len() > 2000)
    {
        return Err(BackendError::new(
            "invalid_model_output",
            "项目量规返回的分数、总结或建议无效",
        ));
    }
    Ok(())
}

fn complete<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
    id: i64,
    feedback: &ProjectFeedback,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let timestamp = now()?;
    let changed = tx.execute("UPDATE project_versions SET status = 'done', feedback_json = ?1, error_code = NULL, error_message = NULL, updated_at = ?2, completed_at = ?2 WHERE id = ?3 AND status IN ('running', 'cancelling')", params![serde_json::to_string(feedback)?, timestamp, id])?;
    if changed == 0 {
        return Ok(());
    }
    let event = Store::event(
        &tx,
        "project.feedback_completed",
        json!({"versionId":id,"projectId":version(&tx, id)?.project_id,"feedback":feedback}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn fail<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
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
    let changed = tx.execute("UPDATE project_versions SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4, completed_at = ?4 WHERE id = ?5 AND status IN ('running', 'cancelling')", params![status, error.code, error.message, now()?, id])?;
    if changed == 0 {
        return Ok(());
    }
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "project.feedback_cancelled"
        } else {
            "project.feedback_failed"
        },
        json!({"versionId":id,"error":error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn evaluate<R: Runtime>(app: &AppHandle<R>, state: &ProjectState, id: i64) -> Result<()> {
    let version = version(&state.store()?.conn, id)?;
    if !["running", "cancelling"].contains(&version.status.as_str()) {
        return Ok(());
    }
    if cancelled(state, id)? {
        return Err(BackendError::new("cancelled", "项目评价已取消"));
    }
    let project = project(state, version.project_id)?;
    let material = version
        .material_id
        .map(|material_id| super::materials::detail(&state.materials()?, material_id))
        .transpose()?;
    let prompt = format!(
        "根据概念应用、实现质量、验证与反思三个量规，给出 0 到 100 的整数分数。只返回 {{\"concept\":0,\"implementation\":0,\"verification\":0,\"summary\":\"基于提交内容的总结\",\"suggestions\":[\"1 到 5 条具体改进建议\"]}}。不要假定已经执行项目或代码。项目：{}。提交成果：{}。关联资料：{}",
        serde_json::to_string(&json!({"title":project.get("title"),"description":project.get("description"),"tag":project.get("tag"),"milestones":project.get("milestones"),"milestone":version.milestone_title}))?,
        serde_json::to_string(&version.content)?,
        serde_json::to_string(&material.and_then(|detail| detail.text).map(|text| text.chars().take(12_000).collect::<String>()))?,
    );
    let messages = vec![
        ChatMessage {
            role: "system".into(),
            content: FEEDBACK_SYSTEM.into(),
            ..Default::default()
        },
        ChatMessage {
            role: "user".into(),
            content: prompt,
            ..Default::default()
        },
    ];
    let request_id = version
        .request_id
        .ok_or_else(|| BackendError::new("invalid_persisted_project", "评价任务缺少请求标识"))?;
    let request = providers::routing::stream_chat(
        app,
        &state.providers,
        &request_id,
        "practice",
        Some(&version.provider_id),
        &messages,
        2048,
        None,
        None,
        false,
    )?;
    if request.status != "done" {
        if cancelled(state, id)? || request.status == "cancelled" {
            return Err(BackendError::new("cancelled", "项目评价已取消"));
        }
        return Err(request
            .error
            .unwrap_or_else(|| BackendError::new("provider_error", "项目评价模型请求未完成")));
    }
    if cancelled(state, id)? {
        return Err(BackendError::new(
            "cancelled",
            "项目评价已取消，未写入评价结果",
        ));
    }
    let feedback: ProjectFeedback = learning::pipeline::parse_model_json(&request.output)?;
    validate_feedback(&feedback)?;
    complete(app, state, id, &feedback)
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
    id: i64,
) -> Result<ProjectWorkspace> {
    let current = version(&state.store()?.conn, id)?;
    if !["running", "cancelling"].contains(&current.status.as_str()) {
        return Err(BackendError::new(
            "project_feedback_not_active",
            "该版本没有正在执行的评价任务",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE project_versions SET status = 'cancelling', cancel_requested = 1, updated_at = ?1 WHERE id = ?2 AND status IN ('running', 'cancelling')", params![now()?, id])?;
    let event = Store::event(
        &tx,
        "project.feedback_cancel_requested",
        json!({"versionId":id,"requestId":current.request_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    if let Some(request_id) = current.request_id {
        // 31#11：容忍 no_active_request——请求恰完成时取消为良性（簿记已提交）；
        // 31#6 僵尸 running 版本在重启后内存标志消失，cancel_chat 必报该码，
        // 若上抛则版本永久卡 cancelling，resume/ensure_available 均不收口
        if let Err(error) = providers::cancel_chat(&state.providers, &request_id) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    workspace(state)
}

pub fn resume<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
    id: i64,
) -> Result<ProjectWorkspace> {
    let current = version(&state.store()?.conn, id)?;
    if current.status == "done" {
        return workspace(state);
    }
    if !["failed", "cancelled", "interrupted"].contains(&current.status.as_str()) {
        return Err(BackendError::new(
            "project_feedback_active",
            "当前评价任务尚未进入可恢复状态",
        ));
    }
    ensure_available(state, current.project_id, Some(id))?;
    let provider = providers::get(&state.providers, &current.provider_id)?;
    if !provider.enabled {
        return Err(BackendError::new(
            "provider_disabled",
            "项目评价供应商已停用",
        ));
    }
    let attempt = current
        .attempt
        .checked_add(1)
        .ok_or_else(|| BackendError::new("too_many_attempts", "评价恢复次数已用尽"))?;
    let request_id = format!("project-feedback-{id}-{attempt}");
    let store = state.store()?;
    // 31#13：IMMEDIATE 起步即持写锁——DEFERRED 下复查 ensure_available 在
    // 读快照上完成，与并发 submit/resume 各留一个 running（对照 submit 的写锁）
    let tx = store.write_transaction()?;
    ensure_available(state, current.project_id, Some(id))?;
    let changed = tx.execute("UPDATE project_versions SET status = 'running', attempt = ?1, request_id = ?2, cancel_requested = 0, error_code = NULL, error_message = NULL, updated_at = ?3, completed_at = NULL WHERE id = ?4 AND status IN ('failed', 'cancelled', 'interrupted')", params![attempt, request_id, now()?, id])?;
    if changed == 0 {
        return Err(BackendError::new(
            "project_feedback_active",
            "评价任务已被其他操作恢复",
        ));
    }
    let event = Store::event(
        &tx,
        "project.feedback_started",
        json!({"projectId":current.project_id,"versionId":id,"versionNumber":current.version_number,"requestId":request_id,"attempt":attempt}),
    )?;
    tx.commit()?;
    emit(app, &event);
    launch(app.clone(), state, id)
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &ProjectState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM project_versions WHERE status IN ('running', 'cancelling')")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for id in ids {
        let current = version(&store.conn, id)?;
        if providers::completed_chat(&state.providers, current.request_id.as_deref())?
            && !cancelled(state, id)?
        {
            // 31#6：重放评价单条失败不上抛——原样传播会让 recover 整体失败并
            // 写入 StartupState.failure，后端每次启动都报错且 id 更大的其余
            // running 版本永不翻转；与 practice/subjective recover 一致逐条落终态
            if let Err(replay_error) = evaluate(app, state, id) {
                let tx = store.conn.unchecked_transaction()?;
                // 34#10：status 落状态机内的 'failed'——原先把原始错误码写进
                // status，不属于 resume/cancel 的任何可恢复集合，版本永久卡死
                tx.execute("UPDATE project_versions SET status = 'failed', error_code = ?1, error_message = ?2, updated_at = ?3, completed_at = ?3 WHERE id = ?4", params![replay_error.code, replay_error.message, now()?, id])?;
                let event = Store::event(
                    &tx,
                    "project.feedback_interrupted",
                    json!({"versionId":id,"error":replay_error}),
                )?;
                tx.commit()?;
                emit(app, &event);
            }
            continue;
        }
        let error = if current.status == "cancelling" {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的项目评价")
        } else {
            BackendError::new("interrupted", "应用重启后项目评价已中断，可从原提交恢复")
        };
        let tx = store.conn.unchecked_transaction()?;
        let status = error.code.clone();
        tx.execute("UPDATE project_versions SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4, completed_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
        let event = Store::event(
            &tx,
            "project.feedback_interrupted",
            json!({"versionId":id,"error":error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn iteration_note<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProjectState,
    input: IterationNoteInput,
) -> Result<Value> {
    let content = input.content.trim();
    if content.is_empty() || content.len() > 100_000 || content.contains('\0') {
        return Err(BackendError::invalid(
            "invalid_note",
            "迭代笔记须为 1 到 100000 字节且不含 NUL",
        ));
    }
    let version = version(&state.store()?.conn, input.version_id)?;
    if version.status != "done" {
        return Err(BackendError::new(
            "project_feedback_not_ready",
            "只有已完成真实评价的版本才能生成迭代笔记",
        ));
    }
    let project = project(state, version.project_id)?;
    let title = project
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("项目");
    let store = state.store()?;
    // 31#14：IMMEDIATE——ID 分配与写入间无锁时，并发同毫秒同 id 的
    // ON CONFLICT DO UPDATE 会静默覆盖另一条笔记
    let tx = store.write_transaction()?;
    let id = Store::next_free_id(&tx, "notes")?;
    let note = json!({
        "id": id.parse::<i64>().map_err(|_| BackendError::new("invalid_id", "笔记标识无效"))?,
        "title": learning::actions::text(&format!("{title} · v{} 迭代笔记", version.version_number), 500),
        "excerpt": learning::actions::text(content, 90),
        "tag": learning::actions::text(title, 100),
        "updated": now()?,
        "content": content,
        // 13#28：与版本的持久关联随笔记落库，事后可按版本检索迭代笔记
        "projectVersionId": input.version_id,
    });
    super::store::validate_collection("notes", &Value::Array(vec![note.clone()]))?;
    // 25#18：配额计数与插入同事务，关闭 check-then-insert 并发窗口
    store.ensure_quota_in_tx(&tx, "notes", 1)?;
    Store::insert_json(&tx, "notes", &id, &note)?;
    let revision = Store::next_revision(&tx, "notes")?;
    let event = Store::event(
        &tx,
        "state.changed",
        json!({"namespace":"notes","revision":revision,"projectVersionId":input.version_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    store.app_state()
}
