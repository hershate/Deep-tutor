use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::providers::{self, ChatMessage};
use super::super::store::Store;
use super::*;
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::sync::atomic::Ordering;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LectureSection {
    pub heading: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LectureExample {
    pub title: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LectureCheckpoint {
    pub question: String,
    pub answer: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LectureContent {
    pub title: String,
    pub overview: String,
    pub sections: Vec<LectureSection>,
    pub examples: Vec<LectureExample>,
    pub checkpoints: Vec<LectureCheckpoint>,
    pub citations: Vec<Citation>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LectureUnit {
    pub index: usize,
    pub node_id: String,
    pub status: String,
    pub attempt: u32,
    pub request_id: Option<String>,
    pub content: Option<LectureContent>,
    pub error: Option<BackendError>,
    pub usage: Option<providers::openai::TokenUsage>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Lecture {
    pub plan_id: String,
    pub stage_id: String,
    pub version: u32,
    pub status: String,
    pub total_units: usize,
    pub completed_units: usize,
    pub current_unit: usize,
    pub previous_version: Option<u32>,
    pub units: Vec<LectureUnit>,
    pub error: Option<BackendError>,
    pub updated_at: Option<String>,
}

fn key(id: i64, stage_id: &str) -> String {
    format!("{id}:{stage_id}")
}

pub(crate) fn request_id(
    id: i64,
    stage_id: &str,
    version: u32,
    index: usize,
    attempt: u32,
) -> String {
    let hash = format!("{:x}", Sha256::digest(stage_id.as_bytes()));
    format!("lecture-{id}-{}-{index}-{version}-{attempt}", &hash[..12])
}

pub(crate) fn validate_content(content: &LectureContent, sources: &[Source]) -> Result<()> {
    if content.title.trim().is_empty()
        || content.title.len() > 200
        || content.overview.trim().is_empty()
        || content.overview.len() > 6000
    {
        return Err(BackendError::new(
            "invalid_lecture_unit",
            "讲义标题或概述无效",
        ));
    }
    if content.sections.is_empty()
        || content.sections.len() > 12
        || content.examples.len() > 8
        || content.checkpoints.is_empty()
        || content.checkpoints.len() > 8
    {
        return Err(BackendError::new(
            "invalid_lecture_unit",
            "讲义章节、示例或检查题数量超出限制",
        ));
    }
    if content.sections.iter().any(|section| {
        section.heading.trim().is_empty()
            || section.heading.len() > 200
            || section.content.trim().is_empty()
            || section.content.len() > 20000
    }) || content.examples.iter().any(|example| {
        example.title.trim().is_empty()
            || example.title.len() > 200
            || example.content.trim().is_empty()
            || example.content.len() > 8000
    }) || content.checkpoints.iter().any(|checkpoint| {
        checkpoint.question.trim().is_empty()
            || checkpoint.question.len() > 1000
            || checkpoint.answer.trim().is_empty()
            || checkpoint.answer.len() > 2000
    }) {
        return Err(BackendError::new(
            "invalid_lecture_unit",
            "讲义章节、示例或检查题内容无效",
        ));
    }
    let lines = content.overview.lines().count()
        + content
            .sections
            .iter()
            .map(|section| section.content.lines().count())
            .sum::<usize>()
        + content
            .examples
            .iter()
            .map(|example| example.content.lines().count())
            .sum::<usize>();
    if lines > 500 {
        return Err(BackendError::new(
            "lecture_too_long",
            "讲义超过 500 行，需拆分为更小的单元",
        ));
    }
    if content.citations.is_empty() {
        return Err(BackendError::new(
            "citation_missing",
            "讲义单元至少需要一条原文引用",
        ));
    }
    for citation in &content.citations {
        let source = sources
            .iter()
            .find(|source| source.id == citation.material_id)
            .ok_or_else(|| {
                BackendError::new("citation_out_of_scope", "讲义引用不在所选资料白名单中")
            })?;
        if !(2..=1000).contains(&citation.quote.chars().count())
            || !source.text.contains(&citation.quote)
        {
            return Err(BackendError::new(
                "citation_mismatch",
                "讲义引用必须是所选资料中连续存在的原文",
            ));
        }
    }
    Ok(())
}

pub fn get(
    state: &LearningState,
    id: &str,
    stage_id: &str,
    version: Option<u32>,
) -> Result<Lecture> {
    let id = plan_id(id)?;
    let plan = get_plan(state, id)?;
    if plan.adopted_project_id.is_none() {
        return Err(BackendError::new(
            "plan_not_ready",
            "请先采用方案，再打开学习空间",
        ));
    }
    let stage = plan
        .result
        .as_ref()
        .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
        .ok_or_else(|| BackendError::new("not_found", "学习阶段不存在"))?;
    let store = state.store()?;
    let current = store.conn.query_row(
        "SELECT version, status, current_unit, error_code, error_message, updated_at FROM learning_lectures WHERE plan_id = ?1 AND stage_id = ?2",
        params![id, stage_id],
        |row| Ok((row.get::<_, i64>(0)? as u32, row.get::<_, String>(1)?, row.get::<_, i64>(2)? as usize, row.get::<_, Option<String>>(3)?, row.get::<_, Option<String>>(4)?, row.get::<_, Option<String>>(5)?)),
    ).optional()?;
    let selected = match version {
        Some(version) => {
            let exists: bool = store.conn.query_row("SELECT EXISTS(SELECT 1 FROM learning_lecture_versions WHERE plan_id = ?1 AND stage_id = ?2 AND version = ?3)", params![id, stage_id, version], |row| row.get(0))?;
            if !exists {
                return Err(BackendError::new("not_found", "讲义历史版本不存在"));
            }
            (
                version,
                "done".into(),
                stage.node_ids.len(),
                None,
                None,
                None,
            )
        }
        None => current.unwrap_or((0, "empty".into(), 0, None, None, None)),
    };
    let (selected_version, status, current_unit, error_code, error_message, updated_at) = selected;
    let mut units = Vec::new();
    if selected_version > 0 {
        let mut statement = store.conn.prepare(
            "SELECT unit_index, node_id, status, attempt, request_id, content_json, error_code, error_message FROM learning_lecture_units WHERE plan_id = ?1 AND stage_id = ?2 AND version = ?3 ORDER BY unit_index",
        )?;
        let rows = statement.query_map(params![id, stage_id, selected_version], |row| {
            let request_id: Option<String> = row.get(4)?;
            let content: Option<String> = row.get(5)?;
            let error_code: Option<String> = row.get(6)?;
            let error_message: Option<String> = row.get(7)?;
            Ok((
                row.get::<_, i64>(0)? as usize,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)? as u32,
                request_id,
                content,
                error_code,
                error_message,
            ))
        })?;
        for row in rows {
            let (
                index,
                node_id,
                unit_status,
                attempt,
                request_id,
                content,
                error_code,
                error_message,
            ) = row?;
            let usage = match &request_id {
                Some(request) => providers::get_chat(&state.providers, request)
                    .ok()
                    .and_then(|request| request.usage),
                None => None,
            };
            units.push(LectureUnit {
                index,
                node_id,
                status: unit_status,
                attempt,
                request_id,
                content: content
                    .map(|value| serde_json::from_str(&value))
                    .transpose()?,
                error: error_code.map(|code| {
                    BackendError::new(
                        &code,
                        error_message.as_deref().unwrap_or("讲义单元生成失败"),
                    )
                }),
                usage,
            });
        }
    }
    let completed_units = units.iter().filter(|unit| unit.status == "done").count();
    let previous_version = store.conn.query_row(
        "SELECT MAX(version) FROM learning_lecture_versions WHERE plan_id = ?1 AND stage_id = ?2 AND version < ?3",
        params![id, stage_id, selected_version],
        |row| row.get(0),
    ).optional()?.flatten();
    Ok(Lecture {
        plan_id: format!("plan-{id}"),
        stage_id: stage_id.into(),
        version: selected_version,
        status,
        total_units: stage.node_ids.len(),
        completed_units,
        current_unit,
        previous_version,
        units,
        error: error_code.map(|code| {
            BackendError::new(&code, error_message.as_deref().unwrap_or("讲义生成失败"))
        }),
        updated_at,
    })
}

fn get_plan(state: &LearningState, id: i64) -> Result<Plan> {
    super::get(state, &format!("plan-{id}"))
}

fn register(state: &LearningState, id: i64, stage_id: &str) -> Result<()> {
    let mut active = state
        .lecture_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "讲义任务状态不可用"))?;
    let key = key(id, stage_id);
    if active.contains_key(&key) {
        return Err(BackendError::new("lecture_active", "该阶段讲义正在生成"));
    }
    active.insert(key, Arc::new(AtomicBool::new(false)));
    Ok(())
}

fn launch<R: Runtime>(
    app: AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: String,
) -> Result<()> {
    #[cfg(test)]
    if state.defer.load(Ordering::Acquire) {
        return Ok(());
    }
    let worker_app = app.clone();
    let worker_state = state.shared();
    let worker_stage = stage_id.clone();
    let active = Arc::clone(&state.lecture_active);
    let key = key(id, &stage_id);
    super::super::security::thread_builder()
        .name(format!("lecture-{id}"))
        .spawn(move || {
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run(&worker_app, &worker_state, id, &worker_stage)
            }));
            match outcome {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = fail_lecture(&worker_app, &worker_state, id, &worker_stage, error);
                }
                Err(_) => {
                    let _ = fail_lecture(
                        &worker_app,
                        &worker_state,
                        id,
                        &worker_stage,
                        BackendError::new("task_panicked", "讲义任务异常终止，请恢复已保存单元"),
                    );
                }
            }
            if let Ok(mut active) = active.lock() {
                active.remove(&key);
            }
        })
        .map(|_| ())
        .map_err(|_| BackendError::new("task_start_failed", "无法启动讲义生成线程"))
}

pub fn generate<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    stage_id: &str,
    force: bool,
) -> Result<Lecture> {
    let id = plan_id(id)?;
    let plan = get_plan(state, id)?;
    if plan.adopted_project_id.is_none() {
        return Err(BackendError::new(
            "plan_not_ready",
            "请先采用方案，再生成讲义",
        ));
    }
    let stage = plan
        .result
        .as_ref()
        .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
        .ok_or_else(|| BackendError::new("not_found", "学习阶段不存在"))?;
    if stage.node_ids.is_empty() {
        return Err(BackendError::new(
            "invalid_lecture_scope",
            "该阶段没有可生成的知识节点",
        ));
    }
    let store = state.store()?;
    let previous = store
        .conn
        .query_row(
            "SELECT version, status FROM learning_lectures WHERE plan_id = ?1 AND stage_id = ?2",
            params![id, stage_id],
            |row| Ok((row.get::<_, i64>(0)? as u32, row.get::<_, String>(1)?)),
        )
        .optional()?;
    if previous
        .as_ref()
        .is_some_and(|(_, status)| status == "done")
        && !force
    {
        return get(state, &id.to_string(), stage_id, None);
    }
    let version = previous
        .map(|(version, status)| {
            if status == "done" {
                version + 1
            } else {
                version
            }
        })
        .unwrap_or(1);
    register(state, id, stage_id)?;
    let outcome = (|| -> Result<_> {
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("INSERT INTO learning_lectures (plan_id, stage_id, version, status, current_unit, updated_at) VALUES (?1, ?2, ?3, 'running', 0, ?4) ON CONFLICT(plan_id, stage_id) DO UPDATE SET version = excluded.version, status = 'running', current_unit = 0, error_code = NULL, error_message = NULL, updated_at = excluded.updated_at", params![id, stage_id, version, now()?])?;
        for (index, node_id) in stage.node_ids.iter().enumerate() {
            tx.execute("INSERT OR IGNORE INTO learning_lecture_units (plan_id, stage_id, version, unit_index, node_id, status, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, 'pending', ?6)", params![id, stage_id, version, index as i64, node_id, now()?])?;
        }
        let event = Store::event(
            &tx,
            "lecture.started",
            json!({"planId": format!("plan-{id}"), "stageId": stage_id, "version": version, "totalUnits": stage.node_ids.len()}),
        )?;
        tx.commit()?;
        Ok(event)
    })();
    match outcome {
        Ok(event) => {
            emit(app, &event);
            if let Err(error) = launch(app.clone(), state, id, stage_id.into()) {
                if let Ok(mut active) = state.lecture_active.lock() {
                    active.remove(&key(id, stage_id));
                }
                let _ = fail_lecture(
                    app,
                    state,
                    id,
                    stage_id,
                    BackendError::new("task_start_failed", "讲义任务未能启动"),
                );
                return Err(error);
            }
            get(state, &id.to_string(), stage_id, None)
        }
        Err(error) => {
            if let Ok(mut active) = state.lecture_active.lock() {
                active.remove(&key(id, stage_id));
            }
            Err(error)
        }
    }
}

fn unit_state(
    state: &LearningState,
    id: i64,
    stage_id: &str,
    version: u32,
    index: usize,
) -> Result<(String, u32, Option<String>)> {
    let store = state.store()?;
    store.conn.query_row("SELECT status, attempt, request_id FROM learning_lecture_units WHERE plan_id = ?1 AND stage_id = ?2 AND version = ?3 AND unit_index = ?4", params![id, stage_id, version, index as i64], |row| Ok((row.get(0)?, row.get::<_, i64>(1)? as u32, row.get(2)?))).optional()?.ok_or_else(|| BackendError::new("not_found", "讲义单元不存在"))
}

pub(crate) fn begin_unit<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: &str,
    version: u32,
    index: usize,
) -> Result<(u32, String)> {
    let (status, previous_attempt, previous_request) =
        unit_state(state, id, stage_id, version, index)?;
    if status == "done" {
        return Err(BackendError::new(
            "step_completed",
            "讲义单元已经完成，不会重复调用模型",
        ));
    }
    let reusable = status == "interrupted"
        && providers::completed_chat(&state.providers, previous_request.as_deref())?;
    let attempt = if status == "running" || reusable {
        previous_attempt
    } else {
        previous_attempt + 1
    };
    let request_id = if (status == "running" || reusable) && previous_request.is_some() {
        previous_request.unwrap()
    } else {
        request_id(id, stage_id, version, index, attempt)
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_lecture_units SET status = 'running', attempt = ?1, request_id = ?2, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE plan_id = ?4 AND stage_id = ?5 AND version = ?6 AND unit_index = ?7", params![attempt, request_id, now()?, id, stage_id, version, index as i64])?;
    let event = Store::event(
        &tx,
        "lecture.unit_started",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "version": version, "unitIndex": index, "attempt": attempt, "requestId": request_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok((attempt, request_id))
}

fn fail_unit<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: &str,
    version: u32,
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
    tx.execute("UPDATE learning_lecture_units SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND stage_id = ?6 AND version = ?7 AND unit_index = ?8", params![status, error.code, error.message, now()?, id, stage_id, version, index as i64])?;
    let event = Store::event(
        &tx,
        "lecture.unit_failed",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "version": version, "unitIndex": index, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn complete_unit<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: &str,
    version: u32,
    index: usize,
    content: &LectureContent,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_lecture_units SET status = 'done', content_json = ?1, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE plan_id = ?3 AND stage_id = ?4 AND version = ?5 AND unit_index = ?6", params![serde_json::to_string(content)?, now()?, id, stage_id, version, index as i64])?;
    tx.execute("UPDATE learning_lectures SET current_unit = ?1, updated_at = ?2 WHERE plan_id = ?3 AND stage_id = ?4", params![index as i64 + 1, now()?, id, stage_id])?;
    let event = Store::event(
        &tx,
        "lecture.unit_completed",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "version": version, "unitIndex": index}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn unit<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    plan: &Plan,
    stage_id: &str,
    version: u32,
    index: usize,
    sources: &[Source],
) -> Result<LectureContent> {
    let id = plan_id(&plan.id)?;
    let stage = plan
        .result
        .as_ref()
        .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
        .ok_or_else(|| BackendError::new("not_found", "学习阶段不存在"))?;
    let node_id = &stage.node_ids[index];
    let node = plan
        .result
        .as_ref()
        .and_then(|result| result.graph.nodes.iter().find(|node| &node.id == node_id))
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "讲义引用了未知知识节点"))?;
    let prompt = format!(
        "为知识节点撰写自学讲义。只返回 {{\"title\":\"...\",\"overview\":\"...\",\"sections\":[{{\"heading\":\"...\",\"content\":\"Markdown 正文\"}}],\"examples\":[{{\"title\":\"...\",\"content\":\"...\"}}],\"checkpoints\":[{{\"question\":\"...\",\"answer\":\"...\"}}],\"citations\":[{{\"materialId\":资料数字ID,\"quote\":\"资料中连续存在的原文\"}}]}}。\n正文必须基于资料，包含概念解释和至少一道检查题；示例可为空数组；所有引用必须真实存在于资料中，不得编造来源或链接。总正文不超过 500 行。\n阶段：{}\n节点：{}\n节点摘要：{}\n难度：{}\n资料快照：{}",
        stage.title, node.title, node.summary, node.difficulty, serde_json::to_string(sources)?
    );
    let provider_id = plan.provider_id.as_str();
    let mut last_error = None;
    for retry in 0..2 {
        if state
            .lecture_active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "讲义任务状态不可用"))?
            .get(&key(id, stage_id))
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            return Err(BackendError::new("cancelled", "讲义生成已取消"));
        }
        let (_, request_id) = begin_unit(app, state, id, stage_id, version, index)?;
        let messages = vec![
            ChatMessage {
                role: "system".into(),
                content: pipeline::MODEL_SYSTEM.into(),
                ..Default::default()
            },
            ChatMessage {
                role: "user".into(),
                content: if retry == 0 {
                    prompt.clone()
                } else {
                    format!("{prompt}\n上次输出未通过校验，请重新输出完整且有效的 JSON。")
                },
                ..Default::default()
            },
        ];
        let request = match providers::routing::stream_chat(
            app,
            &state.providers,
            &request_id,
            "lecture",
            Some(provider_id),
            &messages,
            8192,
            None,
            None,
            false,
        ) {
            Ok(request) => request,
            Err(error) => {
                fail_unit(app, state, id, stage_id, version, index, &error)?;
                return Err(error);
            }
        };
        if request.status != "done" {
            let error = request
                .error
                .unwrap_or_else(|| BackendError::new("provider_error", "讲义模型请求未成功完成"));
            fail_unit(app, state, id, stage_id, version, index, &error)?;
            return Err(error);
        }
        match pipeline::parse_model_json::<LectureContent>(&request.output).and_then(|content| {
            validate_content(&content, sources)?;
            Ok(content)
        }) {
            Ok(content) => return Ok(content),
            Err(error) => {
                fail_unit(app, state, id, stage_id, version, index, &error)?;
                last_error = Some(error);
            }
        }
    }
    Err(last_error
        .unwrap_or_else(|| BackendError::new("invalid_model_output", "讲义单元输出未通过校验")))
}

fn run<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: &str,
) -> Result<()> {
    let plan = get_plan(state, id)?;
    let stage = plan
        .result
        .as_ref()
        .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
        .ok_or_else(|| BackendError::new("not_found", "学习阶段不存在"))?;
    let version = state.store()?.conn.query_row(
        "SELECT version FROM learning_lectures WHERE plan_id = ?1 AND stage_id = ?2",
        params![id, stage_id],
        |row| row.get::<_, i64>(0),
    )? as u32;
    let sources: Vec<Source> = serde_json::from_value(
        plan.artifacts
            .get("step0")
            .and_then(|step| step.get("snapshot"))
            .cloned()
            .ok_or_else(|| BackendError::new("invalid_persisted_plan", "讲义缺少资料快照"))?,
    )?;
    for (index, _) in stage.node_ids.iter().enumerate() {
        if state
            .lecture_active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "讲义任务状态不可用"))?
            .get(&key(id, stage_id))
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            return Err(BackendError::new("cancelled", "讲义生成已取消"));
        }
        if unit_state(state, id, stage_id, version, index)?.0 == "done" {
            continue;
        }
        let content = unit(app, state, &plan, stage_id, version, index, &sources)?;
        complete_unit(app, state, id, stage_id, version, index, &content)?;
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_lectures SET status = 'done', current_unit = ?1, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE plan_id = ?3 AND stage_id = ?4", params![stage.node_ids.len() as i64, now()?, id, stage_id])?;
    tx.execute("INSERT OR IGNORE INTO learning_lecture_versions (plan_id, stage_id, version, created_at) VALUES (?1, ?2, ?3, ?4)", params![id, stage_id, version, now()?])?;
    let event = Store::event(
        &tx,
        "lecture.completed",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "version": version, "totalUnits": stage.node_ids.len()}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn fail_lecture<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    stage_id: &str,
    error: BackendError,
) -> Result<()> {
    let status = if error.code == "cancelled" {
        "cancelled"
    } else {
        "failed"
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_lectures SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND stage_id = ?6", params![status, error.code, error.message, now()?, id, stage_id])?;
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "lecture.cancelled"
        } else {
            "lecture.failed"
        },
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    stage_id: &str,
) -> Result<Lecture> {
    let id = plan_id(id)?;
    let key = key(id, stage_id);
    let flag = state
        .lecture_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "讲义任务状态不可用"))?
        .get(&key)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_lecture", "该阶段没有正在生成的讲义"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE learning_lectures SET status = 'cancelling', updated_at = ?1 WHERE plan_id = ?2 AND stage_id = ?3", params![now()?, id, stage_id])?;
    let event = Store::event(
        &tx,
        "lecture.cancel_requested",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    let request = store.conn.query_row("SELECT request_id FROM learning_lecture_units WHERE plan_id = ?1 AND stage_id = ?2 AND status = 'running' AND request_id IS NOT NULL", params![id, stage_id], |row| row.get::<_, String>(0)).optional()?;
    if let Some(request) = request {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    get(state, &id.to_string(), stage_id, None)
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &LearningState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT plan_id, stage_id, status FROM learning_lectures WHERE status IN ('running', 'cancelling')")?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, stage_id, previous_status) in rows {
        let cancelled = previous_status == "cancelling";
        let status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let error = if cancelled {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的讲义")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试讲义，可恢复已保存单元",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE learning_lecture_units SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND stage_id = ?6 AND status = 'running'", params![status, error.code, error.message, now()?, id, stage_id])?;
        tx.execute("UPDATE learning_lectures SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE plan_id = ?5 AND stage_id = ?6", params![status, error.code, error.message, now()?, id, stage_id])?;
        let event = Store::event(
            &tx,
            if cancelled {
                "lecture.cancelled"
            } else {
                "lecture.interrupted"
            },
            json!({"planId": format!("plan-{id}"), "stageId": stage_id, "error": error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}
