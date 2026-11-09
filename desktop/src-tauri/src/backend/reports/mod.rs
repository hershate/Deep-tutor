pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::LearningState;
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime};

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct FocusSession {
    pub id: i64,
    pub task_id: Option<i64>,
    pub status: String,
    pub planned_seconds: u64,
    pub actual_seconds: u64,
    pub remaining_seconds: u64,
    pub started_at: String,
    pub ended_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FocusOverview {
    pub active: Option<FocusSession>,
    pub recent: Vec<FocusSession>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReportMetrics {
    pub focus_seconds: u64,
    pub previous_focus_seconds: u64,
    pub practice_count: u64,
    pub correct_percent: Option<f64>,
    pub retention_percent: Option<f64>,
    pub goal_status: String,
    pub goal_detail: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrendPoint {
    pub label: String,
    pub value: Option<f64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TimeSlice {
    pub kind: String,
    pub seconds: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MasteryRow {
    pub plan_id: String,
    pub node_id: String,
    pub title: String,
    pub mastery: Option<f64>,
    pub practice_accuracy: Option<f64>,
    pub retention: Option<f64>,
    pub evidence_count: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Recommendation {
    pub id: String,
    pub title: String,
    pub detail: String,
    pub kind: String,
    pub plan_id: Option<String>,
    pub node_id: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanStats {
    pub id: String,
    pub title: String,
    pub status: String,
    pub progress: u8,
    pub focus_seconds: u64,
    pub practice_count: u64,
    pub completed_stages: u64,
    pub total_stages: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LearningReport {
    pub period: String,
    pub metrics: ReportMetrics,
    pub trend: Vec<TrendPoint>,
    pub time_composition: Vec<TimeSlice>,
    pub masteries: Vec<MasteryRow>,
    pub recommendations: Vec<Recommendation>,
    pub plans: Vec<PlanStats>,
    pub weekly: Vec<TimeSlice>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CalendarDay {
    pub date: String,
    pub minutes: u64,
    pub note: String,
    pub source: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CalendarReport {
    pub streak: u32,
    pub total_minutes: u64,
    pub note_count: u64,
    pub checkin_rate: u8,
    pub days: Vec<CalendarDay>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CheckinInput {
    pub date: String,
    pub minutes: u64,
    pub note: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdoptionResult {
    pub added: u32,
    pub focus: Option<FocusSession>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReportDownload {
    pub bytes: usize,
    pub sha256: String,
    pub title: String,
}

pub struct ReportState {
    learning: LearningState,
}

impl ReportState {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            learning: learning.shared(),
        }
    }

    fn store(&self) -> Result<Store> {
        self.learning.store()
    }
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            status TEXT NOT NULL,
            planned_seconds INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            actual_seconds INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS focus_sessions_status ON focus_sessions(status, started_at);
        -- 53#2：打卡日历在 84 天循环内按 status+ended_at 逐日过滤——叠加索引
        -- 覆盖 ended_at，dashboard 事件热触发不再 84 次全表扫
        CREATE INDEX IF NOT EXISTS focus_sessions_status_ended
            ON focus_sessions(status, ended_at);
        CREATE TABLE IF NOT EXISTS study_checkins (
            date TEXT PRIMARY KEY,
            minutes INTEGER NOT NULL,
            note TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        -- 27#6：周报 evidence() 按 created_at 过滤排序 learning_node_evidence，
        -- 证据行每次作答/复习/考试 UPSERT 且无上限
        CREATE INDEX IF NOT EXISTS learning_node_evidence_created
            ON learning_node_evidence(created_at);",
    )?;
    let mut statement = conn.prepare("PRAGMA table_info(focus_sessions)")?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if !columns.iter().any(|column| column == "paused_at") {
        conn.execute("ALTER TABLE focus_sessions ADD COLUMN paused_at TEXT", [])?;
    }
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM focus_sessions", [])?;
    conn.execute("DELETE FROM study_checkins", [])?;
    Ok(())
}

fn now() -> Result<OffsetDateTime> {
    Ok(OffsetDateTime::now_utc())
}

fn timestamp(value: OffsetDateTime) -> Result<String> {
    Ok(value.format(&Rfc3339)?)
}

fn parse_time(value: &str) -> Result<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| BackendError::new("invalid_record", "持久化时间格式无效"))
}

fn day(value: OffsetDateTime) -> String {
    value.date().to_string()
}

fn focus_row(row: &rusqlite::Row<'_>, now: OffsetDateTime) -> rusqlite::Result<FocusSession> {
    let status: String = row.get(2)?;
    let started_at: String = row.get(4)?;
    let stored: i64 = row.get(6)?;
    let actual = if status == "running" {
        (now.unix_timestamp()
            - parse_time(&started_at)
                .map_err(|error| {
                    rusqlite::Error::InvalidColumnType(
                        4,
                        error.message,
                        rusqlite::types::Type::Text,
                    )
                })?
                .unix_timestamp())
        .max(0)
    } else {
        stored
    };
    let planned: i64 = row.get(3)?;
    Ok(FocusSession {
        id: row.get(0)?,
        task_id: row.get(1)?,
        status,
        planned_seconds: planned.max(0) as u64,
        actual_seconds: actual.max(0) as u64,
        remaining_seconds: (planned - actual).max(0) as u64,
        started_at,
        ended_at: row.get(5)?,
    })
}

fn focus_by_id(conn: &Connection, id: i64) -> Result<FocusSession> {
    conn.query_row(
        "SELECT id, task_id, status, planned_seconds, started_at, ended_at, actual_seconds FROM focus_sessions WHERE id = ?1",
        params![id],
        |row| focus_row(row, now().map_err(|error| rusqlite::Error::InvalidColumnType(4, error.message, rusqlite::types::Type::Text))?),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "专注记录不存在"))
}

fn focus_overview(store: &Store) -> Result<FocusOverview> {
    let active = store.conn.query_row(
        "SELECT id, task_id, status, planned_seconds, started_at, ended_at, actual_seconds FROM focus_sessions WHERE status IN ('running', 'paused') ORDER BY id DESC LIMIT 1",
        [],
        |row| focus_row(row, now().map_err(|error| rusqlite::Error::InvalidColumnType(4, error.message, rusqlite::types::Type::Text))?),
    ).optional()?;
    let mut statement = store.conn.prepare("SELECT id, task_id, status, planned_seconds, started_at, ended_at, actual_seconds FROM focus_sessions ORDER BY id DESC LIMIT 10")?;
    let recent = statement
        .query_map([], |row| {
            focus_row(
                row,
                now().map_err(|error| {
                    rusqlite::Error::InvalidColumnType(
                        4,
                        error.message,
                        rusqlite::types::Type::Text,
                    )
                })?,
            )
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    Ok(FocusOverview { active, recent })
}

pub fn get_focus(state: &ReportState) -> Result<FocusOverview> {
    focus_overview(&state.store()?)
}

pub fn start_focus<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReportState,
    task_id: Option<i64>,
    planned_seconds: u64,
) -> Result<FocusSession> {
    if !(60..=14_400).contains(&planned_seconds) {
        return Err(BackendError::invalid(
            "invalid_focus_duration",
            "专注时长须为 60 到 14400 秒",
        ));
    }
    let store = state.store()?;
    if let Some(task_id) = task_id {
        if task_id <= 0
            || !store.conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM kv WHERE namespace = 'tasks' AND id = ?1)",
                params![task_id.to_string()],
                |row| row.get::<_, bool>(0),
            )?
        {
            return Err(BackendError::new("not_found", "专注关联的任务不存在"));
        }
    }
    let timestamp = timestamp(now()?)?;
    // 25#1：活跃检查移入 IMMEDIATE 事务——原实现 EXISTS 在 autocommit 态，
    // 并发两次 start_focus 会双双通过、各插一条 running（finish 只收尾最新
    // 一条，另一条残 running 直到重启）；持写锁后二次检查关闭窗口，
    // 对齐 projects::submit 的做法
    let tx = rusqlite::Transaction::new_unchecked(
        &store.conn,
        rusqlite::TransactionBehavior::Immediate,
    )?;
    let running: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM focus_sessions WHERE status IN ('running', 'paused'))",
        [],
        |row| row.get(0),
    )?;
    if running {
        return Err(BackendError::new(
            "focus_active",
            "已有一个进行中的专注，请先完成或取消",
        ));
    }
    tx.execute("INSERT INTO focus_sessions (task_id, status, planned_seconds, started_at, created_at, updated_at) VALUES (?1, 'running', ?2, ?3, ?3, ?3)", params![task_id, planned_seconds as i64, timestamp])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "focus.started",
        json!({"sessionId":id,"taskId":task_id,"plannedSeconds":planned_seconds}),
    )?;
    tx.commit()?;
    emit(app, &event);
    focus_by_id(&store.conn, id)
}

fn finish_focus<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReportState,
    status: &str,
) -> Result<FocusSession> {
    let store = state.store()?;
    let id: i64 = store
        .conn
        .query_row(
            "SELECT id FROM focus_sessions WHERE status IN ('running', 'paused') ORDER BY id DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("no_active_focus", "当前没有进行中的专注"))?;
    let session = focus_by_id(&store.conn, id)?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    // 56#4：挂机数小时后点「完成」按未封顶墙钟秒计入 focus_minutes 报告与
    // 日历——对齐崩溃恢复路径的 min(planned) 封顶（:411 同口径）
    let actual = session.actual_seconds.min(session.planned_seconds) as i64;
    let updated = tx.execute("UPDATE focus_sessions SET status = ?1, actual_seconds = ?2, ended_at = ?3, updated_at = ?3 WHERE id = ?4 AND status IN ('running', 'paused')", params![status, actual, timestamp, id])?;
    if updated == 0 {
        return Err(BackendError::new("no_active_focus", "当前没有进行中的专注"));
    }
    let event = Store::event(
        &tx,
        &format!("focus.{status}"),
        json!({"sessionId":id,"actualSeconds":actual,"taskId":session.task_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    focus_by_id(&store.conn, id)
}

pub fn complete_focus<R: Runtime>(app: &AppHandle<R>, state: &ReportState) -> Result<FocusSession> {
    finish_focus(app, state, "completed")
}

pub fn cancel_focus<R: Runtime>(app: &AppHandle<R>, state: &ReportState) -> Result<FocusSession> {
    finish_focus(app, state, "cancelled")
}

/// 崩溃恢复：把残留 running/paused 专注会话收尾为 interrupted。
/// 时长取暂停时的累计值，或墙钟流逝（封顶计划时长），避免停机时间全部计入统计。
pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &ReportState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id FROM focus_sessions WHERE status IN ('running', 'paused') ORDER BY id",
    )?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for id in ids {
        let session = focus_by_id(&store.conn, id)?;
        let actual = session.actual_seconds.min(session.planned_seconds) as i64;
        let timestamp = timestamp(now()?)?;
        let tx = store.conn.unchecked_transaction()?;
        tx.execute(
            "UPDATE focus_sessions SET status = 'interrupted', actual_seconds = ?2, ended_at = ?3, updated_at = ?3 WHERE id = ?1 AND status IN ('running', 'paused')",
            params![id, actual, timestamp],
        )?;
        let event = Store::event(
            &tx,
            "focus.interrupted",
            json!({"sessionId":id,"actualSeconds":actual,"taskId":session.task_id}),
        )?;
        tx.commit()?;
        emit(app, &event);
    }
    Ok(())
}

/// 56#2：stop_all 的专注收口——running/paused 会话按 min(planned) 封顶置
/// interrupted（与 recover 同口径，但 stop_all 场景无 app 句柄不发事件，
/// 由后续快照刷新对齐）；原先登出/切换不停 focus，会话停留 running 按
/// 墙钟累计，该用户再登录见「进行中且秒数疯涨」
pub fn stop(state: &ReportState) -> Result<()> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id FROM focus_sessions WHERE status IN ('running', 'paused') ORDER BY id",
    )?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let timestamp = timestamp(now()?)?;
    for id in ids {
        let session = focus_by_id(&store.conn, id)?;
        let actual = session.actual_seconds.min(session.planned_seconds) as i64;
        store.conn.execute(
            "UPDATE focus_sessions SET status = 'interrupted', actual_seconds = ?2, ended_at = ?3, updated_at = ?3 WHERE id = ?1 AND status IN ('running', 'paused')",
            params![id, actual, timestamp],
        )?;
    }
    Ok(())
}

pub fn pause_focus<R: Runtime>(app: &AppHandle<R>, state: &ReportState) -> Result<FocusSession> {
    let store = state.store()?;
    let id: i64 = store
        .conn
        .query_row(
            "SELECT id FROM focus_sessions WHERE status = 'running' ORDER BY id DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("no_active_focus", "当前没有进行中的专注"))?;
    let session = focus_by_id(&store.conn, id)?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    // 53#9：SELECT 与 UPDATE 之间的毫秒窗内并发 finish 可能已把会话置为
    // completed——changed==0 时不再照发 focus.paused（与终态矛盾的悬空事件），
    // 返回会话当前真实状态
    let changed = tx.execute("UPDATE focus_sessions SET status = 'paused', actual_seconds = ?1, paused_at = ?2, updated_at = ?2 WHERE id = ?3 AND status = 'running'", params![session.actual_seconds as i64, timestamp, id])?;
    if changed == 0 {
        tx.commit()?;
        return focus_by_id(&store.conn, id);
    }
    let event = Store::event(
        &tx,
        "focus.paused",
        json!({"sessionId":id,"actualSeconds":session.actual_seconds}),
    )?;
    tx.commit()?;
    emit(app, &event);
    focus_by_id(&store.conn, id)
}

pub fn resume_focus<R: Runtime>(app: &AppHandle<R>, state: &ReportState) -> Result<FocusSession> {
    let store = state.store()?;
    let id: i64 = store
        .conn
        .query_row(
            "SELECT id FROM focus_sessions WHERE status = 'paused' ORDER BY id DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("no_paused_focus", "当前没有暂停中的专注"))?;
    let session = focus_by_id(&store.conn, id)?;
    let started_at = timestamp(now()? - Duration::seconds(session.actual_seconds as i64))?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    // 53#9：同 pause——状态守卫未命中时不发 focus.resumed 悬空事件
    let changed = tx.execute("UPDATE focus_sessions SET status = 'running', started_at = ?1, paused_at = NULL, updated_at = ?2 WHERE id = ?3 AND status = 'paused'", params![started_at, timestamp, id])?;
    if changed == 0 {
        tx.commit()?;
        return focus_by_id(&store.conn, id);
    }
    let event = Store::event(
        &tx,
        "focus.resumed",
        json!({"sessionId":id,"actualSeconds":session.actual_seconds}),
    )?;
    tx.commit()?;
    emit(app, &event);
    focus_by_id(&store.conn, id)
}

fn period_bounds(period: &str) -> Result<(OffsetDateTime, i64, OffsetDateTime)> {
    let local = super::store::now_local();
    let today = local
        .date()
        .with_time(time::Time::MIDNIGHT)
        .assume_offset(local.offset());
    let days = match period {
        "4w" => 28,
        "8w" => 56,
        "semester" => {
            // 03#13-10：学期按“秋季 9/1 起至次年 2 月末、春季 3/1 起至 8 月末”取起点，
            // 寒暑假月归入刚结束的学期，避免 7 月把寒假一并计入“本学期”
            let year = local.year();
            let month = local.month() as u8;
            let start = if month >= 9 {
                time::Date::from_calendar_date(year, time::Month::September, 1)
            } else if month >= 3 {
                time::Date::from_calendar_date(year, time::Month::March, 1)
            } else {
                time::Date::from_calendar_date(year - 1, time::Month::September, 1)
            }
            .map_err(|_| BackendError::new("invalid_period", "无法确定学期开始日期"))?;
            (today.date() - start).whole_days().max(0) + 1
        }
        _ => {
            return Err(BackendError::invalid(
                "invalid_period",
                "报告周期须为 4w、8w 或 semester",
            ))
        }
    };
    let since = today - Duration::days(days - 1);
    let previous = since - Duration::days(days);
    Ok((since, days, previous))
}

fn task_minutes(
    store: &Store,
    since: &str,
    previous: &str,
    current_focus: &std::collections::HashMap<i64, u64>,
    previous_focus: &std::collections::HashMap<i64, u64>,
) -> Result<(u64, u64, u64, u64)> {
    let tasks = store.collection("tasks")?.value;
    let tasks = tasks
        .as_array()
        .ok_or_else(|| BackendError::new("invalid_record", "任务集合格式无效"))?;
    let since = parse_time(since)?;
    let previous = parse_time(previous)?;
    // 03#20/13#9：同一任务的“勾选计划分钟”与“专注计时秒”只取较大者——
    // 专注 25 分钟后勾选 60 分钟任务记 60 分钟，不再双计成 85 分钟
    let mut seconds = 0u64;
    let mut previous_seconds = 0u64;
    // B-8：任务未勾选/完成时间落窗之外时，其关联专注秒不再整额消失——
    // 回退为按真实计时计入当期自由专注
    let mut counted_current: std::collections::HashSet<i64> = std::collections::HashSet::new();
    let mut counted_previous: std::collections::HashSet<i64> = std::collections::HashSet::new();
    for task in tasks {
        if !task.get("done").and_then(Value::as_bool).unwrap_or(false) {
            continue;
        }
        let Some(completed) = task.get("completedAt").and_then(Value::as_str) else {
            continue;
        };
        let completed = parse_time(completed)?;
        let duration = task.get("duration").and_then(Value::as_u64).unwrap_or(0);
        let id = task.get("id").and_then(Value::as_i64).unwrap_or_default();
        if completed >= since {
            seconds += (duration * 60).max(current_focus.get(&id).copied().unwrap_or(0));
            counted_current.insert(id);
        } else if completed >= previous {
            previous_seconds += (duration * 60).max(previous_focus.get(&id).copied().unwrap_or(0));
            counted_previous.insert(id);
        }
    }
    let mut residual_current = 0u64;
    for (id, value) in current_focus {
        if !counted_current.contains(id) {
            residual_current += value;
        }
    }
    let mut residual_previous = 0u64;
    for (id, value) in previous_focus {
        if !counted_previous.contains(id) {
            residual_previous += value;
        }
    }
    Ok((
        seconds / 60,
        previous_seconds / 60,
        residual_current,
        residual_previous,
    ))
}

fn focus_minutes(store: &Store, since: &str, previous: &str) -> Result<(u64, u64)> {
    let since = parse_time(since)?;
    let previous = parse_time(previous)?;
    let mut statement = store.conn.prepare("SELECT actual_seconds, task_id, ended_at FROM focus_sessions WHERE status = 'completed' AND ended_at IS NOT NULL")?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, Option<i64>>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    // 03#20/13#9：关联任务的专注秒并入该任务的 max(计划分钟, 专注秒)；
    // 只有未关联任务的专注按实际秒全额计入
    let mut current_free = 0u64;
    let mut previous_free = 0u64;
    let mut current_tasks: std::collections::HashMap<i64, u64> = std::collections::HashMap::new();
    let mut previous_tasks: std::collections::HashMap<i64, u64> = std::collections::HashMap::new();
    for row in rows {
        let (seconds, task_id, ended) = row?;
        let ended = parse_time(&ended)?;
        let seconds = seconds.max(0) as u64;
        if ended >= since {
            match task_id {
                Some(id) => *current_tasks.entry(id).or_insert(0) += seconds,
                None => current_free += seconds,
            }
        } else if ended >= previous {
            match task_id {
                Some(id) => *previous_tasks.entry(id).or_insert(0) += seconds,
                None => previous_free += seconds,
            }
        }
    }
    let (tasks, old_tasks, residual_free, previous_residual) = task_minutes(
        store,
        &timestamp(since)?,
        &timestamp(previous)?,
        &current_tasks,
        &previous_tasks,
    )?;
    // B-8：residual 为未计入取大的关联专注秒（任务未勾选/落窗外），按真实
    // 计时并入自由专注
    Ok((
        current_free + residual_free + tasks * 60,
        previous_free + previous_residual + old_tasks * 60,
    ))
}

fn evidence(store: &Store, since: &str) -> Result<Vec<(i64, String, String, f64, f64, String)>> {
    let mut statement = store.conn.prepare("SELECT plan_id, node_id, kind, score, max_score, created_at FROM learning_node_evidence WHERE created_at >= ?1 ORDER BY created_at")?;
    let rows = statement.query_map(params![since], |row| {
        let score: f64 = row.get(3)?;
        let maximum: f64 = row.get(4)?;
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            score,
            maximum,
            row.get::<_, String>(5)?,
        ))
    })?;
    let mut collected = Vec::new();
    for row in rows {
        // 25#7：单条脏掌握证据（非有限/超界，外部改库或异常写入）只跳过该行，
        // 不再上抛——原实现令 learning_report 与 get_dashboard 整体失败，
        // 与 learning/knowledge.rs 对同一数据的静默跳过口径不一致
        let (plan_id, node_id, kind, score, maximum, created_at) = row?;
        if score.is_finite()
            && maximum.is_finite()
            && maximum > 0.0
            && score >= 0.0
            && score <= maximum
        {
            collected.push((plan_id, node_id, kind, score, maximum, created_at));
        }
    }
    Ok(collected)
}

/// 73#13a：直接接收调用方连接（get_with 同连接取方案，不再另开 Store::new）
fn node_titles(store: &super::store::Store, plan_id: i64) -> Result<HashMap<String, String>> {
    let plan = super::learning::get_with(store, &format!("plan-{plan_id}"))?;
    Ok(plan
        .result
        .map(|result| {
            result
                .graph
                .nodes
                .into_iter()
                .map(|node| (node.id, node.title))
                .collect()
        })
        .unwrap_or_default())
}

fn ratio(score: f64, maximum: f64) -> f64 {
    score / maximum
}

fn report_recommendations(masteries: &[MasteryRow]) -> Vec<Recommendation> {
    let mut candidates = masteries
        .iter()
        .filter(|row| row.evidence_count < 3 || row.mastery.unwrap_or(0.0) < 0.8)
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        left.mastery
            .unwrap_or(0.0)
            .partial_cmp(&right.mastery.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut result = candidates
        .into_iter()
        .take(3)
        .map(|row| Recommendation {
            id: format!("node:{}:{}", row.plan_id, row.node_id),
            title: format!("补强：{}", row.title),
            detail: if row.evidence_count < 3 {
                format!(
                    "当前只有 {} 条有效证据，先完成一次真实练习或复习。",
                    row.evidence_count
                )
            } else {
                format!(
                    "当前掌握估计为 {:.0}%，建议安排一个短练习并核对错因。",
                    row.mastery.unwrap_or(0.0) * 100.0
                )
            },
            kind: "practice".into(),
            plan_id: Some(row.plan_id.clone()),
            node_id: Some(row.node_id.clone()),
        })
        .collect::<Vec<_>>();
    if result.is_empty() {
        result.push(Recommendation {
            id: "focus:new".into(),
            title: "记录一次真实专注".into(),
            detail: "当前没有足够的掌握证据；从一次可核对的专注和练习开始，不虚构趋势。".into(),
            kind: "focus".into(),
            plan_id: None,
            node_id: None,
        });
    }
    result
}

fn plan_focus(
    store: &Store,
    since: &str,
    week: &str,
) -> Result<(HashMap<i64, u64>, HashMap<i64, u64>)> {
    let since = parse_time(since)?;
    let week = parse_time(week)?;
    let mut statement = store.conn.prepare("SELECT task_id, actual_seconds, ended_at FROM focus_sessions WHERE status = 'completed' AND ended_at IS NOT NULL")?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, Option<i64>>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut totals = HashMap::new();
    let mut weekly = HashMap::new();
    for row in rows {
        let (task_id, seconds, ended) = row?;
        let Some(task_id) = task_id else { continue };
        let ended = parse_time(&ended)?;
        if ended < since {
            continue;
        }
        let plan_id = store.conn.query_row("SELECT plan_id FROM learning_plan_schedule_tasks WHERE task_id = ?1 ORDER BY plan_id LIMIT 1", params![task_id], |row| row.get::<_, i64>(0)).optional()?;
        let Some(plan_id) = plan_id else { continue };
        *totals.entry(plan_id).or_insert(0) += seconds.max(0) as u64;
        if ended >= week {
            *weekly.entry(plan_id).or_insert(0) += seconds.max(0) as u64;
        }
    }
    Ok((totals, weekly))
}

fn learning_report(state: &ReportState, period: &str) -> Result<LearningReport> {
    let store = state.store()?;
    report_with(&store, period)
}

/// 73#13a：dashboard 等扇出方传入已持有的连接，不再重开
pub(crate) fn report_with(store: &super::store::Store, period: &str) -> Result<LearningReport> {
    let (since, _, previous) = period_bounds(period)?;
    let since_text = timestamp(since.to_offset(time::UtcOffset::UTC))?;
    let previous_text = timestamp(previous.to_offset(time::UtcOffset::UTC))?;
    let (focus_seconds, previous_focus_seconds) =
        focus_minutes(&store, &since_text, &previous_text)?;
    // B-11：hints 模式作答不进正确率聚合——「题目答错」可从聚合值反推，
    // 属遮蔽口径的报表旁路；47#7：脏行（score 超界/负值/缺 max）退出 AVG
    // （与 :639 evidence 路径的 finite+界内过滤同口径），不再被 as u8 饱和
    // 转换静默钳到 100% 掩盖
    // 89#1：「主动练习」只计主动作答的题——03#13-27 让交卷时未作答题也落
    // done/0 分行（判分与掌握度证据口径），原先计数与正确率把空卷全算进
    // 「主动练习」（10 题空卷即 +10 题），首页/报告/周报三处同源此聚合；
    // unanswered 标记由 exams.rs 交卷路径写入 feedback_json
    let (practice_count, correct_percent): (i64, Option<f64>) = store.conn.query_row("SELECT COUNT(*), AVG(CASE WHEN a.max_score > 0 AND a.score >= 0 AND a.score <= a.max_score THEN a.score / a.max_score END) FROM practice_answers a JOIN practice_sets s ON s.id = a.set_id WHERE a.status = 'done' AND a.created_at >= ?1 AND s.feedback_mode != 'hints' AND COALESCE(CAST(json_extract(a.feedback_json, '$.unanswered') AS INTEGER), 0) = 0", params![since_text], |row| Ok((row.get(0)?, row.get(1)?)))?;
    let retention_percent: Option<f64> = store.conn.query_row("SELECT AVG(CASE grade WHEN 'good' THEN 0.8 WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 0.6 WHEN 'again' THEN 0.0 ELSE NULL END) FROM review_history WHERE reviewed_at >= ?1", params![since_text], |row| row.get(0))?;
    // 13#6：与首页同口径——最近采用的方案优先，无采用记录回退最新。
    // 73#13a：改在已持有的连接上取回——原先每域扇出各自 Store::new
    let all_plans = super::learning::list_with(&store)?;
    let adopted = store
        .conn
        .query_row(
            // 34#9：31#19 漏改的调用点——plan_id 即 rowid，ORDER BY id 等价按
            // 方案编号倒序；与首页/planning 同口径按采用时间倒序
            "SELECT plan_id FROM learning_plan_adoptions ORDER BY created_at DESC, plan_id DESC LIMIT 1",
            [],
            |row| row.get::<_, i64>(0),
        )
        .ok();
    let latest_plan = match adopted {
        Some(adopted) => all_plans
            .iter()
            .find(|plan| super::learning::plan_id(&plan.id).ok() == Some(adopted))
            .cloned()
            .or_else(|| all_plans.first().cloned()),
        None => all_plans.first().cloned(),
    };
    let (goal_status, goal_detail) = latest_plan
        .map(|plan| {
            let detail = plan
                .result
                .map(|result| {
                    if result.schedule.within_deadline {
                        format!("{} · 当前计划日期在目标日内", plan.goal)
                    } else {
                        format!("{} · 当前计划已超过目标日", plan.goal)
                    }
                })
                .unwrap_or_else(|| format!("{} · 尚未形成完整日程", plan.goal));
            (
                if plan.status == "done" || plan.status == "degraded" {
                    "计划已生成".into()
                } else {
                    "生成中".into()
                },
                detail,
            )
        })
        .unwrap_or_else(|| {
            (
                "暂无学习方案".into(),
                "先创建真实学习方案，再评估目标节奏。".into(),
            )
        });
    let evidence = evidence(&store, &since_text)?;
    let mut trend_groups: BTreeMap<String, (f64, u64)> = BTreeMap::new();
    let mut node_groups: BTreeMap<(i64, String), (f64, u64, f64, u64, f64, u64)> = BTreeMap::new();
    // 证据时间戳按 UTC 存储：趋势分组标签换算到本地日期，与窗口边界（本地午夜）口径一致（07#9）
    let local_offset = super::store::now_local().offset();
    for (plan_id, node_id, kind, score, maximum, created_at) in &evidence {
        let date = parse_time(created_at)?;
        let label = day(date.to_offset(local_offset));
        let trend = trend_groups.entry(label).or_default();
        trend.0 += ratio(*score, *maximum);
        trend.1 += 1;
        let node = node_groups.entry((*plan_id, node_id.clone())).or_default();
        node.0 += ratio(*score, *maximum);
        node.1 += 1;
        if kind.starts_with("practice") {
            node.2 += ratio(*score, *maximum);
            node.3 += 1;
        }
        if kind == "review" {
            node.4 += ratio(*score, *maximum);
            node.5 += 1;
        }
    }
    let trend = trend_groups
        .into_iter()
        .map(|(label, (sum, count))| TrendPoint {
            label,
            value: Some(sum / count as f64),
        })
        .collect::<Vec<_>>();
    let mut titles = HashMap::new();
    for plan_id in node_groups
        .keys()
        .map(|(id, _)| *id)
        .collect::<std::collections::BTreeSet<_>>()
    {
        titles.insert(plan_id, node_titles(store, plan_id)?);
    }
    let masteries = node_groups
        .into_iter()
        .map(
            |(
                (plan_id, node_id),
                (mastery, count, practice, practice_count, review, review_count),
            )| {
                let title = titles
                    .get(&plan_id)
                    .and_then(|nodes| nodes.get(&node_id))
                    .cloned()
                    .unwrap_or(node_id.clone());
                MasteryRow {
                    plan_id: format!("plan-{plan_id}"),
                    node_id,
                    title,
                    mastery: Some(mastery / count as f64),
                    practice_accuracy: (practice_count > 0)
                        .then_some(practice / practice_count as f64),
                    retention: (review_count > 0).then_some(review / review_count as f64),
                    evidence_count: count,
                }
            },
        )
        .collect::<Vec<_>>();
    let recommendations = report_recommendations(&masteries);
    let local = super::store::now_local();
    let week = timestamp(
        local
            .date()
            .with_time(time::Time::MIDNIGHT)
            .assume_offset(local.offset())
            .to_offset(time::UtcOffset::UTC)
            - Duration::days(6),
    )?;
    let (focus_by_plan, focus_by_week) = plan_focus(&store, &since_text, &week)?;
    // 73#13a：同连接取回（list_with）
    let plans = super::learning::list_with(store)?.into_iter().map(|plan| {
        let id = super::learning::plan_id(&plan.id)?;
        let total_stages = plan.result.as_ref().map(|result| result.stages.len() as u64).unwrap_or(0);
        // 25#8：局部重生成不清理旧阶段的完成行，孤儿计数可超过当前阶段总数
        //（"3/2 已完成"）——封顶到 total_stages 保持统计自洽
        let completed_stages: i64 = store.conn.query_row("SELECT COUNT(*) FROM learning_reading_progress WHERE plan_id = ?1 AND completed = 1", params![id], |row| row.get::<_, i64>(0))?.min(i64::try_from(total_stages).unwrap_or(i64::MAX));
        // 89#6：「X 组练习」只计可用组（ready/completed）——原先把生成失败/
        // 取消的集合也计入，连续失败 3 次显示「3 组练习」而一组没做过
        let practice_count: i64 = store.conn.query_row("SELECT COUNT(*) FROM practice_sets WHERE plan_id = ?1 AND status IN ('ready', 'completed')", params![id], |row| row.get(0))?;
        Ok(PlanStats { id: plan.id.clone(), title: plan.goal, status: plan.status, progress: plan.progress, focus_seconds: focus_by_plan.get(&id).copied().unwrap_or(0), practice_count: practice_count as u64, completed_stages: completed_stages as u64, total_stages })
    }).collect::<Result<Vec<_>>>()?;
    let mut time_composition: Vec<TimeSlice> = Vec::new();
    let mut statement = store.conn.prepare("SELECT task_id, SUM(actual_seconds) FROM focus_sessions WHERE status = 'completed' AND ended_at >= ?1 GROUP BY task_id")?;
    let rows = statement.query_map(params![since_text], |row| {
        Ok((row.get::<_, Option<i64>>(0)?, row.get::<_, i64>(1)?))
    })?;
    let tasks = store.collection("tasks")?.value;
    for row in rows {
        let (task_id, seconds) = row?;
        let kind = task_id
            .and_then(|id| {
                tasks
                    .as_array()?
                    .iter()
                    .find(|task| task.get("id").and_then(Value::as_i64) == Some(id))
            })
            .and_then(|task| task.get("kind").and_then(Value::as_str))
            .unwrap_or("学习")
            .to_string();
        if let Some(existing) = time_composition.iter_mut().find(|slice| slice.kind == kind) {
            existing.seconds += seconds as u64;
        } else {
            time_composition.push(TimeSlice {
                kind,
                seconds: seconds as u64,
            });
        }
    }
    let weekly = plans
        .iter()
        .map(|plan| TimeSlice {
            kind: plan.title.clone(),
            seconds: focus_by_week
                .get(&super::learning::plan_id(&plan.id).unwrap_or(0))
                .copied()
                .unwrap_or(0),
        })
        .collect::<Vec<_>>();
    Ok(LearningReport {
        period: period.into(),
        metrics: ReportMetrics {
            focus_seconds,
            previous_focus_seconds,
            practice_count: practice_count as u64,
            correct_percent,
            retention_percent,
            goal_status,
            goal_detail,
        },
        trend,
        time_composition,
        masteries,
        recommendations,
        plans,
        weekly,
    })
}

pub fn get_report(state: &ReportState, period: Option<&str>) -> Result<LearningReport> {
    learning_report(state, period.unwrap_or("8w"))
}

pub fn get_calendar(state: &ReportState) -> Result<CalendarReport> {
    calendar_with(&state.store()?)
}

/// 73#13a：dashboard 扇出方复用调用方连接
pub(crate) fn calendar_with(store: &super::store::Store) -> Result<CalendarReport> {
    let local = super::store::now_local();
    let today = local
        .date()
        .with_time(time::Time::MIDNIGHT)
        .assume_offset(local.offset());
    let mut days = Vec::new();
    let mut total_minutes = 0;
    let mut recorded = 0;
    let mut active_start: Option<u64> = None;
    for offset in (0..84).rev() {
        let date = today - Duration::days(offset);
        let date_text = day(date);
        let next = timestamp((date + Duration::days(1)).to_offset(time::UtcOffset::UTC))?;
        let focus: i64 = store.conn.query_row("SELECT COALESCE(SUM(actual_seconds), 0) FROM focus_sessions WHERE status = 'completed' AND ended_at >= ?1 AND ended_at < ?2", params![timestamp(date.to_offset(time::UtcOffset::UTC))?, next], |row| row.get(0))?;
        let checkin = store
            .conn
            .query_row(
                "SELECT minutes, note FROM study_checkins WHERE date = ?1",
                params![date_text],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?;
        // 13#7：自报与计时并存时不再整日丢弃计时（决策“日历保留计时与
        // 自报来源”）；同一学习活动两来源可能重复描述，取较大值避免双计
        let focus_minutes = (focus.max(0) as u64) / 60;
        let has_checkin = checkin.is_some();
        let (minutes, note, source) = match checkin {
            Some((minutes, note)) => {
                let reported = minutes.max(0) as u64;
                let merged = reported.max(focus_minutes);
                // B-9：来源标签与数值实际来源一致——计时更大时标 timer，
                // 不再出现「90 分钟 · 手动打卡」实际 90 来自计时
                let source = if focus_minutes > reported {
                    "timer"
                } else {
                    "self_report"
                };
                (merged, note, source.to_string())
            }
            None if focus > 0 => (focus_minutes, String::new(), "timer".to_string()),
            None => (0, String::new(), "none".to_string()),
        };
        total_minutes += minutes;
        // 13#8：打卡率名实一致——分子只计主动自报打卡日（计时日不再虚增），
        // 分母取窗口内工作区已活跃天数（首个有记录日起算，84 天封顶）
        if has_checkin && minutes > 0 {
            recorded += 1;
        }
        if minutes > 0 && active_start.is_none() {
            // 15#1：首个活跃日距今 offset 天，活跃天数 = offset + 1
            // （今天首活跃 = 1，83 天前首活跃 = 84），不得反向
            active_start = Some(offset as u64 + 1);
        }
        days.push(CalendarDay {
            date: date_text,
            minutes,
            note,
            source,
        });
    }
    let mut streak = 0;
    let mut remaining = days.iter().rev();
    // 今天尚无记录时给一天宽限：连续链从昨天起算，不因"今天还没学"清零
    if let Some(today) = remaining.next() {
        if today.minutes > 0 {
            streak += 1;
        }
    }
    for date in remaining {
        if date.minutes > 0 {
            streak += 1;
        } else {
            break;
        }
    }
    let note_count: i64 = store.conn.query_row(
        "SELECT COUNT(*) FROM kv WHERE namespace = 'notes'",
        [],
        |row| row.get(0),
    )?;
    Ok(CalendarReport {
        streak,
        total_minutes,
        note_count: note_count as u64,
        // 13#8：分母 = 自首个活跃日以来的天数（1..=84），新工作区不再被
        // 固定 84 稀释；无任何记录时比率为 0
        checkin_rate: (recorded * 100 / active_start.unwrap_or(0).max(1)) as u8,
        days,
    })
}

pub fn save_checkin<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReportState,
    input: CheckinInput,
) -> Result<CalendarReport> {
    let date = time::Date::parse(
        &input.date,
        &time::format_description::well_known::Iso8601::DATE,
    )
    .map_err(|_| BackendError::invalid("invalid_checkin_date", "打卡日期格式无效"))?;
    let local = super::store::now_local();
    if date > local.date() {
        return Err(BackendError::invalid(
            "future_checkin",
            "不能为未到来的日期打卡",
        ));
    }
    // 25#16：日历只枚举近 84 天——更早的打卡会被接受入库但永不展示、不进
    // 统计（"存而不显"），封住下界使写入与展示口径一致
    if date < local.date() - time::Duration::days(83) {
        return Err(BackendError::invalid(
            "invalid_checkin_date",
            "只能为最近 84 天内的日期打卡",
        ));
    }
    let note = input.note.trim().to_string();
    if !(1..=1440).contains(&input.minutes) || note.is_empty() || note.len() > 4000 {
        return Err(BackendError::invalid(
            "invalid_checkin",
            "打卡分钟须为 1 到 1440，笔记须为 1 到 4000 字节",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO study_checkins (date, minutes, note, updated_at) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(date) DO UPDATE SET minutes = excluded.minutes, note = excluded.note, updated_at = excluded.updated_at", params![input.date, input.minutes as i64, note, timestamp(now()?)?])?;
    let event = Store::event(
        &tx,
        "study.checkin_saved",
        json!({"date":input.date,"minutes":input.minutes}),
    )?;
    tx.commit()?;
    emit(app, &event);
    get_calendar(state)
}

pub fn adopt_recommendations<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReportState,
    ids: Vec<String>,
    period: Option<String>,
) -> Result<AdoptionResult> {
    if ids.is_empty()
        || ids.len() > 3
        || ids.iter().collect::<std::collections::BTreeSet<_>>().len() != ids.len()
    {
        return Err(BackendError::invalid(
            "invalid_recommendations",
            "请选择一至三条不同的建议",
        ));
    }
    let report = get_report(state, period.as_deref())?;
    let store = state.store()?;
    let mut added = 0;
    let mut wants_focus = false;
    // 77#3：revision 随任务快照一次取定——原先提交前另读一次新 revision，
    // 并发采纳持有过期 tasks 数组也能通过守卫，同 id 行互相覆盖静默丢任务；
    // 改用快照 revision 后并发方收到可重试的 revision_conflict
    let tasks_collection = store.collection("tasks")?;
    let snapshot_revision = tasks_collection.revision;
    let mut tasks = tasks_collection.value;
    let tasks = tasks
        .as_array_mut()
        .ok_or_else(|| BackendError::new("invalid_record", "任务集合格式无效"))?;
    for id in ids {
        let recommendation = report
            .recommendations
            .iter()
            .find(|item| item.id == id)
            .ok_or_else(|| {
                BackendError::new("recommendation_stale", "建议已随学习证据变化，请刷新后重试")
            })?;
        if recommendation.kind == "focus" {
            wants_focus = true;
            continue;
        }
        if tasks
            .iter()
            .any(|task| task.get("title").and_then(Value::as_str) == Some(&recommendation.title))
        {
            continue;
        }
        // 34#20：id 分配走 next_free_id 的毫秒基数——max+1 紧贴前端
        // Date.now() 型 localId，同毫秒邻接即撞号（24#4/31#58 同族）
        let id: i64 = Store::next_free_id(&store.conn, "tasks")?
            .parse()
            .map_err(|_| BackendError::new("invalid_id", "任务标识无效"))?;
        tasks.push(json!({"id":id,"title":recommendation.title,"subject":"学习建议","duration":20,"done":false,"kind":"练习"}));
        added += 1;
    }
    if wants_focus && focus_overview(&store)?.active.is_some() {
        return Err(BackendError::new(
            "focus_active",
            "已有一个进行中的专注，请先完成或取消",
        ));
    }
    // 先启动专注、后提交任务：专注启动失败时（如预检查后的竞态）任务不落库，
    // 避免"报错但清单已出现新任务"的不一致（07#4）
    let focus = if wants_focus {
        Some(start_focus(app, state, None, 1500)?)
    } else {
        None
    };
    if added > 0 {
        let mutation =
            store.replace_collection("tasks", snapshot_revision, &Value::Array(tasks.clone()))?;
        if let Some(event) = mutation.event {
            emit(app, &event);
        }
    }
    Ok(AdoptionResult { added, focus })
}

pub fn export_report<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReportState,
    destination: &Path,
    period: Option<&str>,
) -> Result<ReportDownload> {
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("csv"))
    {
        return Err(BackendError::invalid(
            "invalid_report_destination",
            "学习报告必须使用 .csv 扩展名",
        ));
    }
    let report = get_report(state, period)?;
    // 11#6：与前两个 CSV 出口（export/formats.rs、providers/usage.rs）同口径——
    // 用户可控单元格以 =/+/-/@ 开头时前置引号，防表格软件按公式执行
    let escape = |value: String| {
        let mut guarded = value;
        if guarded.starts_with(['=', '+', '-', '@']) {
            guarded.insert(0, '\'');
        }
        format!("\"{}\"", guarded.replace('"', "\"\""))
    };
    let mut csv = String::from("\u{feff}指标,数值\n");
    for (name, value) in [
        ("有效学习秒", report.metrics.focus_seconds.to_string()),
        (
            "上一周期有效学习秒",
            report.metrics.previous_focus_seconds.to_string(),
        ),
        ("主动练习题数", report.metrics.practice_count.to_string()),
        (
            "正确率百分比",
            // 13#11：与页面 Math.round(value*100) 同口径输出整数百分比；
            // 15#2：指标为 [0,1] 小数，漏乘 100 会把 76.9% 输出为 1；
            // 47#7：浮点转 u8 为饱和转换，先 clamp 保证脏值不会静默变 100
            report
                .metrics
                .correct_percent
                .map(|value| format!("{}", (value * 100.0).round().clamp(0.0, 100.0) as u8))
                .unwrap_or_else(|| "暂无数据".into()),
        ),
        (
            "记忆保持率百分比",
            report
                .metrics
                .retention_percent
                .map(|value| format!("{}", (value * 100.0).round().clamp(0.0, 100.0) as u8))
                .unwrap_or_else(|| "暂无数据".into()),
        ),
        ("目标状态", report.metrics.goal_status.clone()),
        ("目标说明", report.metrics.goal_detail.clone()),
    ] {
        csv.push_str(&format!("{},{}\n", escape(name.into()), escape(value)));
    }
    csv.push_str("\n方案,状态,进度百分比,专注秒,主动练习组数,已完成阶段,总阶段\n");
    for plan in &report.plans {
        csv.push_str(&format!(
            "{},{},{},{},{},{},{}\n",
            escape(plan.title.clone()),
            escape(plan.status.clone()),
            plan.progress,
            plan.focus_seconds,
            plan.practice_count,
            plan.completed_stages,
            plan.total_stages
        ));
    }
    let bytes = csv.into_bytes();
    super::materials::atomic_export(destination, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let event = Store::event(
        &state.store()?.conn,
        "report.exported",
        json!({"period":report.period,"bytes":bytes.len(),"sha256":sha256}),
    )?;
    emit(app, &event);
    Ok(ReportDownload {
        bytes: bytes.len(),
        sha256,
        title: "知舟学习报告.csv".into(),
    })
}
