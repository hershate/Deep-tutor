use super::{
    now, set_row, sources, validate_citations, AnswerResult, AnswerView, PracticeSetDetail,
    PracticeState, RubricCriterion,
};
use crate::backend::commands::emit;
use crate::backend::error::{BackendError, Result};
use crate::backend::learning::{self, Citation};
use crate::backend::providers::{self, ChatMessage};
use crate::backend::store::{self, Store};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};

pub const GRADING_SYSTEM: &str = "你是学习测评评分器。只能依据题目、学生作答、给定评分要点和资料原文评分，不增加评分项，不迎合学生，不虚构资料。只返回要求的 JSON 对象。";

/// 123#4：同时在飞的主观评分上限——整卷快速提交时线程/连接/模型请求
/// 按题扇出原先无界；4 与生成域单 worker 口径同量级，评分请求本身
/// 有独立的取消与超时约束
pub const MAX_CONCURRENT_GRADING: usize = 4;

fn ensure_column(conn: &Connection, table: &str, name: &str, declaration: &str) -> Result<()> {
    // 59#3：与 variants 共用 store 的幂等补列实现（46#4），消除域内复制漂移面
    store::ensure_column(conn, table, name, declaration)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    ensure_column(
        conn,
        "practice_questions",
        "rubric_json",
        "rubric_json TEXT NOT NULL DEFAULT '[]'",
    )?;
    ensure_column(
        conn,
        "practice_answers",
        "attempt",
        "attempt INTEGER NOT NULL DEFAULT 1",
    )?;
    ensure_column(conn, "practice_answers", "error_code", "error_code TEXT")?;
    ensure_column(
        conn,
        "practice_answers",
        "error_message",
        "error_message TEXT",
    )?;
    Ok(())
}

pub fn validate_rubric(rubric: &[RubricCriterion]) -> Result<()> {
    if !(2..=6).contains(&rubric.len()) {
        return Err(BackendError::new(
            "invalid_model_output",
            "主观题必须有 2 到 6 项评分要点",
        ));
    }
    let mut ids = HashSet::new();
    let mut total = 0.0;
    for criterion in rubric {
        if criterion.id.trim().is_empty()
            || criterion.id.len() > 100
            || criterion.description.trim().is_empty()
            || criterion.description.len() > 2000
            || !criterion.points.is_finite()
            || criterion.points <= 0.0
            || criterion.points > 1.0
            || !ids.insert(criterion.id.trim())
        {
            return Err(BackendError::new(
                "invalid_model_output",
                "评分要点标识、说明或分值无效",
            ));
        }
        total += criterion.points;
    }
    if (total - 1.0).abs() > 0.0001 {
        return Err(BackendError::new(
            "invalid_model_output",
            "评分要点分值总和必须为 1",
        ));
    }
    Ok(())
}

pub fn pending_answers(conn: &Connection, set_id: i64) -> Result<Vec<AnswerView>> {
    let mut statement = conn.prepare(
        "SELECT id, question_id, response_json, status, grader_request_id, error_code, error_message FROM practice_answers WHERE set_id = ?1 AND status != 'done' ORDER BY id",
    )?;
    let rows = statement.query_map(params![set_id], |row| {
        let response: String = row.get(2)?;
        let status: String = row.get(3)?;
        let code: Option<String> = row.get(5)?;
        let message: Option<String> = row.get(6)?;
        Ok(AnswerView {
            id: row.get(0)?,
            question_id: row.get(1)?,
            score: None,
            max_score: None,
            correct: None,
            feedback: json!({"status": status}),
            status,
            error: code
                .map(|code| BackendError::new(&code, message.as_deref().unwrap_or("评分未完成"))),
            request_id: row.get(4)?,
            response: serde_json::from_str(&response).unwrap_or(Value::Null),
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

fn cancelled(state: &PracticeState, answer_id: i64) -> Result<bool> {
    Ok(state
        .grading_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "主观评分状态不可用"))?
        .get(&answer_id)
        .is_none_or(|flag| flag.load(Ordering::Acquire)))
}

pub fn submit<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    set_id: i64,
    question_id: i64,
    answer: Value,
) -> Result<AnswerResult> {
    let actual = answer
        .as_str()
        .map(str::trim)
        .filter(|answer| !answer.is_empty())
        .ok_or_else(|| BackendError::new("invalid_answer", "主观题答案不能为空"))?;
    if actual.len() > 30_000 {
        return Err(BackendError::new(
            "invalid_answer",
            "主观题答案不能超过 30000 字节",
        ));
    }
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
    super::exams::ensure_accepting(&set)?;
    let rubric: String = tx.query_row(
        "SELECT rubric_json FROM practice_questions WHERE id = ?1 AND set_id = ?2 AND kind IN ('short', 'essay')",
        params![question_id, set_id],
        |row| row.get(0),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "主观题不存在"))?;
    let rubric: Vec<RubricCriterion> = serde_json::from_str(&rubric)?;
    validate_rubric(&rubric)?;
    let existing: Option<String> = tx
        .query_row(
            "SELECT status FROM practice_answers WHERE question_id = ?1",
            params![question_id],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(status) = existing {
        return Err(BackendError::new(
            if status == "done" {
                "answer_already_graded"
            } else {
                "answer_already_submitted"
            },
            if status == "done" {
                "这道题已判分"
            } else {
                "这道题已有作答记录，请恢复评分而不是重复提交"
            },
        ));
    }
    tx.execute(
        "INSERT INTO practice_answers (question_id, set_id, response_json, score, max_score, correct, feedback_json, status, created_at) VALUES (?1, ?2, ?3, 0, 1, 0, '{\"status\":\"grading\"}', 'grading', ?4)",
        params![question_id, set_id, serde_json::to_string(&answer)?, now()?],
    )?;
    let answer_id = tx.last_insert_rowid();
    let request_id = format!("practice-grade-{answer_id}-1");
    tx.execute(
        "UPDATE practice_answers SET grader_request_id = ?1 WHERE id = ?2",
        params![request_id, answer_id],
    )?;
    let event = Store::event(
        &tx,
        "practice.answer_submitted",
        json!({"setId": set_id, "questionId": question_id, "answerId": answer_id, "status": "grading"}),
    )?;
    // 141#1：登记收窄为锁内瞬时插入（agent send 同款）——原先
    // grading_active.lock() 覆盖整个 IMMEDIATE 事务，BEGIN 在 busy_timeout
    // 上等待长写事务期间锁不释放，cancel/stop 在同一把锁上排队，取消入口
    // 在最需要取消的窗口被堵死；commit 失败补偿移除登记
    {
        let mut active = state
            .grading_active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "主观评分状态不可用"))?;
        active.insert(answer_id, Arc::new(AtomicBool::new(false)));
    }
    if let Err(error) = tx.commit() {
        let mut active = state
            .grading_active
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        active.remove(&answer_id);
        return Err(error.into());
    }
    emit(app, &event);
    launch(app.clone(), state, answer_id)?;
    Ok(AnswerResult {
        answer: AnswerView {
            id: answer_id,
            question_id,
            score: None,
            max_score: None,
            correct: None,
            feedback: json!({"status": "grading"}),
            status: "grading".into(),
            error: None,
            request_id: Some(request_id),
            response: answer,
        },
        set: set_row(&store.conn, set_id)?,
    })
}

fn launch<R: Runtime>(app: AppHandle<R>, state: &PracticeState, answer_id: i64) -> Result<()> {
    let worker_app = app.clone();
    let worker_state = state.shared();
    let active = Arc::clone(&state.grading_active);
    let result = super::super::security::thread_builder()
        .name(format!("practice-grade-{answer_id}"))
        .spawn(move || {
            // 123#4：并发闸——在飞评分压到 MAX_CONCURRENT_GRADING，多余的
            // 在此排队；排队期间取消/停机照常生效（标志命中即按取消收尾，
            // stop 的 15 秒等待窗不会被永久的许可等待卡死）
            let mut held = false;
            loop {
                {
                    let mut running = worker_state
                        .grading_permits
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    if *running < MAX_CONCURRENT_GRADING {
                        *running += 1;
                        held = true;
                        break;
                    }
                }
                if cancelled(&worker_state, answer_id).unwrap_or(true) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            if held {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    grade(&worker_app, &worker_state, answer_id)
                }));
                // 130#9：fail() 自身 panic（DB 层 bug 级事件）不得跳过其后的许可
                // 递减与 active 清理——否则 4 个许可占满后评分域瘫痪需重启；
                // 双 panic 在此处退化为「评分行停在 grading、可 abandon 重试」
                let failure = match result {
                    Ok(Ok(())) => None,
                    Ok(Err(error)) => Some(error),
                    Err(_) => Some(BackendError::new("task_panicked", "主观评分任务异常终止")),
                };
                if let Some(error) = failure {
                    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        fail(&worker_app, &worker_state, answer_id, error)
                    }));
                }
                let mut running = worker_state
                    .grading_permits
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                *running = running.saturating_sub(1);
            } else {
                let _ = fail(
                    &worker_app,
                    &worker_state,
                    answer_id,
                    BackendError::new("cancelled", "主观评分已取消"),
                );
            }
            // 90#11：锁中毒恢复继续用（73#16 语义）——静默跳过会让 grading
            // 残留死键，恢复/重试被误判「评分进行中」
            let mut active = active
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            active.remove(&answer_id);
        });
    if result.is_err() {
        // 90#11：同上，恢复继续用
        let mut active = state
            .grading_active
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        active.remove(&answer_id);
        fail(
            &app,
            state,
            answer_id,
            BackendError::new("task_start_failed", "评分任务未能启动，可恢复评分"),
        )?;
        return Err(BackendError::new(
            "task_start_failed",
            "无法启动主观评分任务",
        ));
    }
    Ok(())
}

struct GradingTask {
    answer_id: i64,
    set_id: i64,
    question_id: i64,
    status: String,
    response: String,
    request_id: Option<String>,
    kind: String,
    prompt: String,
    expected: String,
    explanation: String,
    rubric: String,
    citations: String,
    node_ids: String,
    provider_id: String,
    plan_id: i64,
    feedback_mode: String,
    mode: String,
}

fn task(conn: &Connection, answer_id: i64) -> Result<GradingTask> {
    conn.query_row(
        "SELECT a.id, a.set_id, a.question_id, a.status, a.response_json, a.grader_request_id, q.kind, q.prompt, q.answer_json, q.explanation, q.rubric_json, q.citations_json, q.node_ids_json, s.provider_id, s.plan_id, s.feedback_mode, s.mode FROM practice_answers a JOIN practice_questions q ON q.id = a.question_id JOIN practice_sets s ON s.id = a.set_id WHERE a.id = ?1",
        params![answer_id],
        |row| Ok(GradingTask {
            answer_id: row.get(0)?, set_id: row.get(1)?, question_id: row.get(2)?, status: row.get(3)?,
            response: row.get(4)?, request_id: row.get(5)?, kind: row.get(6)?, prompt: row.get(7)?,
            expected: row.get(8)?, explanation: row.get(9)?, rubric: row.get(10)?, citations: row.get(11)?,
            node_ids: row.get(12)?, provider_id: row.get(13)?, plan_id: row.get(14)?,
            feedback_mode: row.get(15)?, mode: row.get(16)?,
        }),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "作答记录不存在"))
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GradedCriterion {
    id: String,
    score: f64,
    evidence: String,
    feedback: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GradingDraft {
    criteria: Vec<GradedCriterion>,
    overall_feedback: String,
    citations: Vec<Citation>,
}

fn evaluate(
    draft: GradingDraft,
    rubric: &[RubricCriterion],
    task: &GradingTask,
    pass_ratio: f64,
) -> Result<(f64, bool, Value)> {
    if draft.criteria.len() != rubric.len()
        || draft.overall_feedback.trim().is_empty()
        || draft.overall_feedback.len() > 6000
    {
        return Err(BackendError::new(
            "invalid_model_output",
            "评分项数量或总体反馈无效",
        ));
    }
    let mut seen = HashSet::new();
    let mut criteria = Vec::new();
    let mut total = 0.0;
    for criterion in rubric {
        let result = draft
            .criteria
            .iter()
            .find(|item| item.id == criterion.id)
            .ok_or_else(|| {
                BackendError::new("invalid_model_output", "评分结果缺少题目保存的评分项")
            })?;
        if !seen.insert(&criterion.id)
            || !result.score.is_finite()
            || !(0.0..=1.0).contains(&result.score)
            || result.evidence.trim().is_empty()
            || result.feedback.trim().is_empty()
            || result.evidence.len() > 6000
            || result.feedback.len() > 6000
        {
            return Err(BackendError::new(
                "invalid_model_output",
                "评分结果包含无效、重复或不完整的评分项",
            ));
        }
        let awarded = (criterion.points * result.score * 1_000_000.0).round() / 1_000_000.0;
        total += awarded;
        criteria.push(json!({
            "id": criterion.id, "description": criterion.description,
            "score": awarded, "maxScore": criterion.points,
            "evidence": result.evidence, "feedback": result.feedback,
        }));
    }
    // 77#11a：±1e-4 容差校验在 validate_rubric——超出容差的 rubric 已被拒收，
    // 此处 clamp 只兜浮点累加尾差，不掩盖非法输入
    let score = total.clamp(0.0, 1.0);
    // 77#11b：达标阈值取本组 set 的 pass_score_percent（原先硬编码 0.8，
    // 错题本判定与考试通过口径脱节）
    let correct = score >= pass_ratio;
    let expected: Value = serde_json::from_str(&task.expected)?;
    Ok((
        score,
        correct,
        json!({
            "answer": expected, "explanation": task.explanation,
            "criteria": criteria, "overallFeedback": draft.overall_feedback,
            "citations": draft.citations,
        }),
    ))
}

fn grade<R: Runtime>(app: &AppHandle<R>, state: &PracticeState, answer_id: i64) -> Result<()> {
    let store = state.store()?;
    let task = task(&store.conn, answer_id)?;
    if task.status != "grading" || cancelled(state, answer_id)? {
        return Err(BackendError::new("cancelled", "主观评分已取消"));
    }
    let request_id = task
        .request_id
        .clone()
        .ok_or_else(|| BackendError::new("grading_request_missing", "作答缺少评分请求标识"))?;
    let (_, source_list, _) = sources(state, &format!("plan-{}", task.plan_id))?;
    let citations: Vec<Citation> = serde_json::from_str(&task.citations)?;
    validate_citations(&citations, &source_list)?;
    let rubric: Vec<RubricCriterion> = serde_json::from_str(&task.rubric)?;
    validate_rubric(&rubric)?;
    let expected: Value = serde_json::from_str(&task.expected)?;
    let response: Value = serde_json::from_str(&task.response)?;
    let prompt = serde_json::to_string(&json!({
        "questionKind": task.kind, "question": task.prompt,
        "referenceAnswer": expected, "rubric": rubric,
        "studentAnswer": response, "citations": citations,
        "requiredOutput": {
            "criteria": [{"id": "评分项标识", "score": "0到1的完成比例", "evidence": "评分依据", "feedback": "具体反馈"}],
            "overallFeedback": "总体反馈", "citations": [{"materialId": "资料数字ID", "quote": "资料连续原文"}],
        },
    }))?;
    let messages = vec![
        ChatMessage {
            role: "system".into(),
            content: GRADING_SYSTEM.into(),
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
        Some(&task.provider_id),
        &messages,
        4096,
        None,
        None,
        false,
    )?;
    if result.status != "done" {
        return Err(result
            .error
            .unwrap_or_else(|| BackendError::new("provider_error", "评分请求未完成")));
    }
    if cancelled(state, answer_id)? {
        return Err(BackendError::new(
            "cancelled",
            "主观评分已取消，未写入模型评分",
        ));
    }
    let draft: GradingDraft = learning::pipeline::parse_model_json(&result.output)?;
    validate_citations(&draft.citations, &source_list)?;
    let set = set_row(&store.conn, task.set_id)?;
    let (score, correct, feedback) = evaluate(
        draft,
        &rubric,
        &task,
        f64::from(set.pass_score_percent.unwrap_or(80)) / 100.0,
    )?;
    let overall_feedback = feedback["overallFeedback"]
        .as_str()
        .unwrap_or("评分未达到掌握阈值")
        .to_string();
    let timestamp = now()?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let changed = tx.execute(
        "UPDATE practice_answers SET status = 'done', score = ?1, max_score = 1, correct = ?2, feedback_json = ?3, error_code = NULL, error_message = NULL WHERE id = ?4 AND status = 'grading'",
        params![score, correct, serde_json::to_string(&feedback)?, task.answer_id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "cancelled",
            "主观评分已取消，未写入模型评分",
        ));
    }
    // B-11（23#2）：hints 模式承诺「只给提示不显示答案」——主观题的总体反馈
    // 文本即答案信号，写错题本会经错题本 UI 与错题变式提示词（variants JOIN
    // practice_mistakes）两个旁路泄露；与客观题路径（practice/mod.rs）同守卫
    if !correct && task.feedback_mode != "hints" {
        tx.execute(
            "INSERT INTO practice_mistakes (question_id, reason, last_wrong_at) VALUES (?1, ?2, ?3) ON CONFLICT(question_id) DO UPDATE SET wrong_count = wrong_count + 1, last_wrong_at = excluded.last_wrong_at, mastered = 0, mastered_question_id = NULL",
            params![task.question_id, overall_feedback, timestamp],
        )?;
    }
    // 31#12：hints 遮蔽旁路——evidence 照写真实得分会让知识图谱页
    //「N 项测评记录 · 0/1」反推对错；与错题本同口径跳过。
    // 101#4：进行中的考试延迟到交卷补写（与客观题路径同守卫）
    if task.feedback_mode != "hints" && task.mode != "exam" {
        let nodes: Vec<String> = serde_json::from_str(&task.node_ids)?;
        for node in nodes {
            tx.execute(
                "INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at, evidence_key) VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, ?7) ON CONFLICT(plan_id, node_id, evidence_key) DO UPDATE SET score = excluded.score, max_score = excluded.max_score, details_json = excluded.details_json, created_at = excluded.created_at",
                params![task.plan_id, node, "practice", score,
                    serde_json::to_string(&json!({"setId": task.set_id, "questionId": task.question_id, "answerId": answer_id, "requestId": request_id}))?,
                    timestamp, format!("practice-answer-{answer_id}")],
            )?;
        }
    }
    let answered: u32 = tx.query_row(
        "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status = 'done'",
        params![task.set_id],
        |row| row.get(0),
    )?;
    let set = set_row(&tx, task.set_id)?;
    let completed = answered == set.total_questions && set.mode != "exam";
    if completed {
        tx.execute("UPDATE practice_sets SET status = 'completed', score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), completed_at = ?1, updated_at = ?1 WHERE id = ?2", params![timestamp, task.set_id])?;
    } else {
        tx.execute("UPDATE practice_sets SET score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), updated_at = ?1 WHERE id = ?2", params![timestamp, task.set_id])?;
    }
    let reveal = task.feedback_mode == "immediate" || (task.feedback_mode != "hints" && completed);
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
        json!({
            "setId": task.set_id, "questionId": task.question_id, "answerId": answer_id,
            "answered": answered, "correct": reveal.then_some(correct), "requestId": request_id,
        }),
    )?];
    // 23#2：hints 模式不下发掌握事件——事件本身即"答对"信号，与遮蔽口径一致
    if let Some(source_question_id) = super::variants::mark_mastered(
        &tx,
        task.question_id,
        correct && task.feedback_mode != "hints",
    )? {
        events.push(Store::event(
            &tx,
            "practice.mistake_mastered_by_variant",
            json!({"setId": task.set_id, "sourceQuestionId": source_question_id, "variantQuestionId": task.question_id, "answerId": answer_id, "requestId": request_id}),
        )?);
    }
    tx.commit()?;
    for event in events {
        emit(app, &event);
    }
    Ok(())
}

fn fail<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    answer_id: i64,
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
        "UPDATE practice_answers SET status = ?1, error_code = ?2, error_message = ?3 WHERE id = ?4 AND status IN ('grading', 'cancelling')",
        params![status, error.code, error.message, answer_id],
    )?;
    if changed == 0 {
        return Ok(());
    }
    let (set_id, question_id): (i64, i64) = tx.query_row(
        "SELECT set_id, question_id FROM practice_answers WHERE id = ?1",
        params![answer_id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let event = Store::event(
        &tx,
        if status == "cancelled" {
            "practice.answer_cancelled"
        } else {
            "practice.answer_failed"
        },
        json!({
            "setId": set_id, "questionId": question_id, "answerId": answer_id, "error": error,
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    answer_id: i64,
) -> Result<PracticeSetDetail> {
    let flag = state
        .grading_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "主观评分状态不可用"))?
        .get(&answer_id)
        .cloned()
        .ok_or_else(|| BackendError::new("no_active_grading", "该作答没有正在评分的任务"))?;
    flag.store(true, Ordering::Release);
    let store = state.store()?;
    let task = task(&store.conn, answer_id)?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE practice_answers SET status = 'cancelling' WHERE id = ?1 AND status = 'grading'",
        params![answer_id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "no_active_grading",
            "评分已经结束，不能重复取消",
        ));
    }
    let event = Store::event(
        &tx,
        "practice.answer_cancel_requested",
        json!({
            "setId": task.set_id, "questionId": task.question_id, "answerId": answer_id,
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    if let Some(request_id) = task.request_id {
        if let Err(error) = providers::cancel_chat(&state.providers, &request_id) {
            if error.code != "no_active_request" {
                return Err(error);
            }
        }
    }
    super::detail(state, task.set_id)
}

/// 123#7：主观评分永久失败的终局出口——恢复评分是首选，但模型反复
/// invalid_model_output 时作答既不能重答（question_id 已占用）也没有
/// 删除命令，考试被 grading_in_progress 永久挡在交卷外。显式放弃把该
/// 作答按 0 分收尾：用户主动选择、feedback 标记 abandoned、不写模型
/// 评分——不违反「不伪造评分结果」。与未作答同口径不进错题本
///（03#13-27：无判分依据的 0 分 ≠ 做错）。
pub fn abandon<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    answer_id: i64,
) -> Result<PracticeSetDetail> {
    let store = state.store()?;
    let task = task(&store.conn, answer_id)?;
    match task.status.as_str() {
        "done" => {
            return Err(BackendError::new("answer_already_graded", "这道题已判分"));
        }
        "grading" | "cancelling" => {
            return Err(BackendError::new(
                "grading_in_progress",
                "评分仍在进行，请先取消或等待结束再放弃",
            ));
        }
        "failed" | "cancelled" | "interrupted" => {}
        _ => {
            return Err(BackendError::new(
                "invalid_answer_status",
                "作答状态不支持放弃",
            ));
        }
    }
    let tx = store.conn.unchecked_transaction()?;
    let timestamp = now()?;
    let changed = tx.execute(
        "UPDATE practice_answers SET status = 'done', score = 0, max_score = 1, correct = 0, feedback_json = ?1, error_code = NULL, error_message = NULL WHERE id = ?2 AND status IN ('failed', 'cancelled', 'interrupted')",
        params![
            serde_json::to_string(&json!({"abandoned": true, "message": "已放弃该题评分，按 0 分计。"}))?,
            answer_id
        ],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "answer_already_graded",
            "这道题的评分状态已改变，请刷新后重试",
        ));
    }
    // 与 grade() 同守卫：hints 遮蔽不写 evidence；考试延迟到交卷补写
    //（finalize 的 done 行回填会把本题按 0 分计入）
    if task.feedback_mode != "hints" && task.mode != "exam" {
        let nodes: Vec<String> = serde_json::from_str(&task.node_ids)?;
        for node in nodes {
            tx.execute(
                "INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at, evidence_key) VALUES (?1, ?2, 'practice', 0, 1, ?3, ?4, ?5) ON CONFLICT(plan_id, node_id, evidence_key) DO UPDATE SET score = excluded.score, max_score = excluded.max_score, details_json = excluded.details_json, created_at = excluded.created_at",
                params![
                    task.plan_id,
                    node,
                    serde_json::to_string(&json!({"setId": task.set_id, "questionId": task.question_id, "answerId": answer_id, "abandoned": true}))?,
                    timestamp,
                    format!("practice-answer-{answer_id}")
                ],
            )?;
        }
    }
    let answered: u32 = tx.query_row(
        "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status = 'done'",
        params![task.set_id],
        |row| row.get(0),
    )?;
    let set = set_row(&tx, task.set_id)?;
    let completed = answered == set.total_questions && set.mode != "exam";
    if completed {
        tx.execute("UPDATE practice_sets SET status = 'completed', score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), completed_at = ?1, updated_at = ?1 WHERE id = ?2", params![timestamp, task.set_id])?;
    } else {
        tx.execute("UPDATE practice_sets SET score = (SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = practice_sets.id AND status = 'done'), updated_at = ?1 WHERE id = ?2", params![timestamp, task.set_id])?;
    }
    let event = Store::event(
        &tx,
        "practice.answer_abandoned",
        json!({"setId": task.set_id, "questionId": task.question_id, "answerId": answer_id, "answered": answered}),
    )?;
    tx.commit()?;
    emit(app, &event);
    super::detail(state, task.set_id)
}

pub fn stop(state: &PracticeState) -> Result<()> {
    let flags = state
        .grading_active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "主观评分状态不可用"))?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    for flag in flags {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT grader_request_id FROM practice_answers WHERE status IN ('grading', 'cancelling') AND grader_request_id IS NOT NULL")?;
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
    // 123#4：等待窗按在飞评分数放宽——固定 15 秒在并发闸排队的整卷
    // 提交场景可能不够（每个在飞请求各自需要收到取消并收尾）
    let running: usize = state
        .grading_permits
        .lock()
        .map(|count| *count)
        .unwrap_or(0);
    let deadline = Instant::now() + Duration::from_secs(15 + 15 * running as u64);
    while Instant::now() < deadline {
        if state
            .grading_active
            .lock()
            .map(|active| active.is_empty())
            .unwrap_or(false)
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new("grading_active", "主观评分任务尚未停止"))
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &PracticeState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT id, set_id, question_id, status FROM practice_answers WHERE status IN ('grading', 'cancelling')")?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, set_id, question_id, previous) in rows {
        let cancelled = previous == "cancelling";
        let status = if cancelled {
            "cancelled"
        } else {
            "interrupted"
        };
        let error = if cancelled {
            BackendError::new("cancelled", "应用重启后未重新执行已取消的评分")
        } else {
            BackendError::new(
                "request_interrupted",
                "应用重启后未自动重试评分，可恢复任务",
            )
        };
        let tx = store.conn.unchecked_transaction()?;
        // 112#1：同 learning——活跃态守卫防仍存活线程的终态被覆写
        let changed = tx.execute("UPDATE practice_answers SET status = ?1, error_code = ?2, error_message = ?3 WHERE id = ?4 AND status IN ('grading', 'cancelling')", params![status, error.code, error.message, id])?;
        if changed == 0 {
            continue;
        }
        let event = Store::event(
            &tx,
            if cancelled {
                "practice.answer_cancelled"
            } else {
                "practice.answer_interrupted"
            },
            json!({
                "setId": set_id, "questionId": question_id, "answerId": id, "error": error,
            }),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

pub fn resume<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    answer_id: i64,
) -> Result<PracticeSetDetail> {
    let initial = task(&state.store()?.conn, answer_id)?;
    if initial.status == "done" || initial.status == "grading" || initial.status == "cancelling" {
        return super::detail(state, initial.set_id);
    }
    if !["failed", "cancelled", "interrupted"].contains(&initial.status.as_str()) {
        return Err(BackendError::new(
            "invalid_grading_status",
            "当前作答状态不能恢复评分",
        ));
    }
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let (status, previous_request, attempt): (String, Option<String>, u32) = tx.query_row(
        "SELECT status, grader_request_id, attempt FROM practice_answers WHERE id = ?1",
        params![answer_id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )?;
    if !["failed", "cancelled", "interrupted"].contains(&status.as_str()) {
        return Err(BackendError::new(
            "grading_active",
            "评分状态已改变，不能重复恢复",
        ));
    }
    let reusable = status == "interrupted"
        && providers::completed_chat(&state.providers, previous_request.as_deref())?;
    let attempt = if reusable {
        attempt
    } else {
        // 137#3：对齐 learning 域 25#6 防线——裸 +1 在 release 下回绕
        attempt
            .checked_add(1)
            .ok_or_else(|| BackendError::new("too_many_attempts", "评分重试次数已用尽"))?
    };
    let request_id = if reusable {
        previous_request.unwrap()
    } else {
        format!("practice-grade-{answer_id}-{attempt}")
    };
    tx.execute(
        "UPDATE practice_answers SET status = 'grading', grader_request_id = ?1, attempt = ?2, error_code = NULL, error_message = NULL WHERE id = ?3 AND status IN ('failed', 'cancelled', 'interrupted')",
        params![request_id, attempt, answer_id],
    )?;
    let event = Store::event(
        &tx,
        "practice.answer_grading_resumed",
        json!({
            "setId": initial.set_id, "questionId": initial.question_id, "answerId": answer_id, "attempt": attempt,
        }),
    )?;
    // 141#1：与 submit 同款——锁内只做 contains_key 复查与登记（原子），
    // IMMEDIATE 事务移出锁窗口，commit 失败补偿移除登记
    {
        let mut active = state
            .grading_active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "主观评分状态不可用"))?;
        if active.contains_key(&answer_id) {
            return Err(BackendError::new(
                "grading_active",
                "评分状态已改变，不能重复恢复",
            ));
        }
        active.insert(answer_id, Arc::new(AtomicBool::new(false)));
    }
    if let Err(error) = tx.commit() {
        let mut active = state
            .grading_active
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        active.remove(&answer_id);
        return Err(error.into());
    }
    emit(app, &event);
    launch(app.clone(), state, answer_id)?;
    super::detail(state, initial.set_id)
}
