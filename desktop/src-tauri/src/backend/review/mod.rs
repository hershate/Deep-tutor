pub mod commands;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{self, LearningState};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

pub struct ReviewState {
    database_path: PathBuf,
    learning: LearningState,
}

impl ReviewState {
    pub fn new(path: &Path, learning: &LearningState) -> Self {
        Self {
            database_path: path.to_path_buf(),
            learning: learning.shared(),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let learning = LearningState::for_tests()?;
        let path = learning.database_path().to_path_buf();
        Ok(Self {
            database_path: path,
            learning,
        })
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            learning: self.learning.shared(),
        }
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewCard {
    pub id: i64,
    pub plan_id: Option<String>,
    pub node_id: Option<String>,
    pub front: String,
    pub back: String,
    pub tag: String,
    pub due_at: String,
    pub interval_days: f64,
    pub ease: f64,
    pub repetitions: u32,
    pub lapses: u32,
    pub suspended: bool,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewDay {
    pub date: String,
    pub label: String,
    pub count: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewStats {
    pub due_today: u32,
    pub retention: Option<u8>,
    pub estimated_minutes: u32,
    pub tomorrow: u32,
    pub calendar: Vec<ReviewDay>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewHistoryEntry {
    pub id: i64,
    pub grade: String,
    pub previous_interval: f64,
    pub next_interval: f64,
    pub ease: f64,
    pub reviewed_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GradeResult {
    pub card: ReviewCard,
    pub history_id: i64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CardInput {
    pub front: String,
    pub back: String,
    pub tag: Option<String>,
    pub plan_id: Option<String>,
    pub node_id: Option<String>,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

fn timestamp(value: OffsetDateTime) -> Result<String> {
    Ok(value.format(&Rfc3339)?)
}

fn day_start(date: time::Date, offset: time::UtcOffset) -> Result<String> {
    timestamp(date.with_time(time::Time::MIDNIGHT).assume_offset(offset))
}

fn parse_time(value: &str) -> Result<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| BackendError::new("invalid_persisted_time", "复习时间格式无效"))
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS review_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT,
            node_id TEXT,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            tag TEXT NOT NULL,
            due_at TEXT NOT NULL,
            interval_days REAL NOT NULL DEFAULT 0,
            ease REAL NOT NULL DEFAULT 2.5,
            repetitions INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            suspended INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS review_due_cards ON review_cards(deleted, suspended, due_at);
        CREATE TABLE IF NOT EXISTS review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL REFERENCES review_cards(id) ON DELETE CASCADE,
            grade TEXT NOT NULL,
            previous_interval REAL NOT NULL,
            next_interval REAL NOT NULL,
            ease REAL NOT NULL,
            reviewed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS review_history_card ON review_history(card_id, id);",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM review_history", [])?;
    conn.execute("DELETE FROM review_cards", [])?;
    conn.execute(
        "DELETE FROM learning_node_evidence WHERE kind = 'review'",
        [],
    )?;
    Ok(())
}

fn validate_scope(
    input: &CardInput,
    state: &ReviewState,
) -> Result<(Option<String>, Option<String>)> {
    if input.front.trim().is_empty()
        || input.front.len() > 8000
        || input.back.trim().is_empty()
        || input.back.len() > 20_000
    {
        return Err(BackendError::invalid(
            "invalid_card_content",
            "卡片正面须为 1 到 8000 字节，背面须为 1 到 20000 字节",
        ));
    }
    if input.tag.as_deref().is_some_and(|tag| tag.len() > 100) {
        return Err(BackendError::invalid(
            "invalid_card_tag",
            "卡片标签不能超过 100 字节",
        ));
    }
    if input.node_id.is_some() && input.plan_id.is_none() {
        return Err(BackendError::invalid(
            "invalid_card_scope",
            "关联知识点必须同时选择学习方案",
        ));
    }
    if let Some(id) = &input.plan_id {
        let plan = learning::get(&state.learning, id)?;
        if plan.adopted_project_id.is_none() {
            return Err(BackendError::new(
                "invalid_card_scope",
                "只能关联已采用的学习方案",
            ));
        }
        if let Some(node) = &input.node_id {
            let result = plan
                .result
                .ok_or_else(|| BackendError::new("invalid_card_scope", "方案尚未形成知识图谱"))?;
            if !result.graph.nodes.iter().any(|item| item.id == *node) {
                return Err(BackendError::new(
                    "invalid_card_scope",
                    "关联知识点不在当前方案中",
                ));
            }
        }
    }
    Ok((input.plan_id.clone(), input.node_id.clone()))
}

fn card(row: &rusqlite::Row<'_>) -> rusqlite::Result<ReviewCard> {
    Ok(ReviewCard {
        id: row.get(0)?,
        plan_id: row.get(1)?,
        node_id: row.get(2)?,
        front: row.get(3)?,
        back: row.get(4)?,
        tag: row.get(5)?,
        due_at: row.get(6)?,
        interval_days: row.get(7)?,
        ease: row.get(8)?,
        repetitions: row.get::<_, i64>(9)? as u32,
        lapses: row.get::<_, i64>(10)? as u32,
        suspended: row.get(11)?,
        created_at: row.get(12)?,
        updated_at: row.get(13)?,
    })
}

const CARD_COLUMNS: &str = "id, plan_id, node_id, front, back, tag, due_at, interval_days, ease, repetitions, lapses, suspended, created_at, updated_at";

fn get_card(conn: &Connection, id: i64) -> Result<ReviewCard> {
    conn.query_row(
        &format!("SELECT {CARD_COLUMNS} FROM review_cards WHERE id = ?1 AND deleted = 0"),
        params![id],
        card,
    )
    .optional()?
    .ok_or_else(|| BackendError::new("not_found", "记忆卡不存在或已删除"))
}

fn event(conn: &Connection, kind: &str, card_id: i64) -> Result<super::store::BackendEvent> {
    Store::event(conn, kind, json!({"cardId": card_id}))
}

pub fn create<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReviewState,
    input: CardInput,
) -> Result<ReviewCard> {
    let (plan_id, node_id) = validate_scope(&input, state)?;
    let store = state.store()?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO review_cards (plan_id, node_id, front, back, tag, due_at, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?6)",
        params![plan_id, node_id, input.front.trim(), input.back.trim(), input.tag.as_deref().map(str::trim).filter(|tag| !tag.is_empty()).unwrap_or("手动创建"), timestamp],
    )?;
    let id = tx.last_insert_rowid();
    let event = event(&tx, "review.card_created", id)?;
    tx.commit()?;
    emit(app, &event);
    get_card(&store.conn, id)
}

pub fn update<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReviewState,
    id: i64,
    input: CardInput,
) -> Result<ReviewCard> {
    if id <= 0 {
        return Err(BackendError::invalid("invalid_card_id", "记忆卡标识无效"));
    }
    let (plan_id, node_id) = validate_scope(&input, state)?;
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE review_cards SET plan_id = ?1, node_id = ?2, front = ?3, back = ?4, tag = ?5, updated_at = ?6 WHERE id = ?7 AND deleted = 0",
        params![plan_id, node_id, input.front.trim(), input.back.trim(), input.tag.as_deref().map(str::trim).filter(|tag| !tag.is_empty()).unwrap_or("手动创建"), now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "记忆卡不存在或已删除"));
    }
    let event = event(&tx, "review.card_updated", id)?;
    tx.commit()?;
    emit(app, &event);
    get_card(&store.conn, id)
}

pub fn delete<R: Runtime>(app: &AppHandle<R>, state: &ReviewState, id: i64) -> Result<()> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE review_cards SET deleted = 1, updated_at = ?1 WHERE id = ?2 AND deleted = 0",
        params![now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "记忆卡不存在或已删除"));
    }
    let event = event(&tx, "review.card_deleted", id)?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn set_suspended<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReviewState,
    id: i64,
    suspended: bool,
) -> Result<ReviewCard> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE review_cards SET suspended = ?1, updated_at = ?2 WHERE id = ?3 AND deleted = 0",
        params![suspended, now()?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "记忆卡不存在或已删除"));
    }
    let event = event(&tx, "review.card_suspended", id)?;
    tx.commit()?;
    emit(app, &event);
    get_card(&store.conn, id)
}

pub fn list(state: &ReviewState) -> Result<Vec<ReviewCard>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(&format!(
        "SELECT {CARD_COLUMNS} FROM review_cards WHERE deleted = 0 ORDER BY due_at, id LIMIT 200"
    ))?;
    let rows = statement.query_map([], card)?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub fn next(state: &ReviewState) -> Result<Option<ReviewCard>> {
    let store = state.store()?;
    store
        .conn
        .query_row(
            &format!("SELECT {CARD_COLUMNS} FROM review_cards WHERE deleted = 0 AND suspended = 0 AND due_at <= ?1 ORDER BY due_at, id LIMIT 1"),
            params![now()?],
            card,
        )
        .optional()
        .map_err(Into::into)
}

pub fn history(state: &ReviewState, card_id: i64) -> Result<Vec<ReviewHistoryEntry>> {
    let store = state.store()?;
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM review_cards WHERE id = ?1)",
        params![card_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new("not_found", "记忆卡不存在"));
    }
    let mut statement = store.conn.prepare("SELECT id, grade, previous_interval, next_interval, ease, reviewed_at FROM review_history WHERE card_id = ?1 ORDER BY id DESC LIMIT 100")?;
    let rows = statement.query_map(params![card_id], |row| {
        Ok(ReviewHistoryEntry {
            id: row.get(0)?,
            grade: row.get(1)?,
            previous_interval: row.get(2)?,
            next_interval: row.get(3)?,
            ease: row.get(4)?,
            reviewed_at: row.get(5)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub fn grade<R: Runtime>(
    app: &AppHandle<R>,
    state: &ReviewState,
    id: i64,
    grade: &str,
) -> Result<GradeResult> {
    if !["again", "hard", "good", "easy"].contains(&grade) {
        return Err(BackendError::invalid(
            "invalid_review_grade",
            "复习反馈必须为再来一次、困难、良好或轻松",
        ));
    }
    let mut store = state.store()?;
    let tx = store
        .conn
        .transaction_with_behavior(TransactionBehavior::Immediate)?;
    let card = get_card(&tx, id)?;
    if parse_time(&card.due_at)? > OffsetDateTime::now_utc() {
        return Err(BackendError::new("card_not_due", "这张卡片尚未到复习时间"));
    }
    let previous_interval = card.interval_days;
    let next_interval = match grade {
        "again" => 10.0 / 1440.0,
        "hard" if card.repetitions == 0 => 2.0,
        "good" if card.repetitions == 0 => 5.0,
        "easy" if card.repetitions == 0 => 12.0,
        "hard" => (previous_interval * 1.2).max(1.0),
        "good" => previous_interval * card.ease,
        "easy" => previous_interval * card.ease * 1.3,
        _ => unreachable!(),
    };
    let ease = match grade {
        "again" => (card.ease - 0.2).max(1.3),
        "hard" => (card.ease - 0.15).max(1.3),
        "good" => card.ease,
        "easy" => (card.ease + 0.15).min(3.0),
        _ => unreachable!(),
    };
    let due_at =
        timestamp(OffsetDateTime::now_utc() + Duration::seconds_f64(next_interval * 86_400.0))?;
    let changed = tx.execute(
        "UPDATE review_cards SET due_at = ?1, interval_days = ?2, ease = ?3, repetitions = ?4, lapses = ?5, updated_at = ?6 WHERE id = ?7 AND deleted = 0 AND suspended = 0 AND due_at <= ?8",
        params![due_at, next_interval, ease, if grade == "again" { 0 } else { card.repetitions + 1 }, if grade == "again" { card.lapses + 1 } else { card.lapses }, now()?, id, now()?],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "card_not_due",
            "这张卡片已被其他操作复习",
        ));
    }
    tx.execute(
        "INSERT INTO review_history (card_id, grade, previous_interval, next_interval, ease, reviewed_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![id, grade, previous_interval, next_interval, ease, now()?],
    )?;
    let history_id = tx.last_insert_rowid();
    if let (Some(plan_id), Some(node_id)) = (&card.plan_id, &card.node_id) {
        let score = match grade {
            "again" => 0.0,
            "hard" => 0.6,
            "good" => 0.8,
            "easy" => 1.0,
            _ => unreachable!(),
        };
        tx.execute(
            "INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at, evidence_key) VALUES (?1, ?2, 'review', ?3, 1, ?4, ?5, ?6) ON CONFLICT(plan_id, node_id, evidence_key) DO UPDATE SET score = excluded.score, max_score = excluded.max_score, details_json = excluded.details_json, created_at = excluded.created_at",
            params![learning::plan_id(plan_id)?, node_id, score, serde_json::to_string(&json!({"historyId": history_id, "cardId": id}))?, now()?, format!("review-history-{history_id}")],
        )?;
    }
    let event = Store::event(
        &tx,
        "review.graded",
        json!({"cardId": id, "historyId": history_id, "grade": grade, "dueAt": due_at, "intervalDays": next_interval}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(GradeResult {
        card: get_card(&store.conn, id)?,
        history_id,
    })
}

pub fn stats(state: &ReviewState) -> Result<ReviewStats> {
    let store = state.store()?;
    let local = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    let today = local.date();
    let tomorrow = today
        .next_day()
        .ok_or_else(|| BackendError::new("invalid_persisted_time", "无法计算明日复习日期"))?;
    let end = day_start(tomorrow, local.offset())?;
    let due_today: u32 = store.conn.query_row(
        "SELECT COUNT(*) FROM review_cards WHERE deleted = 0 AND suspended = 0 AND due_at < ?1",
        params![end],
        |row| row.get(0),
    )?;
    let tomorrow_start = end.clone();
    let day_after = day_start(tomorrow.next_day().unwrap(), local.offset())?;
    let tomorrow_count: u32 = store.conn.query_row(
        "SELECT COUNT(*) FROM review_cards WHERE deleted = 0 AND suspended = 0 AND due_at >= ?1 AND due_at < ?2",
        params![tomorrow_start, day_after],
        |row| row.get(0),
    )?;
    let retention: Option<f64> = store.conn.query_row(
        "SELECT AVG(CASE grade WHEN 'good' THEN 0.8 WHEN 'easy' THEN 1.0 WHEN 'hard' THEN 0.6 ELSE 0.0 END) FROM review_history",
        [],
        |row| row.get(0),
    )?;
    let mut calendar = Vec::new();
    for offset in 0..7 {
        let date = today.checked_add(Duration::days(offset)).unwrap();
        let next_date = date.next_day().unwrap();
        let count: u32 = store.conn.query_row(
            "SELECT COUNT(*) FROM review_cards WHERE deleted = 0 AND suspended = 0 AND due_at >= ?1 AND due_at < ?2",
            params![day_start(date, local.offset())?, day_start(next_date, local.offset())?],
            |row| row.get(0),
        )?;
        let label = if offset == 0 {
            "今天".into()
        } else {
            match date.weekday().number_from_monday() {
                1 => "周一".into(),
                2 => "周二".into(),
                3 => "周三".into(),
                4 => "周四".into(),
                5 => "周五".into(),
                6 => "周六".into(),
                _ => "周日".into(),
            }
        };
        calendar.push(ReviewDay {
            date: date.to_string(),
            label,
            count,
        });
    }
    Ok(ReviewStats {
        due_today,
        retention: retention.map(|value| (value * 100.0).round() as u8),
        estimated_minutes: (due_today as f64 * 40.0 / 60.0).ceil() as u32,
        tomorrow: tomorrow_count,
        calendar,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::BackendState;
    use std::sync::Mutex;

    fn setup() -> (tauri::App<tauri::test::MockRuntime>, ReviewState) {
        let state = ReviewState::for_tests().unwrap();
        let learning = state.learning.shared();
        let app = tauri::test::mock_builder()
            .manage(BackendState {
                store: Mutex::new(state.store().unwrap()),
                materials: learning.material_state().shared(),
                providers: learning.provider_state(),
                learning: learning.shared(),
                agent: crate::backend::agent::AgentState::for_tests().unwrap(),
                review: state.shared(),
            })
            .invoke_handler(tauri::generate_handler![
                super::commands::create_review_card,
                super::commands::update_review_card,
                super::commands::delete_review_card,
                super::commands::set_review_card_suspended,
                super::commands::list_review_cards,
                super::commands::next_review_card,
                super::commands::grade_review_card,
                super::commands::get_review_stats,
                super::commands::list_review_history,
            ])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        (app, state)
    }

    fn ipc(
        window: &tauri::WebviewWindow<tauri::test::MockRuntime>,
        command: &str,
        arguments: serde_json::Value,
    ) -> std::result::Result<serde_json::Value, serde_json::Value> {
        tauri::test::get_ipc_response(
            window,
            tauri::webview::InvokeRequest {
                cmd: command.into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: "http://tauri.localhost".parse().unwrap(),
                body: tauri::ipc::InvokeBody::Json(arguments),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.into(),
            },
        )
        .map(|body| body.deserialize::<serde_json::Value>().unwrap())
    }

    fn seed_plan(state: &ReviewState) {
        let result = json!({
            "goal": "真实复习测试", "level": "基础", "deadline": "2099-01-01", "milestone": null, "milestoneDate": null,
            "graph": {"nodes": [{"id": "prob", "title": "概率", "summary": "真实节点", "difficulty": 1, "required": true, "estimatedMinutes": 15, "citations": []}], "edges": []},
            "stages": [],
            "schedule": {"startDate": "2026-01-01", "endDate": "2026-01-02", "totalMinutes": 0, "days": 0, "withinDeadline": true, "milestoneDate": null, "withinMilestone": null, "entries": []},
            "resources": {},
            "validation": {
                "dependencyClosure": {"status": "pass", "details": ""}, "difficultyStep": {"status": "pass", "details": ""},
                "cognitiveLoad": {"status": "pass", "details": ""}, "milestoneFeasibility": {"status": "pass", "details": ""},
                "gapAnalysis": {"status": "pass", "details": ""}, "passed": true, "fixes": []
            },
            "warnings": []
        });
        let store = state.store().unwrap();
        let input = json!({"goal": "真实复习测试", "level": "基础", "dailyMinutes": 45, "deadline": "2099-01-01", "materialIds": [], "providerId": null, "milestone": null, "milestoneDate": null});
        store.conn.execute(
            "INSERT INTO learning_plans (id, status, provider_id, input_json, artifacts_json, result_json, current_step, version, created_at, updated_at) VALUES (1, 'done', 'p1', ?1, '{}', ?2, 8, 1, 'now', 'now')",
            params![input.to_string(), result.to_string()],
        ).unwrap();
        store.conn.execute("INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (1, 1, 'now')", []).unwrap();
    }

    fn input() -> CardInput {
        CardInput {
            front: "P(A) 是什么？".into(),
            back: "先验概率".into(),
            tag: Some("概率".into()),
            plan_id: Some("plan-1".into()),
            node_id: Some("prob".into()),
        }
    }

    fn force_due(state: &ReviewState, id: i64) {
        state
            .store()
            .unwrap()
            .conn
            .execute(
                "UPDATE review_cards SET due_at = '2000-01-01T00:00:00Z' WHERE id = ?1",
                params![id],
            )
            .unwrap();
    }

    #[test]
    fn review_cards_crud_schedule_stats_and_evidence_are_real() {
        let (app, state) = setup();
        seed_plan(&state);
        let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        let created = ipc(&window, "create_review_card", json!({"input": input()})).unwrap();
        let id = created["id"].as_i64().unwrap();
        assert_eq!(created["ease"], 2.5);
        assert_eq!(created["repetitions"], 0);
        assert_eq!(
            ipc(&window, "next_review_card", json!({})).unwrap()["id"],
            id
        );
        let initial = ipc(&window, "get_review_stats", json!({})).unwrap();
        assert_eq!(initial["dueToday"], 1);
        assert_eq!(initial["retention"], serde_json::Value::Null);
        assert_eq!(initial["estimatedMinutes"], 1);
        let invalid = ipc(&window, "create_review_card", json!({"input": {"front": "错误节点", "back": "不会创建", "planId": "plan-1", "nodeId": "missing"}})).unwrap_err();
        assert_eq!(invalid["code"], "invalid_card_scope");
        let updated = ipc(&window, "update_review_card", json!({"id": id, "input": {"front": "新的问题", "back": "新的答案", "tag": "概率", "planId": "plan-1", "nodeId": "prob"}})).unwrap();
        assert_eq!(updated["front"], "新的问题");
        let good = ipc(
            &window,
            "grade_review_card",
            json!({"id": id, "grade": "good"}),
        )
        .unwrap();
        assert_eq!(good["card"]["intervalDays"], 5.0);
        assert_eq!(good["card"]["repetitions"], 1);
        assert!(ipc(&window, "next_review_card", json!({}))
            .unwrap()
            .is_null());
        assert_eq!(
            ipc(
                &window,
                "grade_review_card",
                json!({"id": id, "grade": "good"})
            )
            .unwrap_err()["code"],
            "card_not_due"
        );
        assert_eq!(
            ipc(&window, "get_review_stats", json!({})).unwrap()["retention"],
            80
        );
        force_due(&state, id);
        let again = ipc(
            &window,
            "grade_review_card",
            json!({"id": id, "grade": "again"}),
        )
        .unwrap();
        assert_eq!(again["card"]["repetitions"], 0);
        assert_eq!(again["card"]["lapses"], 1);
        assert!(again["card"]["intervalDays"].as_f64().unwrap() < 0.007);
        force_due(&state, id);
        let hard = ipc(
            &window,
            "grade_review_card",
            json!({"id": id, "grade": "hard"}),
        )
        .unwrap();
        assert_eq!(hard["card"]["intervalDays"], 2.0);
        assert_eq!(hard["card"]["ease"], 2.15);
        force_due(&state, id);
        let easy = ipc(
            &window,
            "grade_review_card",
            json!({"id": id, "grade": "easy"}),
        )
        .unwrap();
        assert_eq!(easy["card"]["intervalDays"], 5.59);
        assert_eq!(easy["card"]["ease"], 2.3);
        assert_eq!(
            ipc(&window, "get_review_stats", json!({})).unwrap()["retention"],
            60
        );
        let history = ipc(&window, "list_review_history", json!({"cardId": id})).unwrap();
        assert_eq!(history.as_array().unwrap().len(), 4);
        let store = state.store().unwrap();
        let (score, key): (f64, String) = store.conn.query_row("SELECT score, evidence_key FROM learning_node_evidence WHERE kind = 'review' AND node_id = 'prob' ORDER BY id DESC LIMIT 1", [], |row| Ok((row.get(0)?, row.get(1)?))).unwrap();
        assert_eq!(score, 1.0);
        assert_eq!(key, "review-history-4");
        drop(store);
        ipc(
            &window,
            "set_review_card_suspended",
            json!({"id": id, "suspended": true}),
        )
        .unwrap();
        assert!(ipc(&window, "next_review_card", json!({}))
            .unwrap()
            .is_null());
        ipc(
            &window,
            "set_review_card_suspended",
            json!({"id": id, "suspended": false}),
        )
        .unwrap();
        force_due(&state, id);
        assert_eq!(
            ipc(&window, "next_review_card", json!({})).unwrap()["id"],
            id
        );
        ipc(&window, "delete_review_card", json!({"id": id})).unwrap();
        assert!(ipc(&window, "next_review_card", json!({}))
            .unwrap()
            .is_null());
        assert_eq!(
            ipc(&window, "list_review_cards", json!({}))
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            0
        );
        let history = ipc(&window, "list_review_history", json!({"cardId": id})).unwrap();
        assert_eq!(history.as_array().unwrap().len(), 4);
    }

    #[test]
    fn concurrent_grade_is_applied_once_and_reset_clears_review_evidence() {
        let (app, state) = setup();
        seed_plan(&state);
        let card = create(app.handle(), &state, input()).unwrap();
        let mut workers = Vec::new();
        for _ in 0..2 {
            let app = app.handle().clone();
            let state = state.shared();
            workers.push(std::thread::spawn(move || {
                grade(&app, &state, card.id, "good")
            }));
        }
        let outcomes = workers
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(outcomes.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            outcomes
                .iter()
                .filter(|result| result
                    .as_ref()
                    .is_err_and(|error| error.code == "card_not_due"))
                .count(),
            1
        );
        assert_eq!(history(&state, card.id).unwrap().len(), 1);
        let store = state.store().unwrap();
        let evidence: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM learning_node_evidence WHERE kind = 'review'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(evidence, 1);
        clear_all(&store.conn).unwrap();
        let remaining: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM learning_node_evidence WHERE kind = 'review'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(remaining, 0);
        assert!(next(&state).unwrap().is_none());
    }
}
