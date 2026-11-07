pub mod commands;
pub mod exams;
pub mod subjective;
pub mod variants;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{self, Citation, LearningState, Source};
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

const GENERATION_SYSTEM: &str = "你是学习测评命题器。只能依据给定知识节点与资料原文命题，只返回符合要求的 JSON 对象。所有引用必须逐字来自资料，不能虚构概念、学习证据、链接或答案。";

pub struct PracticeState {
    database_path: PathBuf,
    providers: ProviderState,
    learning: LearningState,
    active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
    grading_active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
}

impl PracticeState {
    pub fn new(path: &Path, providers: &ProviderState, learning: &LearningState) -> Self {
        Self {
            database_path: path.to_path_buf(),
            providers: providers.shared(),
            learning: learning.shared(),
            active: Arc::new(Mutex::new(HashMap::new())),
            grading_active: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let learning = LearningState::for_tests()?;
        Ok(Self {
            database_path: learning.database_path().to_path_buf(),
            providers: learning.provider_state(),
            learning,
            active: Arc::new(Mutex::new(HashMap::new())),
            grading_active: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            providers: self.providers.shared(),
            learning: self.learning.shared(),
            active: Arc::clone(&self.active),
            grading_active: Arc::clone(&self.grading_active),
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PracticeSet {
    pub id: i64,
    pub mode: String,
    pub status: String,
    pub plan_id: String,
    pub stage_id: Option<String>,
    pub node_id: Option<String>,
    pub provider_id: String,
    pub difficulty: String,
    pub feedback_mode: String,
    pub kinds: Vec<String>,
    pub variant_of: Option<i64>,
    pub exam_time_limit_minutes: Option<u32>,
    pub exam_started_at: Option<String>,
    pub exam_deadline: Option<String>,
    pub pass_score_percent: Option<u8>,
    pub total_questions: u32,
    pub answered_questions: u32,
    /// 03#2：hints 模式不公布 set 级总分（与逐题遮蔽同口径）
    pub score: Option<f64>,
    pub max_score: Option<f64>,
    pub attempt: u32,
    pub request_id: Option<String>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RubricCriterion {
    pub id: String,
    pub description: String,
    pub points: f64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PracticeQuestion {
    pub id: i64,
    pub position: u32,
    pub kind: String,
    pub prompt: String,
    pub options: Vec<String>,
    pub hint: String,
    pub difficulty: u8,
    pub node_ids: Vec<String>,
    pub citations: Vec<Citation>,
    pub rubric: Vec<RubricCriterion>,
    pub variant_of: Option<i64>,
    pub bookmarked: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PracticeSetDetail {
    pub set: PracticeSet,
    pub questions: Vec<PracticeQuestion>,
    pub answers: Vec<AnswerView>,
    pub answered_question_ids: Vec<i64>,
    pub exam_expired: bool,
    pub exam_remaining_seconds: Option<i64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnswerView {
    pub id: i64,
    pub question_id: i64,
    pub score: Option<f64>,
    pub max_score: Option<f64>,
    pub correct: Option<bool>,
    pub feedback: Value,
    pub status: String,
    pub error: Option<BackendError>,
    pub request_id: Option<String>,
    pub response: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnswerResult {
    pub answer: AnswerView,
    pub set: PracticeSet,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Mistake {
    pub question_id: i64,
    pub set_id: i64,
    pub plan_id: String,
    pub stage_id: Option<String>,
    pub kind: String,
    pub prompt: String,
    pub node_ids: Vec<String>,
    pub reason: String,
    pub wrong_count: u32,
    pub last_wrong_at: String,
    pub mastered: bool,
    pub mastered_question_id: Option<i64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateInput {
    pub plan_id: String,
    pub stage_id: Option<String>,
    pub node_id: Option<String>,
    pub count: Option<u32>,
    pub difficulty: Option<String>,
    pub feedback_mode: Option<String>,
    pub kinds: Option<Vec<String>>,
    pub mode: Option<String>,
    pub variant_of: Option<i64>,
    pub time_limit_minutes: Option<u32>,
    pub pass_score_percent: Option<u8>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct QuestionDraft {
    kind: String,
    prompt: String,
    #[serde(default)]
    options: Vec<String>,
    answer: Value,
    hint: String,
    explanation: String,
    citations: Vec<Citation>,
    node_ids: Vec<String>,
    #[serde(default)]
    difficulty: Option<u8>,
    #[serde(default)]
    rubric: Vec<RubricCriterion>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct QuestionDrafts {
    questions: Vec<QuestionDraft>,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS practice_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            stage_id TEXT,
            provider_id TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            feedback_mode TEXT NOT NULL,
            kinds_json TEXT NOT NULL DEFAULT '[\"choice\", \"fill\"]',
            total_questions INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            max_score REAL NOT NULL DEFAULT 0,
            attempt INTEGER NOT NULL DEFAULT 1,
            request_id TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS practice_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id INTEGER NOT NULL REFERENCES practice_sets(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            prompt TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer_json TEXT NOT NULL,
            hint TEXT NOT NULL,
            explanation TEXT NOT NULL,
            citations_json TEXT NOT NULL,
            node_ids_json TEXT NOT NULL,
            difficulty INTEGER NOT NULL,
            variant_of INTEGER,
            UNIQUE(set_id, position)
        );
        CREATE TABLE IF NOT EXISTS practice_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL UNIQUE REFERENCES practice_questions(id) ON DELETE CASCADE,
            set_id INTEGER NOT NULL REFERENCES practice_sets(id) ON DELETE CASCADE,
            response_json TEXT NOT NULL,
            score REAL NOT NULL,
            max_score REAL NOT NULL,
            correct INTEGER NOT NULL,
            feedback_json TEXT NOT NULL,
            status TEXT NOT NULL,
            grader_request_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS practice_mistakes (
            question_id INTEGER PRIMARY KEY REFERENCES practice_questions(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            wrong_count INTEGER NOT NULL DEFAULT 1,
            last_wrong_at TEXT NOT NULL,
            mastered INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS practice_bookmarks (
            question_id INTEGER PRIMARY KEY REFERENCES practice_questions(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );
        -- 27#5：答题提交/详情/计数按 set_id 过滤 practice_answers，仅 PK+
        -- UNIQUE(question_id) 时为全表扫
        CREATE INDEX IF NOT EXISTS practice_answers_set ON practice_answers(set_id);",
    )?;
    let mut statement = conn.prepare("PRAGMA table_info(practice_sets)")?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if !columns.iter().any(|column| column == "kinds_json") {
        conn.execute("ALTER TABLE practice_sets ADD COLUMN kinds_json TEXT NOT NULL DEFAULT '[\"choice\", \"fill\"]'", [])?;
    }
    if !columns.iter().any(|column| column == "node_id") {
        conn.execute("ALTER TABLE practice_sets ADD COLUMN node_id TEXT", [])?;
    }
    subjective::migrate(conn)?;
    variants::migrate(conn)?;
    exams::migrate(conn)?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM practice_answers", [])?;
    conn.execute("DELETE FROM practice_mistakes", [])?;
    conn.execute("DELETE FROM practice_bookmarks", [])?;
    conn.execute("DELETE FROM practice_questions", [])?;
    conn.execute("DELETE FROM practice_sets", [])?;
    conn.execute(
        "DELETE FROM learning_node_evidence WHERE kind IN ('practice', 'exam')",
        [],
    )?;
    Ok(())
}

fn sources(
    state: &PracticeState,
    id: &str,
) -> Result<(learning::Plan, Vec<Source>, HashSet<String>)> {
    let plan = learning::get(&state.learning, id)?;
    if plan.adopted_project_id.is_none() {
        return Err(BackendError::new(
            "plan_not_ready",
            "请先采用学习方案再生成练习",
        ));
    }
    let sources: Vec<Source> = serde_json::from_value(
        plan.artifacts
            .get("step0")
            .and_then(|step| step.get("snapshot"))
            .cloned()
            .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少原始资料快照"))?,
    )?;
    let nodes = plan
        .result
        .as_ref()
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案尚未形成知识图谱"))?
        .graph
        .nodes
        .iter()
        .map(|node| node.id.clone())
        .collect();
    Ok((plan, sources, nodes))
}

fn validate_citations(citations: &[Citation], sources: &[Source]) -> Result<()> {
    if citations.is_empty() {
        return Err(BackendError::new(
            "citation_missing",
            "题目必须包含资料原文引用",
        ));
    }
    for citation in citations {
        let source = sources
            .iter()
            .find(|source| source.id == citation.material_id)
            .ok_or_else(|| BackendError::new("citation_out_of_scope", "题目引用不在所选资料中"))?;
        if !(2..=1000).contains(&citation.quote.chars().count())
            || !source.text.contains(&citation.quote)
        {
            return Err(BackendError::new(
                "citation_mismatch",
                "题目引用必须是资料中连续存在的原文",
            ));
        }
    }
    Ok(())
}

fn generation_prompt(
    plan: &learning::Plan,
    sources: &[Source],
    input: &GenerateInput,
    node_ids: &HashSet<String>,
    variant: Option<&variants::VariantSource>,
) -> Result<String> {
    let stage = input.stage_id.as_deref().and_then(|id| {
        plan.result
            .as_ref()
            .and_then(|result| result.stages.iter().find(|stage| stage.id == id))
    });
    let nodes = plan
        .result
        .as_ref()
        .map(|result| {
            result
                .graph
                .nodes
                .iter()
                .filter(|node| {
                    if input.node_id.is_some() {
                        node_ids.contains(&node.id)
                    } else {
                        stage
                            .map(|stage| stage.node_ids.contains(&node.id))
                            .unwrap_or_else(|| node_ids.contains(&node.id))
                    }
                })
                .map(|node| json!({"id": node.id, "title": node.title, "summary": node.summary, "difficulty": node.difficulty}))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let source_data = sources
        .iter()
        .map(|source| {
            let text = source.text.chars().take(12_000).collect::<String>();
            json!({"materialId": source.id, "title": source.title, "text": text})
        })
        .collect::<Vec<_>>();
    let subjective = input
        .kinds
        .as_deref()
        .unwrap_or_default()
        .iter()
        .all(|kind| ["short", "essay"].contains(&kind.as_str()));
    let prompt = format!(
        "生成 {} 道学习题，范围为 {:?}。题目类别：{}（请求了多种类别时，每种类别至少 1 道，除非题目总数少于类别数）。只返回 {{\"questions\":[{{\"kind\":\"choice|fill|short|essay\",\"prompt\":\"...\",\"options\":[\"...\"],\"answer\":正确选项下标或参考答案,\"hint\":\"...\",\"explanation\":\"...\",\"citations\":[{{\"materialId\":资料数字ID,\"quote\":\"资料连续原文\"}}],\"nodeIds\":[\"...\"],\"difficulty\":1到5,\"rubric\":[{{\"id\":\"评分项标识\",\"description\":\"评分要点\",\"points\":分值}}]}}]}}。choice 必须给 2 到 6 个不同选项，answer 是从 0 开始的下标；fill 的 answer 必须是准确、简洁、可用于文字核对的原文术语；short 与 essay 必须给参考答案和 2 到 6 项 rubric，points 总和为 1。其他题型的 rubric 为空数组。每题至少一条真实引用。难度：{}。节点：{}。资料：{}",
        input.count.unwrap_or(5),
        input.kinds,
        if subjective { "subjective" } else { "objective" },
        input.difficulty.as_deref().unwrap_or("adaptive"),
        serde_json::to_string(&nodes)?,
        serde_json::to_string(&source_data)?,
    );
    if let Some(source) = variant {
        Ok(format!("{prompt}\n变式练习：保持原考点与原题型：{}，改变设问情境、数值或论证角度，不得只换几个词或复制原题。原题与错误原因：{}", source.kind, source.prompt_json()?))
    } else {
        Ok(prompt)
    }
}

fn launch<R: Runtime>(app: AppHandle<R>, state: &PracticeState, id: i64) -> Result<()> {
    let worker_app = app.clone();
    let worker_state = state.shared();
    let active = Arc::clone(&state.active);
    let result = super::security::thread_builder()
        .name(format!("practice-generate-{id}"))
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                generate_questions(&worker_app, &worker_state, id)
            }));
            match result {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = fail_generation(&worker_app, &worker_state, id, error);
                }
                Err(_) => {
                    let _ = fail_generation(
                        &worker_app,
                        &worker_state,
                        id,
                        BackendError::new("task_panicked", "练习生成任务异常终止"),
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
        let error = BackendError::new("task_start_failed", "练习生成未能启动，可恢复任务");
        fail_generation(&app, state, id, error)?;
        return Err(BackendError::new(
            "task_start_failed",
            "无法启动练习生成任务",
        ));
    }
    Ok(())
}

pub fn generate<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    input: GenerateInput,
) -> Result<PracticeSetDetail> {
    let count = input.count.unwrap_or(5);
    if !(1..=20).contains(&count) {
        return Err(BackendError::invalid(
            "invalid_question_count",
            "练习数量须为 1 到 20 题",
        ));
    }
    let mut kinds = input
        .kinds
        .clone()
        .unwrap_or_else(|| vec!["choice".into(), "fill".into()]);
    let unique = kinds.iter().collect::<HashSet<_>>();
    if kinds.is_empty()
        || unique.len() != kinds.len()
        || kinds
            .iter()
            .any(|kind| !["choice", "fill", "short", "essay"].contains(&kind.as_str()))
    {
        return Err(BackendError::new(
            "unsupported_question_kind",
            "支持的题型为单选、填空、简答与论述",
        ));
    }
    if input.mode.as_deref() == Some("exam") && input.variant_of.is_some() {
        return Err(BackendError::new(
            "variant_in_exam",
            "错题变式请使用针对练习，不作为模拟考试",
        ));
    }
    let mut stage_id = input.stage_id.clone();
    let mut feedback_mode = input
        .feedback_mode
        .clone()
        .unwrap_or_else(|| "immediate".into());
    if let Some(source_id) = input.variant_of {
        let source = variants::source(state, source_id, &input.plan_id)?;
        if input
            .kinds
            .as_ref()
            .is_some_and(|requested| requested.len() != 1 || requested[0] != source.kind)
        {
            return Err(BackendError::new(
                "invalid_variant_kind",
                "变式练习必须保持原题题型",
            ));
        }
        kinds = vec![source.kind.clone()];
        if stage_id.is_none() {
            stage_id = source.stage_id.clone();
        }
        if input.feedback_mode.is_none() {
            feedback_mode = source.feedback_mode.clone();
        }
    }
    if !["adaptive", "basic", "advanced", "comprehensive"]
        .contains(&input.difficulty.as_deref().unwrap_or("adaptive"))
    {
        return Err(BackendError::invalid("invalid_difficulty", "练习难度无效"));
    }
    if !["immediate", "deferred", "hints"].contains(&feedback_mode.as_str()) {
        return Err(BackendError::invalid(
            "invalid_feedback_mode",
            "反馈方式无效",
        ));
    }
    let mode = input.mode.as_deref().unwrap_or("targeted");
    if !["targeted", "exam"].contains(&mode) {
        return Err(BackendError::invalid(
            "invalid_practice_mode",
            "练习模式无效",
        ));
    }
    let time_limit = input.time_limit_minutes.unwrap_or(45);
    let pass_score = input.pass_score_percent.unwrap_or(80);
    if mode == "exam" {
        if feedback_mode != "deferred" {
            return Err(BackendError::invalid(
                "invalid_feedback_mode",
                "模拟考试必须在交卷后统一反馈",
            ));
        }
        if !(1..=240).contains(&time_limit) {
            return Err(BackendError::invalid(
                "invalid_exam_time_limit",
                "考试时长须为 1 到 240 分钟",
            ));
        }
        if !(1..=100).contains(&pass_score) {
            return Err(BackendError::invalid(
                "invalid_pass_score",
                "建议通过百分比须为 1 到 100",
            ));
        }
    } else if input.time_limit_minutes.is_some() || input.pass_score_percent.is_some() {
        return Err(BackendError::invalid(
            "invalid_exam_configuration",
            "考试时长与通过百分比只适用于模拟考试",
        ));
    }
    let exam_config = (mode == "exam").then_some((time_limit, pass_score));
    let (plan, _, _) = sources(state, &input.plan_id)?;
    if let Some(stage_id) = &stage_id {
        if !plan.result.as_ref().is_some_and(|result| {
            result
                .stages
                .iter()
                .any(|stage| &stage.id == stage_id && !stage.node_ids.is_empty())
        }) {
            return Err(BackendError::new(
                "invalid_practice_scope",
                "练习阶段不存在或没有知识节点",
            ));
        }
    }
    if let Some(node_id) = input.node_id.as_deref() {
        let valid = !node_id.trim().is_empty()
            && plan.result.as_ref().is_some_and(|result| {
                result.graph.nodes.iter().any(|node| node.id == node_id)
                    && stage_id.as_ref().is_none_or(|stage_id| {
                        result.stages.iter().any(|stage| {
                            &stage.id == stage_id && stage.node_ids.iter().any(|id| id == node_id)
                        })
                    })
            })
            && match input.variant_of {
                Some(source_id) => variants::source(state, source_id, &input.plan_id)?
                    .node_ids
                    .iter()
                    .any(|id| id == node_id),
                None => true,
            };
        if !valid {
            return Err(BackendError::new(
                "invalid_practice_scope",
                "目标知识节点不存在、超出所选阶段或不符合变式范围",
            ));
        }
    }
    let provider_id = providers::routing::provider_for_task(&state.providers, "practice")?;
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?;
    let mut store = state.store()?;
    let timestamp = now()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    tx.execute(
        "INSERT INTO practice_sets (mode, status, plan_id, stage_id, provider_id, difficulty, feedback_mode, kinds_json, variant_of, exam_time_limit_minutes, pass_score_percent, total_questions, node_id, created_at, updated_at) VALUES (?1, 'generating', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?13)",
        params![mode, learning::plan_id(&input.plan_id)?, stage_id, provider_id, input.difficulty.as_deref().unwrap_or("adaptive"), feedback_mode, serde_json::to_string(&kinds)?, input.variant_of, exam_config.map(|config| config.0), exam_config.map(|config| config.1), count, input.node_id, timestamp],
    )?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "practice.generation_started",
        json!({"setId": id, "mode": mode, "variantOf": input.variant_of}),
    )?;
    active.insert(id, Arc::new(AtomicBool::new(false)));
    if let Err(error) = tx.commit() {
        active.remove(&id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, id)?;
    detail(state, id)
}

fn fail_generation<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
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
    let changed = tx.execute(
        "UPDATE practice_sets SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5 AND status IN ('generating', 'cancelling')",
        params![status, error.code, error.message, now()?, id],
    )?;
    if changed == 0 {
        return Ok(());
    }
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "practice.generation_cancelled"
        } else {
            "practice.generation_failed"
        },
        json!({"setId": id, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn generate_questions<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    id: i64,
) -> Result<()> {
    let store = state.store()?;
    let (plan_id, stage_id, provider_id, difficulty, feedback, kinds, count, attempt, mode, variant_of, node_id): (i64, Option<String>, String, String, String, String, u32, u32, String, Option<i64>, Option<String>) = store.conn.query_row(
        "SELECT plan_id, stage_id, provider_id, difficulty, feedback_mode, kinds_json, total_questions, attempt, mode, variant_of, node_id FROM practice_sets WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?, row.get(10)?)),
    )?;
    let plan_id = format!("plan-{plan_id}");
    let (plan, sources, nodes) = sources(state, &plan_id)?;
    let input = GenerateInput {
        plan_id: plan_id.clone(),
        stage_id,
        node_id,
        count: Some(count),
        difficulty: Some(difficulty),
        feedback_mode: Some(feedback),
        kinds: Some(serde_json::from_str(&kinds)?),
        mode: Some(mode),
        variant_of,
        time_limit_minutes: None,
        pass_score_percent: None,
    };
    let variant = variant_of
        .map(|source_id| variants::source(state, source_id, &plan_id))
        .transpose()?;
    let nodes = if let Some(source) = &variant {
        source.node_ids.iter().cloned().collect::<HashSet<_>>()
    } else if let Some(node_id) = input.node_id.as_deref() {
        HashSet::from([node_id.to_string()])
    } else if let Some(stage_id) = input.stage_id.as_deref() {
        plan.result
            .as_ref()
            .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
            .ok_or_else(|| BackendError::new("invalid_persisted_plan", "练习引用的学习阶段不存在"))?
            .node_ids
            .iter()
            .cloned()
            .collect::<HashSet<_>>()
    } else {
        nodes
    };
    let request_id = format!("practice-{id}-{attempt}");
    store.conn.execute(
        "UPDATE practice_sets SET request_id = ?1, updated_at = ?2 WHERE id = ?3 AND status IN ('generating', 'cancelling')",
        params![request_id, now()?, id],
    )?;
    if state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?
        .get(&id)
        .is_none_or(|flag| flag.load(Ordering::Acquire))
    {
        return Err(BackendError::new("cancelled", "练习生成已取消"));
    }
    let prompt = generation_prompt(&plan, &sources, &input, &nodes, variant.as_ref())?;
    let messages = vec![
        ChatMessage {
            role: "system".into(),
            content: GENERATION_SYSTEM.into(),
            ..Default::default()
        },
        ChatMessage {
            role: "user".into(),
            content: prompt,
            ..Default::default()
        },
    ];
    let result = providers::routing::stream_chat(
        app,
        &state.providers,
        &request_id,
        "practice",
        Some(&provider_id),
        &messages,
        8192,
        None,
        None,
        false,
    )?;
    if result.status != "done" {
        return Err(result
            .error
            .unwrap_or_else(|| BackendError::new("provider_error", "练习生成请求未完成")));
    }
    if state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?
        .get(&id)
        .is_none_or(|flag| flag.load(Ordering::Acquire))
    {
        return Err(BackendError::new(
            "cancelled",
            "练习生成已取消，未写入模型结果",
        ));
    }
    let drafts: QuestionDrafts = learning::pipeline::parse_model_json(&result.output)?;
    if drafts.questions.len() != count as usize {
        return Err(BackendError::new(
            "invalid_model_output",
            "模型返回的题目数量与请求不一致",
        ));
    }
    let mut seen = HashSet::new();
    let tx = store.conn.unchecked_transaction()?;
    for (index, question) in drafts.questions.iter().enumerate() {
        if !input
            .kinds
            .as_deref()
            .unwrap_or_default()
            .contains(&question.kind)
            || question.prompt.trim().is_empty()
            || variant
                .as_ref()
                .is_some_and(|source| source.prompt.trim() == question.prompt.trim())
            || question.prompt.len() > 6000
            || question.hint.len() > 2000
            || question.explanation.len() > 6000
            || question.node_ids.is_empty()
            || !question.node_ids.iter().all(|node| nodes.contains(node))
            || question
                .difficulty
                .is_some_and(|difficulty| !(1..=5).contains(&difficulty))
            || !seen.insert(question.prompt.trim().to_string())
        {
            return Err(BackendError::new(
                "invalid_model_output",
                "模型返回了无效、重复或未请求的题型",
            ));
        }
        match question.kind.as_str() {
            "choice" => {
                let index = question.answer.as_u64().ok_or_else(|| {
                    BackendError::new("invalid_model_output", "单选题答案必须是选项下标")
                })? as usize;
                if question.options.len() < 2
                    || question.options.len() > 6
                    || index >= question.options.len()
                    || question
                        .options
                        .iter()
                        .any(|option| option.trim().is_empty())
                {
                    return Err(BackendError::new(
                        "invalid_model_output",
                        "单选题选项或答案无效",
                    ));
                }
                let unique = question
                    .options
                    .iter()
                    .map(|option| option.trim())
                    .collect::<HashSet<_>>();
                if unique.len() != question.options.len() {
                    return Err(BackendError::new(
                        "invalid_model_output",
                        "单选题选项不能重复",
                    ));
                }
            }
            "fill" => {
                if !question.options.is_empty()
                    || question
                        .answer
                        .as_str()
                        .is_none_or(|answer| answer.trim().is_empty() || answer.len() > 1000)
                {
                    return Err(BackendError::new("invalid_model_output", "填空题答案无效"));
                }
            }
            "short" | "essay" => {
                if !question.options.is_empty()
                    || question
                        .answer
                        .as_str()
                        .is_none_or(|answer| answer.trim().is_empty() || answer.len() > 10_000)
                    || question.hint.trim().is_empty()
                    || question.explanation.trim().is_empty()
                {
                    return Err(BackendError::new(
                        "invalid_model_output",
                        "主观题参考答案、提示或解析无效",
                    ));
                }
                subjective::validate_rubric(&question.rubric)?;
            }
            _ => {
                return Err(BackendError::new(
                    "unsupported_question_kind",
                    "模型返回了未支持的题型",
                ))
            }
        }
        validate_citations(&question.citations, &sources)?;
        tx.execute(
            "INSERT INTO practice_questions (set_id, position, kind, prompt, options_json, answer_json, hint, explanation, citations_json, node_ids_json, difficulty, rubric_json, variant_of) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![id, index as i64, question.kind, question.prompt, serde_json::to_string(&question.options)?, serde_json::to_string(&question.answer)?, question.hint, question.explanation, serde_json::to_string(&question.citations)?, serde_json::to_string(&question.node_ids)?, question.difficulty.unwrap_or(3), serde_json::to_string(&question.rubric)?, input.variant_of],
        )?;
    }
    let (started_at, deadline) = if input.mode.as_deref() == Some("exam") {
        let minutes = set_row(&store.conn, id)?
            .exam_time_limit_minutes
            .ok_or_else(|| BackendError::new("exam_not_started", "试卷缺少考试时长"))?;
        let started_at = now()?;
        let deadline = (OffsetDateTime::now_utc() + Duration::from_secs(minutes as u64 * 60))
            .format(&Rfc3339)?;
        (Some(started_at), Some(deadline))
    } else {
        (None, None)
    };
    let changed = tx.execute(
        "UPDATE practice_sets SET status = 'ready', max_score = ?1, error_code = NULL, error_message = NULL, updated_at = ?2, exam_started_at = CASE WHEN ?4 = 'exam' THEN ?5 ELSE NULL END, exam_deadline = CASE WHEN ?4 = 'exam' THEN ?6 ELSE NULL END WHERE id = ?3 AND status = 'generating'",
        params![count, now()?, id, input.mode.as_deref(), started_at, deadline],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "cancelled",
            "练习生成已取消，未写入模型结果",
        ));
    }
    let event = Store::event(
        &tx,
        "practice.generation_completed",
        json!({"setId": id, "count": count}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

fn set_row(conn: &Connection, id: i64) -> Result<PracticeSet> {
    let (mut set, kinds): (PracticeSet, String) = conn.query_row(
        "SELECT mode, status, plan_id, stage_id, provider_id, difficulty, feedback_mode, kinds_json, total_questions, score, max_score, attempt, request_id, error_code, error_message, created_at, updated_at, completed_at, variant_of, exam_time_limit_minutes, exam_started_at, exam_deadline, pass_score_percent, node_id FROM practice_sets WHERE id = ?1",
        params![id],
        |row| {
            let plan_id: i64 = row.get(2)?;
            let feedback_mode: String = row.get(6)?;
            let code: Option<String> = row.get(13)?;
            let message: Option<String> = row.get(14)?;
            Ok((PracticeSet {
                id,
                mode: row.get(0)?,
                status: row.get(1)?,
                plan_id: format!("plan-{plan_id}"),
                stage_id: row.get(3)?,
                node_id: row.get(23)?,
                provider_id: row.get(4)?,
                difficulty: row.get(5)?,
                feedback_mode: feedback_mode.clone(),
                kinds: Vec::new(),
                variant_of: row.get(18)?,
                exam_time_limit_minutes: row.get::<_, Option<i64>>(19)?.map(|value| value as u32),
                exam_started_at: row.get(20)?,
                exam_deadline: row.get(21)?,
                pass_score_percent: row.get::<_, Option<i64>>(22)?.map(|value| value as u8),
                total_questions: row.get(8)?,
                answered_questions: 0,
                // 03#2：hints 模式遮蔽 set 级分数，完成后也不公布总分与正确率
                score: (feedback_mode != "hints").then(|| row.get(9)).transpose()?,
                max_score: (feedback_mode != "hints").then(|| row.get(10)).transpose()?,
                attempt: row.get(11)?,
                request_id: row.get(12)?,
                error: code.map(|code| BackendError::new(&code, message.as_deref().unwrap_or("练习生成失败"))),
                created_at: row.get(15)?,
                updated_at: row.get(16)?,
                completed_at: row.get(17)?,
            }, row.get(7)?))
        },
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "练习集合不存在"))?;
    set.kinds = serde_json::from_str(&kinds)?;
    set.answered_questions = conn.query_row(
        "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status = 'done'",
        params![id],
        |row| row.get(0),
    )?;
    Ok(set)
}

/// 13#24：到期未交卷的考试在读取时惰性收卷。仓库规约不允许后台
/// 定时器/无人值守进程，读取路径是确定性的收卷时机。
pub fn lazy_finalize_expired_exam<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    id: i64,
) -> Result<()> {
    let conn = state.store()?.conn;
    let set = set_row(&conn, id)?;
    if exams::remaining(&set).is_ok_and(|(expired, _)| expired) {
        // 23#3：仍有未完成评分的作答（评分失败/重启置 interrupted）时跳过惰性
        // 收卷——此时 finalize 必报 grading_in_progress，若随读取上抛会让详情
        // 整体不可读，而恢复评分所需的 answer_id 只能从详情枚举，形成恢复
        // 死锁；报错留给显式交卷路径
        let pending: u32 = conn.query_row(
            "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status != 'done'",
            params![id],
            |row| row.get(0),
        )?;
        if pending == 0 {
            exams::finalize(app, state, id)?;
        }
    }
    Ok(())
}

pub fn detail(state: &PracticeState, id: i64) -> Result<PracticeSetDetail> {
    let store = state.store()?;
    let set = set_row(&store.conn, id)?;
    // 书签与提示一次性预取：detail 在每次提交与 practice.* 事件刷新时整段重跑，
    // 逐题 EXISTS / 逐答案查提示的 N+1 会随题量放大为可感卡顿（07#10）
    let mut statement = store
        .conn
        .prepare("SELECT question_id FROM practice_bookmarks WHERE question_id IN (SELECT id FROM practice_questions WHERE set_id = ?1)")?;
    let bookmarks: std::collections::BTreeSet<i64> = statement
        .query_map(params![id], |row| row.get(0))?
        .collect::<std::result::Result<_, _>>()?;
    drop(statement);
    let mut statement = store.conn.prepare("SELECT id, position, kind, prompt, options_json, hint, difficulty, citations_json, node_ids_json, variant_of, rubric_json FROM practice_questions WHERE set_id = ?1 ORDER BY position")?;
    let mut hints = HashMap::new();
    let questions = statement
        .query_map(params![id], |row| {
            let question_id: i64 = row.get(0)?;
            let options: String = row.get(4)?;
            let hint: String = row.get(5)?;
            let citations: String = row.get(7)?;
            let nodes: String = row.get(8)?;
            let rubric: String = row.get(10)?;
            Ok(PracticeQuestion {
                id: question_id,
                position: row.get::<_, i64>(1)? as u32,
                kind: row.get(2)?,
                prompt: row.get(3)?,
                options: serde_json::from_str(&options).unwrap_or_default(),
                bookmarked: bookmarks.contains(&question_id),
                hint: hint.clone(),
                difficulty: row.get::<_, i64>(6)? as u8,
                citations: serde_json::from_str(&citations).unwrap_or_default(),
                node_ids: serde_json::from_str(&nodes).unwrap_or_default(),
                rubric: serde_json::from_str(&rubric).unwrap_or_default(),
                variant_of: row.get(9)?,
            })
            .map(|question| {
                hints.insert(question_id, hint);
                question
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let mut answers = if set.status == "completed" || set.feedback_mode == "immediate" {
        let mut statement = store.conn.prepare("SELECT id, question_id, response_json, grader_request_id, score, max_score, correct, feedback_json FROM practice_answers WHERE set_id = ?1 AND status = 'done' ORDER BY id")?;
        let rows = statement.query_map(params![id], |row| {
            let feedback: String = row.get(7)?;
            let response: String = row.get(2)?;
            let reveal = set.feedback_mode != "hints";
            Ok(AnswerView {
                id: row.get(0)?,
                question_id: row.get(1)?,
                score: reveal.then(|| row.get(4)).transpose()?,
                max_score: reveal.then(|| row.get(5)).transpose()?,
                correct: reveal.then(|| row.get(6)).transpose()?,
                feedback: if set.feedback_mode == "hints" {
                    let question_id: i64 = row.get(1)?;
                    json!({"hint": hints.get(&question_id).cloned().unwrap_or_default()})
                } else {
                    serde_json::from_str(&feedback).unwrap_or(Value::Null)
                },
                status: "done".into(),
                error: None,
                request_id: row.get(3)?,
                response: serde_json::from_str(&response).unwrap_or(Value::Null),
            })
        })?;
        rows.collect::<std::result::Result<Vec<_>, _>>()?
    } else {
        Vec::new()
    };
    answers.extend(subjective::pending_answers(&store.conn, id)?);
    answers.sort_by_key(|answer| answer.question_id);
    let mut statement = store
        .conn
        .prepare("SELECT question_id FROM practice_answers WHERE set_id = ?1 ORDER BY id")?;
    let answered_question_ids = statement
        .query_map(params![id], |row| row.get(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let (exam_expired, exam_remaining_seconds) = exams::remaining(&set)?;
    Ok(PracticeSetDetail {
        set,
        questions,
        answers,
        answered_question_ids,
        exam_expired,
        exam_remaining_seconds,
    })
}

fn normalize(answer: &str) -> String {
    answer
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        // 13#23：中文 IME 常见全角字母/数字/标点归一为半角等价形态，
        // 语义等价作答（ＡＢＣ/１２３/，）不再被判错
        .chars()
        .map(|character| match character {
            '！'..='～' => char::from_u32(character as u32 - 0xFEE0).unwrap_or(character),
            '　' => ' ',
            other => other,
        })
        .collect::<String>()
        .to_lowercase()
}

pub fn submit<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    set_id: i64,
    question_id: i64,
    answer: Value,
) -> Result<AnswerResult> {
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let set = set_row(&tx, set_id)?;
    if set.status != "ready" {
        return Err(BackendError::new(
            "set_not_ready",
            "练习尚未生成完成或已提交",
        ));
    }
    exams::ensure_accepting(&set)?;
    let (kind, expected, explanation, nodes, options): (String, String, String, String, String) = tx.query_row(
        "SELECT kind, answer_json, explanation, node_ids_json, options_json FROM practice_questions WHERE id = ?1 AND set_id = ?2",
        params![question_id, set_id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "题目不存在"))?;
    let already: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM practice_answers WHERE question_id = ?1 AND status = 'done')",
        params![question_id],
        |row| row.get(0),
    )?;
    if already {
        return Err(BackendError::new(
            "answer_already_graded",
            "这道题已判分，不能重复提交",
        ));
    }
    if ["short", "essay"].contains(&kind.as_str()) {
        drop(tx);
        return subjective::submit(app, state, set_id, question_id, answer);
    }
    let expected: Value = serde_json::from_str(&expected)?;
    let correct = match kind.as_str() {
        "choice" => {
            let selected = answer.as_u64().ok_or_else(|| {
                BackendError::invalid("invalid_answer", "单选题答案必须是选项下标")
            })?;
            let options: Vec<String> = serde_json::from_str(&options)?;
            if selected >= options.len() as u64 {
                return Err(BackendError::invalid("invalid_answer", "所选选项不存在"));
            }
            Some(selected) == expected.as_u64()
        }
        "fill" => {
            let actual = answer
                .as_str()
                .filter(|answer| !answer.trim().is_empty())
                .ok_or_else(|| BackendError::invalid("invalid_answer", "填空题答案不能为空"))?;
            // 25#9：对齐主观题的 30,000 字节上限——生成侧填空限 1,000 字，
            // 用户作答原无上限，超大载荷会经 normalize 全量直写 response_json
            if actual.len() > 30_000 {
                return Err(BackendError::invalid(
                    "invalid_answer",
                    "填空题答案不能超过 30000 字节",
                ));
            }
            normalize(actual) == normalize(expected.as_str().unwrap_or_default())
        }
        _ => {
            return Err(BackendError::new(
                "unsupported_question_kind",
                "不支持的练习题型",
            ))
        }
    };
    let timestamp = now()?;
    tx.execute(
        "INSERT INTO practice_answers (question_id, set_id, response_json, score, max_score, correct, feedback_json, status, created_at) VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, 'done', ?7)",
        params![question_id, set_id, serde_json::to_string(&answer)?, if correct { 1.0 } else { 0.0 }, correct, serde_json::to_string(&json!({"answer": expected, "explanation": explanation}))?, timestamp],
    )?;
    let answer_id = tx.last_insert_rowid();
    // B-11：hints 模式承诺「只给提示不显示答案」——错题本行本身即答错
    // 信号、reason 更是完整解析，写错题本会把答案经同屏标签页泄露出去；
    // hints 模式的作答不进错题本（反馈面与遮蔽口径一致）
    if !correct && set.feedback_mode != "hints" {
        tx.execute(
            "INSERT INTO practice_mistakes (question_id, reason, last_wrong_at) VALUES (?1, ?2, ?3) ON CONFLICT(question_id) DO UPDATE SET wrong_count = wrong_count + 1, last_wrong_at = excluded.last_wrong_at, mastered = 0, mastered_question_id = NULL",
            params![question_id, explanation, timestamp],
        )?;
    }
    let nodes: Vec<String> = serde_json::from_str(&nodes)?;
    for node in nodes {
        tx.execute(
            "INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at, evidence_key) VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, ?7) ON CONFLICT(plan_id, node_id, evidence_key) DO UPDATE SET score = excluded.score, max_score = excluded.max_score, details_json = excluded.details_json, created_at = excluded.created_at",
            params![learning::plan_id(&set.plan_id)?, node, if set.mode == "exam" { "exam" } else { "practice" }, if correct { 1.0 } else { 0.0 }, serde_json::to_string(&json!({"setId": set_id, "questionId": question_id, "answerId": answer_id}))?, timestamp, format!("practice-answer-{answer_id}")],
        )?;
    }
    let answered: u32 = tx.query_row(
        "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status = 'done'",
        params![set_id],
        |row| row.get(0),
    )?;
    let completed = answered == set.total_questions && set.mode != "exam";
    if completed {
        tx.execute("UPDATE practice_sets SET status = 'completed', score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), completed_at = ?1, updated_at = ?1 WHERE id = ?2", params![timestamp, set_id])?;
    } else {
        tx.execute("UPDATE practice_sets SET score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), updated_at = ?1 WHERE id = ?2", params![timestamp, set_id])?;
    }
    let reveal = set.feedback_mode == "immediate" || (set.feedback_mode != "hints" && completed);
    let kind = if completed {
        "practice.set_completed"
    } else if reveal {
        "practice.answer_graded"
    } else {
        "practice.answer_submitted"
    };
    let mut events = vec![Store::event(
        &tx,
        kind,
        json!({"setId": set_id, "questionId": question_id, "answerId": answer_id, "answered": answered, "correct": reveal.then_some(correct)}),
    )?];
    if let Some(source_question_id) =
        variants::mark_mastered(&tx, question_id, correct && set.feedback_mode != "hints")?
    {
        events.push(Store::event(
            &tx,
            "practice.mistake_mastered_by_variant",
            json!({"setId": set_id, "sourceQuestionId": source_question_id, "variantQuestionId": question_id, "answerId": answer_id}),
        )?);
    }
    tx.commit()?;
    for event in events {
        emit(app, &event);
    }
    let result_set = set_row(&store.conn, set_id)?;
    let hint: String = store.conn.query_row(
        "SELECT hint FROM practice_questions WHERE id = ?1",
        params![question_id],
        |row| row.get(0),
    )?;
    Ok(AnswerResult {
        answer: AnswerView {
            id: answer_id,
            question_id,
            score: reveal.then_some(if correct { 1.0 } else { 0.0 }),
            max_score: reveal.then_some(1.0),
            correct: reveal.then_some(correct),
            feedback: if set.feedback_mode == "hints" {
                json!({"hint": hint})
            } else if reveal {
                json!({"answer": expected, "explanation": explanation})
            } else {
                json!({"status": "submitted"})
            },
            status: "done".into(),
            error: None,
            request_id: None,
            response: answer,
        },
        set: result_set,
    })
}

pub fn history(state: &PracticeState) -> Result<Vec<PracticeSet>> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM practice_sets ORDER BY id DESC LIMIT 50")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    ids.into_iter().map(|id| set_row(&store.conn, id)).collect()
}

pub fn mistakes(state: &PracticeState) -> Result<Vec<Mistake>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT m.question_id, q.set_id, s.plan_id, s.stage_id, q.kind, q.prompt, q.node_ids_json, m.reason, m.wrong_count, m.last_wrong_at, m.mastered, m.mastered_question_id FROM practice_mistakes m JOIN practice_questions q ON q.id = m.question_id JOIN practice_sets s ON s.id = q.set_id ORDER BY m.last_wrong_at DESC")?;
    let rows = statement.query_map([], |row| {
        let nodes: String = row.get(6)?;
        Ok(Mistake {
            question_id: row.get(0)?,
            set_id: row.get(1)?,
            plan_id: format!("plan-{}", row.get::<_, i64>(2)?),
            stage_id: row.get(3)?,
            kind: row.get(4)?,
            prompt: row.get(5)?,
            node_ids: serde_json::from_str(&nodes).unwrap_or_default(),
            reason: row.get(7)?,
            wrong_count: row.get::<_, i64>(8)? as u32,
            last_wrong_at: row.get(9)?,
            mastered: row.get(10)?,
            mastered_question_id: row.get(11)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub fn export_mistakes(state: &PracticeState) -> Result<Value> {
    Ok(json!({"version": 1, "exportedAt": now()?, "mistakes": mistakes(state)?}))
}

pub fn bookmark<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    question_id: i64,
    bookmarked: bool,
) -> Result<()> {
    let store = state.store()?;
    // 13#26：题目不存在时给出类型化错误（此前 FK 违例折叠为 storage_failed，
    // 文案误导排障）
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM practice_questions WHERE id = ?1)",
        params![question_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new(
            "not_found",
            "题目不存在或已随练习集删除，未收藏",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    if bookmarked {
        tx.execute("INSERT INTO practice_bookmarks (question_id, created_at) VALUES (?1, ?2) ON CONFLICT(question_id) DO NOTHING", params![question_id, now()?])?;
    } else {
        tx.execute(
            "DELETE FROM practice_bookmarks WHERE question_id = ?1",
            params![question_id],
        )?;
    }
    let event = Store::event(
        &tx,
        "practice.bookmark_changed",
        json!({"questionId": question_id, "bookmarked": bookmarked}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn set_mistake_mastered<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    question_id: i64,
    mastered: bool,
) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE practice_mistakes SET mastered = ?1, mastered_question_id = NULL WHERE question_id = ?2",
        params![mastered, question_id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "错题不存在"));
    }
    let event = Store::event(
        &tx,
        "practice.mistake_mastery_changed",
        json!({"questionId": question_id, "mastered": mastered}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel<R: Runtime>(app: &AppHandle<R>, state: &PracticeState, id: i64) -> Result<()> {
    let flag = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?
        .get(&id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_generation", "该练习没有正在生成的任务"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let request: Option<String> = store
        .conn
        .query_row(
            "SELECT request_id FROM practice_sets WHERE id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "练习集合不存在"))?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("UPDATE practice_sets SET status = 'cancelling', updated_at = ?1 WHERE id = ?2 AND status = 'generating'", params![now()?, id])?;
    if changed == 0 {
        return Err(BackendError::new(
            "no_active_generation",
            "练习生成已经结束，不能重复取消",
        ));
    }
    let event = Store::event(
        &tx,
        "practice.generation_cancel_requested",
        json!({"setId": id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    if let Some(request) = request {
        if let Err(error) = providers::cancel_chat(&state.providers, &request) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    Ok(())
}

pub fn stop(state: &PracticeState) -> Result<()> {
    let flags = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT request_id FROM practice_sets WHERE status IN ('generating', 'cancelling') AND request_id IS NOT NULL")?;
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
    subjective::stop(state)?;
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
        "generation_active",
        "练习生成任务尚未停止",
    ))
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &PracticeState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id, status FROM practice_sets WHERE status IN ('generating', 'cancelling')",
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
            BackendError::new("cancelled", "应用重启后未重新执行已取消的练习生成")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试练习生成，可恢复任务",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("UPDATE practice_sets SET status = ?1, error_code = ?2, error_message = ?3, updated_at = ?4 WHERE id = ?5", params![status, error.code, error.message, now()?, id])?;
        let event = Store::event(
            &tx,
            if cancelled {
                "practice.generation_cancelled"
            } else {
                "practice.generation_interrupted"
            },
            json!({"setId": id, "error": error}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    subjective::recover(app, state)?;
    Ok(())
}

pub fn resume<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    id: i64,
) -> Result<PracticeSetDetail> {
    let set = set_row(&state.store()?.conn, id)?;
    if !["failed", "cancelled", "interrupted"].contains(&set.status.as_str()) {
        return detail(state, id);
    }
    let mut active = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "练习任务状态不可用"))?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let previous_request: Option<String> = tx.query_row(
        "SELECT request_id FROM practice_sets WHERE id = ?1 AND status IN ('failed', 'cancelled', 'interrupted')",
        params![id],
        |row| row.get(0),
    ).optional()?.ok_or_else(|| BackendError::new("generation_active", "练习状态已改变，不能重复恢复"))?;
    if active.contains_key(&id) {
        return Err(BackendError::new(
            "generation_active",
            "该练习已在恢复，请勿重复启动",
        ));
    }
    let completed = set.status == "interrupted"
        && providers::completed_chat(&state.providers, previous_request.as_deref())?;
    let attempt = if completed {
        set.attempt
    } else {
        set.attempt + 1
    };
    let request_id = if completed {
        previous_request.unwrap()
    } else {
        format!("practice-{id}-{attempt}")
    };
    active.insert(id, Arc::new(AtomicBool::new(false)));
    let outcome = (|| -> Result<_> {
        tx.execute("UPDATE practice_sets SET status = 'generating', attempt = ?1, request_id = ?2, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE id = ?4 AND status IN ('failed', 'cancelled', 'interrupted')", params![attempt, request_id, now()?, id])?;
        Store::event(
            &tx,
            "practice.generation_resumed",
            json!({"setId": id, "attempt": attempt}),
        )
    })();
    let event = match outcome {
        Ok(event) => event,
        Err(error) => {
            active.remove(&id);
            return Err(error);
        }
    };
    if let Err(error) = tx.commit() {
        active.remove(&id);
        return Err(error.into());
    }
    drop(active);
    emit(app, &event);
    launch(app.clone(), state, id)?;
    detail(state, id)
}

#[cfg(test)]
mod tests;
