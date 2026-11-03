pub mod commands;

use super::commands::emit;
use super::error::{BackendError, Result};
#[cfg(test)]
use super::learning::LearningState;
use super::learning::{self, Citation};
use super::materials::MaterialState;
use super::providers::{self, ChatMessage, ProviderState};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const SYSTEM: &str = "你是基于本地资料的学习问答助手。只能使用提供的资料，不得用记忆补造来源。只返回 JSON：answer 为真实回答，sufficient 表示资料是否足够，citations 为可逐字定位的资料引用。资料不足时明确说明，不得虚构结论。";
const MAX_SOURCE_CHARS: usize = 16_000;

pub struct KnowledgeState {
    database_path: PathBuf,
    materials: MaterialState,
    providers: ProviderState,
    active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
    #[cfg(test)]
    _learning: Option<LearningState>,
}

impl KnowledgeState {
    pub fn new(path: &Path, providers: &ProviderState, materials: &MaterialState) -> Self {
        Self {
            database_path: path.to_path_buf(),
            materials: materials.shared(),
            providers: providers.shared(),
            active: Arc::new(Mutex::new(HashMap::new())),
            #[cfg(test)]
            _learning: None,
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let learning = LearningState::for_tests()?;
        Ok(Self {
            database_path: learning.database_path().to_path_buf(),
            providers: learning.provider_state(),
            materials: learning.material_state(),
            active: Arc::new(Mutex::new(HashMap::new())),
            _learning: Some(learning),
        })
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            materials: self.materials.shared(),
            providers: self.providers.shared(),
            active: Arc::clone(&self.active),
            #[cfg(test)]
            _learning: None,
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeSource {
    pub material_id: i64,
    pub title: String,
    pub sha256: String,
    pub kind: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeCitation {
    pub material_id: i64,
    pub title: String,
    pub sha256: String,
    pub quote: String,
    pub char_start: usize,
    pub char_end: usize,
    pub line: usize,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeHit {
    pub kind: String,
    pub id: i64,
    pub title: String,
    pub excerpt: String,
    pub score: u32,
    pub material_id: Option<i64>,
    pub note_id: Option<i64>,
    pub citation: Option<KnowledgeCitation>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeSession {
    pub id: i64,
    pub question: String,
    pub answer: Option<String>,
    pub sufficient: Option<bool>,
    pub citations: Vec<KnowledgeCitation>,
    pub sources: Vec<KnowledgeSource>,
    pub status: String,
    pub provider_id: String,
    pub request_id: Option<String>,
    pub attempt: u32,
    pub warning: Option<String>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone)]
struct Scope {
    source: KnowledgeSource,
    text: String,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS knowledge_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT,
            sufficient INTEGER,
            citations_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            request_id TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            warning TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_session_materials (
            session_id INTEGER NOT NULL REFERENCES knowledge_sessions(id) ON DELETE CASCADE,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
            PRIMARY KEY (session_id, material_id)
        );
        CREATE INDEX IF NOT EXISTS knowledge_session_material ON knowledge_session_materials(material_id);",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM knowledge_session_materials", [])?;
    conn.execute("DELETE FROM knowledge_sessions", [])?;
    Ok(())
}

fn locate(source: &Scope, quote: &str) -> Result<KnowledgeCitation> {
    if !(2..=1000).contains(&quote.chars().count()) {
        return Err(BackendError::new(
            "citation_mismatch",
            "引用长度须为 2 到 1000 字",
        ));
    }
    let byte_start = source
        .text
        .find(quote)
        .ok_or_else(|| BackendError::new("citation_mismatch", "引用无法在所选资料原文中定位"))?;
    let char_start = source.text[..byte_start].chars().count();
    let line = source.text[..byte_start]
        .bytes()
        .filter(|byte| *byte == b'\n')
        .count()
        + 1;
    Ok(KnowledgeCitation {
        material_id: source.source.material_id,
        title: source.source.title.clone(),
        sha256: source.source.sha256.clone(),
        quote: quote.to_string(),
        char_start,
        char_end: char_start + quote.chars().count(),
        line,
    })
}

fn load_scope(state: &KnowledgeState, ids: &[i64]) -> Result<Vec<Scope>> {
    if ids.is_empty()
        || ids.len() > 8
        || ids.iter().any(|id| *id <= 0)
        || ids.iter().collect::<HashSet<_>>().len() != ids.len()
    {
        return Err(BackendError::invalid(
            "invalid_knowledge_scope",
            "请选择 1 到 8 份不重复的资料",
        ));
    }
    let mut scope = Vec::new();
    for id in ids {
        let detail = super::materials::detail(&state.materials, *id)?;
        let text = detail
            .text
            .filter(|text| !text.trim().is_empty())
            .ok_or_else(|| {
                BackendError::new(
                    "material_not_parsed",
                    &format!("资料「{}」尚未产生可用解析文本", detail.material.title),
                )
            })?;
        scope.push(Scope {
            source: KnowledgeSource {
                material_id: detail.material.id,
                title: detail.material.title,
                sha256: detail.material.sha256,
                kind: detail.material.kind,
            },
            text,
        });
    }
    Ok(scope)
}

fn session_scope(state: &KnowledgeState, id: i64) -> Result<Vec<Scope>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT material_id FROM knowledge_session_materials WHERE session_id = ?1 ORDER BY material_id")?;
    let ids = statement
        .query_map(params![id], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    drop(store);
    load_scope(state, &ids)
}

fn session_row(conn: &Connection, state: &KnowledgeState, id: i64) -> Result<KnowledgeSession> {
    let (question, answer, sufficient, citations, status, provider_id, request_id, attempt, warning, code, message, created_at, updated_at): (String, Option<String>, Option<bool>, String, String, String, Option<String>, u32, Option<String>, Option<String>, Option<String>, String, String) = conn.query_row(
        "SELECT question, answer, sufficient, citations_json, status, provider_id, request_id, attempt, warning, error_code, error_message, created_at, updated_at FROM knowledge_sessions WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?, row.get(10)?, row.get(11)?, row.get(12)?)),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "Wiki 问答记录不存在"))?;
    let sources = session_scope(state, id)?
        .into_iter()
        .map(|scope| scope.source)
        .collect();
    Ok(KnowledgeSession {
        id,
        question,
        answer,
        sufficient,
        citations: serde_json::from_str(&citations)?,
        sources,
        status,
        provider_id,
        request_id,
        attempt,
        warning,
        error: code
            .map(|code| BackendError::new(&code, message.as_deref().unwrap_or("问答任务未完成"))),
        created_at,
        updated_at,
    })
}

pub fn get(state: &KnowledgeState, id: i64) -> Result<KnowledgeSession> {
    let store = state.store()?;
    session_row(&store.conn, state, id)
}

pub fn list(state: &KnowledgeState) -> Result<Vec<KnowledgeSession>> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM knowledge_sessions ORDER BY id DESC LIMIT 100")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    ids.into_iter()
        .map(|id| session_row(&store.conn, state, id))
        .collect()
}

fn excerpt(text: &str, query: &str) -> String {
    let Some(start) = text.find(query) else {
        return text.chars().take(180).collect();
    };
    let prefix = text[..start]
        .chars()
        .rev()
        .take(45)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect::<String>();
    let suffix = text[start..].chars().take(160).collect::<String>();
    format!("{prefix}{suffix}")
}

fn search_hit(
    kind: &str,
    id: i64,
    title: String,
    text: String,
    terms: &[String],
    source: Option<&Scope>,
) -> Option<KnowledgeHit> {
    let title_lower = title.to_lowercase();
    let text_lower = text.to_lowercase();
    if !terms
        .iter()
        .all(|term| title_lower.contains(term) || text_lower.contains(term))
    {
        return None;
    }
    let score = terms
        .iter()
        .map(|term| title_lower.matches(term).count() + text_lower.matches(term).count())
        .sum::<usize>() as u32;
    Some(KnowledgeHit {
        kind: kind.into(),
        id,
        title,
        excerpt: excerpt(&text, &terms[0]),
        score,
        material_id: (kind == "material").then_some(id),
        note_id: (kind == "note").then_some(id),
        citation: source.and_then(|source| locate(source, &terms[0]).ok()),
    })
}

pub fn search(state: &KnowledgeState, query: &str) -> Result<Vec<KnowledgeHit>> {
    let query = query.trim();
    if query.is_empty() || query.len() > 200 {
        return Err(BackendError::invalid(
            "invalid_search_query",
            "检索词须为 1 到 200 字节",
        ));
    }
    let terms = query
        .split_whitespace()
        .map(str::to_lowercase)
        .collect::<Vec<_>>();
    let store = state.store()?;
    let notes = store.collection("notes")?.value;
    let mut hits = Vec::new();
    for note in notes.as_array().into_iter().flatten() {
        let Some(id) = note.get("id").and_then(Value::as_i64) else {
            continue;
        };
        let title = note
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let content = note
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if let Some(hit) = search_hit("note", id, title, content, &terms, None) {
            hits.push(hit);
        }
    }
    for material in super::materials::list(&state.materials)? {
        let detail = super::materials::detail(&state.materials, material.id)?;
        let text = detail.text.clone().unwrap_or_default();
        let scope = Scope {
            source: KnowledgeSource {
                material_id: material.id,
                title: material.title.clone(),
                sha256: material.sha256,
                kind: material.kind,
            },
            text: text.clone(),
        };
        if let Some(hit) = search_hit(
            "material",
            material.id,
            material.title,
            text,
            &terms,
            Some(&scope),
        ) {
            hits.push(hit);
        }
    }
    hits.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then_with(|| a.kind.cmp(&b.kind))
            .then_with(|| a.id.cmp(&b.id))
    });
    Ok(hits)
}

fn launch<R: Runtime>(app: AppHandle<R>, state: &KnowledgeState, id: i64) -> Result<()> {
    let worker_app = app.clone();
    let worker_state = state.shared();
    let active = Arc::clone(&state.active);
    let result = super::security::thread_builder()
        .name(format!("wiki-answer-{id}"))
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run(&worker_app, &worker_state, id)
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
                        BackendError::new("task_panicked", "Wiki 问答任务异常终止"),
                    );
                }
            }
            if let Ok(mut active) = active.lock() {
                active.remove(&id);
            }
        });
    if result.is_err() {
        if let Ok(mut active) = state.active.lock() {
            active.remove(&id);
        }
        fail(
            &app,
            state,
            id,
            BackendError::new("task_start_failed", "Wiki 问答未能启动，可恢复任务"),
        )?;
        return Err(BackendError::new(
            "task_start_failed",
            "无法启动 Wiki 问答任务",
        ));
    }
    Ok(())
}

pub fn ask<R: Runtime>(
    app: &AppHandle<R>,
    state: &KnowledgeState,
    question: String,
    material_ids: Vec<i64>,
) -> Result<KnowledgeSession> {
    let question = question.trim().to_string();
    if question.is_empty() || question.len() > 8000 {
        return Err(BackendError::invalid(
            "invalid_knowledge_question",
            "问题须为 1 到 8000 字节",
        ));
    }
    let scope = load_scope(state, &material_ids)?;
    let provider_id = providers::routing::provider_for_task(&state.providers, "wiki")?;
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let timestamp = now()?;
    tx.execute(
        "INSERT INTO knowledge_sessions (question, status, provider_id, created_at, updated_at) VALUES (?1, 'running', ?2, ?3, ?3)",
        params![question, provider_id, timestamp],
    )?;
    let id = tx.last_insert_rowid();
    let request_id = format!("wiki-{id}-1");
    tx.execute(
        "UPDATE knowledge_sessions SET request_id = ?1 WHERE id = ?2",
        params![request_id, id],
    )?;
    for source in scope {
        tx.execute(
            "INSERT INTO knowledge_session_materials (session_id, material_id) VALUES (?1, ?2)",
            params![id, source.source.material_id],
        )?;
    }
    let event = Store::event(
        &tx,
        "knowledge.answer_started",
        json!({"sessionId": id, "requestId": request_id, "materialIds": material_ids}),
    )?;
    active.insert(id, Arc::new(AtomicBool::new(false)));
    if let Err(error) = tx.commit() {
        active.remove(&id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, id)?;
    get(state, id)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ModelAnswer {
    answer: String,
    sufficient: bool,
    citations: Vec<Citation>,
}

fn run<R: Runtime>(app: &AppHandle<R>, state: &KnowledgeState, id: i64) -> Result<()> {
    let store = state.store()?;
    let (question, provider_id, request_id): (String, String, Option<String>) =
        store.conn.query_row(
            "SELECT question, provider_id, request_id FROM knowledge_sessions WHERE id = ?1",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
    let request_id =
        request_id.ok_or_else(|| BackendError::new("request_missing", "问答缺少模型请求标识"))?;
    if state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?
        .get(&id)
        .is_none_or(|flag| flag.load(Ordering::Acquire))
    {
        return Err(BackendError::new("cancelled", "Wiki 问答已取消"));
    }
    let scope = session_scope(state, id)?;
    let mut truncated = false;
    let source_data = scope.iter().map(|source| {
        truncated |= source.text.chars().count() > MAX_SOURCE_CHARS;
        json!({"materialId": source.source.material_id, "title": source.source.title, "text": source.text.chars().take(MAX_SOURCE_CHARS).collect::<String>(), "truncated": source.text.chars().count() > MAX_SOURCE_CHARS})
    }).collect::<Vec<_>>();
    let messages = vec![
        ChatMessage {
            role: "system".into(),
            content: SYSTEM.into(),
            ..Default::default()
        },
        ChatMessage {
            role: "user".into(),
            content: serde_json::to_string(
                &json!({"question": question, "sources": source_data, "citationSchema": [{"materialId": "资料数字ID", "quote": "连续原文"}]}),
            )?,
            ..Default::default()
        },
    ];
    let result = providers::routing::stream_chat(
        app,
        &state.providers,
        &request_id,
        "wiki",
        Some(&provider_id),
        &messages,
        4096,
        None,
        None,
        false,
    )?;
    if result.status != "done" {
        return Err(result
            .error
            .unwrap_or_else(|| BackendError::new("provider_error", "模型问答请求未完成")));
    }
    if state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?
        .get(&id)
        .is_none_or(|flag| flag.load(Ordering::Acquire))
    {
        return Err(BackendError::new(
            "cancelled",
            "Wiki 问答已取消，未写入模型结果",
        ));
    }
    let draft: ModelAnswer = learning::pipeline::parse_model_json(&result.output)?;
    if draft.answer.trim().is_empty()
        || draft.answer.len() > 30_000
        || (draft.sufficient && draft.citations.is_empty())
    {
        return Err(BackendError::new(
            "invalid_model_output",
            "回答为空、过长或缺少必要的资料引用",
        ));
    }
    let mut seen = HashSet::new();
    let mut citations = Vec::new();
    for citation in draft.citations {
        let source = scope
            .iter()
            .find(|source| source.source.material_id == citation.material_id)
            .ok_or_else(|| {
                BackendError::new("citation_out_of_scope", "引用不在本次问答的资料范围内")
            })?;
        if seen.insert((citation.material_id, citation.quote.clone())) {
            citations.push(locate(source, &citation.quote)?);
        }
    }
    let warning = truncated
        .then_some("部分资料仅使用前 16000 字；模型不能据此推断未提供的内容。".to_string());
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE knowledge_sessions SET answer = ?1, sufficient = ?2, citations_json = ?3, warning = ?4, status = 'done', error_code = NULL, error_message = NULL, updated_at = ?5 WHERE id = ?6 AND status = 'running'",
        params![draft.answer.trim(), draft.sufficient, serde_json::to_string(&citations)?, warning, now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("cancelled", "问答已取消，未写入模型结果"));
    }
    let event = Store::event(
        &tx,
        "knowledge.answer_completed",
        json!({"sessionId": id, "requestId": request_id, "citationCount": citations.len(), "sufficient": draft.sufficient}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn fail<R: Runtime>(
    app: &AppHandle<R>,
    state: &KnowledgeState,
    id: i64,
    error: BackendError,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let status = if error.code == "cancelled" {
        "cancelled"
    } else {
        "failed"
    };
    let changed = tx.execute("UPDATE knowledge_sessions SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5 AND status IN ('running', 'cancelling')", params![status, error.code, error.message, now()?, id])?;
    if changed == 0 {
        return Ok(());
    }
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "knowledge.answer_cancelled"
        } else {
            "knowledge.answer_failed"
        },
        json!({"sessionId": id, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    state: &KnowledgeState,
    id: i64,
) -> Result<KnowledgeSession> {
    let flag = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?
        .get(&id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_answer", "该问答没有正在执行的任务"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let request_id: Option<String> = store
        .conn
        .query_row(
            "SELECT request_id FROM knowledge_sessions WHERE id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "Wiki 问答记录不存在"))?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("UPDATE knowledge_sessions SET status = 'cancelling', updated_at = ?1 WHERE id = ?2 AND status = 'running'", params![now()?, id])?;
    if changed == 0 {
        return Err(BackendError::new(
            "no_active_answer",
            "问答已经结束，不能重复取消",
        ));
    }
    let event = Store::event(
        &tx,
        "knowledge.answer_cancel_requested",
        json!({"sessionId": id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    if let Some(request_id) = request_id {
        if let Err(error) = providers::cancel_chat(&state.providers, &request_id) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    get(state, id)
}

pub fn resume<R: Runtime>(
    app: &AppHandle<R>,
    state: &KnowledgeState,
    id: i64,
) -> Result<KnowledgeSession> {
    let initial = get(state, id)?;
    if !["failed", "cancelled", "interrupted"].contains(&initial.status.as_str()) {
        return Ok(initial);
    }
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let (status, previous): (String, Option<String>) = tx.query_row("SELECT status, request_id FROM knowledge_sessions WHERE id = ?1 AND status IN ('failed', 'cancelled', 'interrupted')", params![id], |row| Ok((row.get(0)?, row.get(1)?))).optional()?.ok_or_else(|| BackendError::new("answer_active", "问答状态已改变，不能重复恢复"))?;
    if active.contains_key(&id) {
        return Err(BackendError::new("answer_active", "该问答已在恢复"));
    }
    let reusable = status == "interrupted"
        && providers::completed_chat(&state.providers, previous.as_deref())?;
    let attempt = if reusable {
        initial.attempt
    } else {
        initial.attempt + 1
    };
    let request_id = if reusable {
        previous.unwrap()
    } else {
        format!("wiki-{id}-{attempt}")
    };
    tx.execute("UPDATE knowledge_sessions SET status = 'running', request_id = ?1, attempt = ?2, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE id = ?4", params![request_id, attempt, now()?, id])?;
    let event = Store::event(
        &tx,
        "knowledge.answer_resumed",
        json!({"sessionId": id, "requestId": request_id, "attempt": attempt}),
    )?;
    active.insert(id, Arc::new(AtomicBool::new(false)));
    if let Err(error) = tx.commit() {
        active.remove(&id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, id)?;
    get(state, id)
}

pub fn stop(state: &KnowledgeState) -> Result<()> {
    let flags = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT request_id FROM knowledge_sessions WHERE status IN ('running', 'cancelling') AND request_id IS NOT NULL")?;
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
    Err(BackendError::new("answer_active", "Wiki 问答任务尚未停止"))
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &KnowledgeState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id, status FROM knowledge_sessions WHERE status IN ('running', 'cancelling')",
    )?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, previous) in rows {
        let cancelled = previous == "cancelling";
        let status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let error = if cancelled {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的问答")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试问答，可恢复任务",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE knowledge_sessions SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
        let event = Store::event(
            &tx,
            if cancelled {
                "knowledge.answer_cancelled"
            } else {
                "knowledge.answer_interrupted"
            },
            json!({"sessionId": id, "error": error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn delete<R: Runtime>(app: &AppHandle<R>, state: &KnowledgeState, id: i64) -> Result<()> {
    if state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "Wiki 问答状态不可用"))?
        .contains_key(&id)
    {
        return Err(BackendError::new(
            "answer_active",
            "请先取消并等待问答结束，再删除历史",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("DELETE FROM knowledge_sessions WHERE id = ?1", params![id])?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "Wiki 问答记录不存在"));
    }
    let event = Store::event(&tx, "knowledge.session_deleted", json!({"sessionId": id}))?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn clear_history<R: Runtime>(app: &AppHandle<R>, state: &KnowledgeState) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let running: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM knowledge_sessions WHERE status IN ('running', 'cancelling'))",
        [],
        |row| row.get(0),
    )?;
    if running {
        return Err(BackendError::new(
            "answer_active",
            "请先取消并等待问答结束，再清空历史",
        ));
    }
    let deleted = tx.execute("DELETE FROM knowledge_sessions", [])?;
    let event = Store::event(
        &tx,
        "knowledge.history_cleared",
        json!({"deleted": deleted}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

#[cfg(test)]
mod tests;
