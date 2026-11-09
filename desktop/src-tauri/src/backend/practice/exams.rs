use super::{now, set_row, PracticeSet, PracticeSetDetail, PracticeState};
use crate::backend::commands::emit;
use crate::backend::error::{BackendError, Result};
use crate::backend::learning;
use crate::backend::store::{self, Store};
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::collections::HashSet;
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

fn ensure_column(conn: &Connection, table: &str, name: &str, declaration: &str) -> Result<()> {
    // 59#3：与 variants 共用 store 的幂等补列实现（46#4），消除域内复制漂移面
    store::ensure_column(conn, table, name, declaration)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    ensure_column(
        conn,
        "practice_sets",
        "exam_time_limit_minutes",
        "exam_time_limit_minutes INTEGER",
    )?;
    ensure_column(
        conn,
        "practice_sets",
        "exam_started_at",
        "exam_started_at TEXT",
    )?;
    ensure_column(conn, "practice_sets", "exam_deadline", "exam_deadline TEXT")?;
    ensure_column(
        conn,
        "practice_sets",
        "pass_score_percent",
        "pass_score_percent INTEGER",
    )?;
    Ok(())
}

pub fn remaining(set: &PracticeSet) -> Result<(bool, Option<i64>)> {
    if set.mode != "exam" || set.status != "ready" {
        return Ok((false, None));
    }
    let Some(deadline) = set.exam_deadline.as_deref() else {
        // 47#1：deadline 在学生首次拉卷时才落位（start_exam_timer）——
        // 尚未开考的试卷不受时限约束，原先此处报 exam_not_started
        return Ok((false, None));
    };
    let deadline = OffsetDateTime::parse(deadline, &Rfc3339)
        .map_err(|_| BackendError::new("invalid_persisted_time", "考试截止时间格式无效"))?;
    let seconds = (deadline - OffsetDateTime::now_utc())
        .whole_seconds()
        .max(0);
    Ok((seconds <= 0, Some(seconds)))
}

pub fn ensure_accepting(set: &PracticeSet) -> Result<()> {
    if set.mode == "exam" && remaining(set)?.0 {
        return Err(BackendError::new(
            "exam_deadline_exceeded",
            "考试时间已结束，不能继续作答；请提交已保存的答案并交卷",
        ));
    }
    Ok(())
}

pub fn finalize<R: Runtime>(
    app: &AppHandle<R>,
    state: &PracticeState,
    id: i64,
) -> Result<PracticeSetDetail> {
    let initial = set_row(&state.store()?.conn, id)?;
    if initial.mode != "exam" {
        return Err(BackendError::new("not_an_exam", "这组练习不是模拟考试"));
    }
    if initial.status == "completed" {
        return super::detail(state, id);
    }
    if initial.status != "ready" {
        return Err(BackendError::new(
            "set_not_ready",
            "试卷尚未生成完成，不能交卷",
        ));
    }
    let (expired, _) = remaining(&initial)?;
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let current_status: String = tx.query_row(
        "SELECT status FROM practice_sets WHERE id = ?1",
        params![id],
        |row| row.get(0),
    )?;
    if current_status == "completed" {
        drop(tx);
        return super::detail(state, id);
    }
    if current_status != "ready" {
        return Err(BackendError::new(
            "set_not_ready",
            "试卷状态已改变，不能重复交卷",
        ));
    }
    let pending: u32 = tx.query_row(
        "SELECT COUNT(*) FROM practice_answers WHERE set_id = ?1 AND status != 'done'",
        params![id],
        |row| row.get(0),
    )?;
    if pending > 0 {
        return Err(BackendError::new(
            "grading_in_progress",
            "仍有作答未完成评分，请完成或恢复评分后再交卷；不会伪造评分结果",
        ));
    }
    let answered: HashSet<i64> = tx
        .prepare("SELECT question_id FROM practice_answers WHERE set_id = ?1")?
        .query_map(params![id], |row| row.get(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?
        .into_iter()
        .collect();
    let questions = tx
        .prepare("SELECT id, kind, answer_json, explanation, node_ids_json FROM practice_questions WHERE set_id = ?1 ORDER BY position")?
        .query_map(params![id], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?, row.get::<_, String>(4)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let timestamp = now()?;
    let plan_id = learning::plan_id(&initial.plan_id)?;
    let mut unanswered = 0;
    for (question_id, _kind, expected, explanation, nodes) in questions {
        if answered.contains(&question_id) {
            continue;
        }
        let expected: Value = serde_json::from_str(&expected)?;
        let feedback = json!({
            "answer": expected,
            "explanation": if explanation.trim().is_empty() { "未作答，按 0 分计。".into() } else { format!("未作答，按 0 分计。{explanation}") },
            "unanswered": true,
        });
        tx.execute(
            "INSERT INTO practice_answers (question_id, set_id, response_json, score, max_score, correct, feedback_json, status, created_at) VALUES (?1, ?2, 'null', 0, 1, 0, ?3, 'done', ?4)",
            params![question_id, id, serde_json::to_string(&feedback)?, timestamp],
        )?;
        let answer_id = tx.last_insert_rowid();
        // 03#13-27：未作答≠做错——不再写入错题本，避免空题灌满错题与变式来源；
        // 0 分答案与掌握度证据仍如实记录
        let nodes: Vec<String> = serde_json::from_str(&nodes)?;
        for node in nodes {
            tx.execute(
                "INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at, evidence_key) VALUES (?1, ?2, 'exam', 0, 1, ?3, ?4, ?5) ON CONFLICT(plan_id, node_id, evidence_key) DO NOTHING",
                params![plan_id, node, serde_json::to_string(&json!({"examId": id, "questionId": question_id, "answerId": answer_id, "unanswered": true}))?, timestamp, format!("practice-answer-{answer_id}")],
            )?;
        }
        unanswered += 1;
    }
    let score: f64 = tx.query_row(
        "SELECT COALESCE(SUM(score), 0) FROM practice_answers WHERE set_id = ?1 AND status = 'done'",
        params![id],
        |row| row.get(0),
    )?;
    tx.execute(
        "UPDATE practice_sets SET status = 'completed', score = ?1, completed_at = ?2, updated_at = ?2 WHERE id = ?3 AND status = 'ready'",
        params![score, timestamp, id],
    )?;
    let event = Store::event(
        &tx,
        "practice.exam_completed",
        json!({
            "setId": id, "score": score, "maxScore": initial.max_score,
            "passScorePercent": initial.pass_score_percent,
            "unanswered": unanswered, "expired": expired,
        }),
    )?;
    tx.commit()?;
    emit(app, &event);
    super::detail(state, id)
}
