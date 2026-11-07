pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::LearningState;
use super::store::Store;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Manager, Runtime};
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime};

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct SearchResult {
    pub kind: String,
    pub id: String,
    pub title: String,
    pub subtitle: String,
    pub page: String,
    pub plan_id: Option<String>,
    pub stage_id: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NotificationItem {
    pub id: String,
    pub kind: String,
    pub title: String,
    pub body: String,
    pub page: String,
    pub created_at: String,
    pub read_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NotificationOverview {
    pub items: Vec<NotificationItem>,
    pub unread_count: u64,
    pub suppressed: bool,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct LearningProfile {
    pub name: String,
    pub role: String,
    pub goal: String,
    pub language: String,
    pub weekly_hours: u32,
    pub learning_style: String,
    pub explanation_style: String,
    pub quiet_start: String,
    pub quiet_end: String,
    pub quiet_enabled: bool,
    pub diagnostic_started_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProfileInput {
    pub name: String,
    pub role: String,
    pub goal: String,
    pub language: String,
    pub weekly_hours: u32,
    pub learning_style: String,
    pub explanation_style: String,
    pub quiet_start: String,
    pub quiet_end: String,
    pub quiet_enabled: bool,
    pub diagnostic_started_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardTask {
    pub title: String,
    pub detail: String,
    pub minutes: u64,
    pub kind: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardItem {
    pub kind: String,
    pub title: String,
    pub detail: String,
    pub date: String,
    pub page: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardOverview {
    pub name: String,
    pub goal: String,
    pub next_task: Option<DashboardTask>,
    pub progress: u8,
    pub deadline: Option<String>,
    pub review_due: u32,
    pub review_minutes: u32,
    pub streak: u32,
    pub week: Vec<super::reports::TimeSlice>,
    pub focus_seconds: u64,
    pub practice_count: u64,
    pub retention_percent: Option<f64>,
    pub upcoming: Vec<DashboardItem>,
    pub recommendation: Option<super::reports::Recommendation>,
}

pub struct EngagementState {
    learning: LearningState,
}

impl EngagementState {
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
        "CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            page TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT
        );",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM notifications", [])?;
    Ok(())
}

fn now() -> Result<OffsetDateTime> {
    Ok(OffsetDateTime::now_utc())
}

fn timestamp(value: OffsetDateTime) -> Result<String> {
    Ok(value.format(&Rfc3339)?)
}

fn text<'a>(object: &'a Value, key: &str, fallback: &'a str) -> &'a str {
    object.get(key).and_then(Value::as_str).unwrap_or(fallback)
}

fn preferences(store: &Store) -> Result<Value> {
    Ok(store.collection("preferences")?.value)
}

pub fn get_profile<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
) -> Result<LearningProfile> {
    let store = state.store()?;
    let preferences = preferences(&store)?;
    let security = app
        .try_state::<super::security::SecurityState>()
        .map(|state| state.shared())
        .unwrap_or_else(|| super::security::SecurityState::new(state.learning.database_path()));
    let fallback = super::security::initialize_session(app, &security)
        .map(|user| user.name)
        .unwrap_or_else(|_| "本地学习者".into());
    Ok(LearningProfile {
        name: text(&preferences, "profileName", &fallback).into(),
        role: text(&preferences, "learningRole", "自学者").into(),
        goal: text(&preferences, "learningGoal", "").into(),
        language: text(&preferences, "language", "zh-CN").into(),
        weekly_hours: preferences
            .get("weeklyHours")
            .and_then(Value::as_u64)
            .unwrap_or(15) as u32,
        learning_style: text(&preferences, "learningStyle", "案例与练习结合").into(),
        explanation_style: text(&preferences, "explanationStyle", "先追问，再针对讲解").into(),
        quiet_start: text(&preferences, "quietStart", "22:00").into(),
        quiet_end: text(&preferences, "quietEnd", "08:00").into(),
        quiet_enabled: preferences
            .get("quietEnabled")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        diagnostic_started_at: preferences
            .get("diagnosticStartedAt")
            .and_then(Value::as_str)
            .map(Into::into),
    })
}

fn valid_time(value: &str) -> bool {
    let Some((hour, minute)) = value.split_once(':') else {
        return false;
    };
    hour.len() == 2
        && minute.len() == 2
        && hour.parse::<u32>().is_ok_and(|hour| hour < 24)
        && minute.parse::<u32>().is_ok_and(|minute| minute < 60)
}

fn validate_profile(input: &ProfileInput) -> Result<()> {
    if input.name.trim().is_empty()
        || input.name.len() > 80
        || input.role.len() > 100
        || input.goal.len() > 200
        || !["zh-CN", "en-US"].contains(&input.language.as_str())
        || ![8, 15, 25].contains(&input.weekly_hours)
        || !["案例与练习结合", "先系统阅读，再集中练习", "以项目实践为主"]
            .contains(&input.learning_style.as_str())
        || !["先追问，再针对讲解", "直接给出完整讲解", "苏格拉底式引导"]
            .contains(&input.explanation_style.as_str())
        || !valid_time(&input.quiet_start)
        || !valid_time(&input.quiet_end)
    {
        return Err(BackendError::invalid(
            "invalid_profile",
            "学习档案、语言、每周时长、学习方式或免打扰时间不符合约定",
        ));
    }
    if let Some(date) = &input.diagnostic_started_at {
        OffsetDateTime::parse(date, &Rfc3339)
            .map_err(|_| BackendError::invalid("invalid_diagnostic_time", "学习诊断时间无效"))?;
    }
    Ok(())
}

pub fn save_profile<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
    input: ProfileInput,
) -> Result<LearningProfile> {
    validate_profile(&input)?;
    let store = state.store()?;
    let mut preferences = preferences(&store)?;
    preferences["profileName"] = Value::String(input.name.trim().into());
    preferences["learningRole"] = Value::String(input.role.trim().into());
    preferences["learningGoal"] = Value::String(input.goal.trim().into());
    preferences["language"] = Value::String(input.language);
    preferences["weeklyHours"] = Value::from(input.weekly_hours);
    preferences["learningStyle"] = Value::String(input.learning_style);
    preferences["explanationStyle"] = Value::String(input.explanation_style);
    preferences["quietStart"] = Value::String(input.quiet_start);
    preferences["quietEnd"] = Value::String(input.quiet_end);
    preferences["quietEnabled"] = Value::Bool(input.quiet_enabled);
    preferences["diagnosticStartedAt"] = input
        .diagnostic_started_at
        .map(Value::String)
        .unwrap_or(Value::Null);
    let revision = store.collection("preferences")?.revision;
    let mutation = store.replace_collection("preferences", revision, &preferences)?;
    if let Some(event) = mutation.event {
        emit(app, &event);
    }
    get_profile(app, state)
}

pub fn restart_diagnostic<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
) -> Result<LearningProfile> {
    let profile = get_profile(app, state)?;
    save_profile(
        app,
        state,
        ProfileInput {
            name: profile.name,
            role: profile.role,
            goal: profile.goal,
            language: profile.language,
            weekly_hours: profile.weekly_hours,
            learning_style: profile.learning_style,
            explanation_style: profile.explanation_style,
            quiet_start: profile.quiet_start,
            quiet_end: profile.quiet_end,
            quiet_enabled: profile.quiet_enabled,
            diagnostic_started_at: Some(timestamp(now()?)?),
        },
    )
}

fn escape_like(query: &str) -> String {
    query
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}

fn result(
    kind: &str,
    id: impl Into<String>,
    title: impl Into<String>,
    subtitle: impl Into<String>,
    page: &str,
    plan_id: Option<String>,
    stage_id: Option<String>,
) -> SearchResult {
    SearchResult {
        kind: kind.into(),
        id: id.into(),
        title: title.into(),
        subtitle: subtitle.into(),
        page: page.into(),
        plan_id,
        stage_id,
    }
}

fn pages(query: &str) -> Vec<SearchResult> {
    let pages = [
        ("today", "今日"),
        ("generator", "AI 方案生成"),
        ("plan", "学习计划"),
        ("study", "学习空间"),
        ("map", "知识地图"),
        ("agent", "Agent 助手"),
        ("practice", "练习测评"),
        ("review", "记忆复习"),
        ("library", "笔记与 Wiki"),
        ("projects", "项目作品"),
        ("reports", "学习报告"),
        ("community", "学习社区"),
        ("providers", "模型供应商"),
        ("integrations", "插件与事件"),
        ("settings", "设置与偏好"),
    ];
    pages
        .into_iter()
        .filter(|(_, title)| {
            query.is_empty() || title.to_lowercase().contains(&query.to_lowercase())
        })
        .map(|(page, title)| result("page", page, title, "功能页面", page, None, None))
        .collect()
}

pub fn search(state: &EngagementState, query: String) -> Result<Vec<SearchResult>> {
    let query = query.trim().to_string();
    if query.len() > 200 {
        return Err(BackendError::invalid(
            "invalid_search_query",
            "搜索内容不能超过 200 字节",
        ));
    }
    if query.is_empty() {
        return Ok(pages(""));
    }
    let store = state.store()?;
    let mut results = pages(&query);
    let needle = format!("%{}%", escape_like(&query));
    let mut statement = store.conn.prepare("SELECT namespace, id, data FROM kv WHERE namespace IN ('notes', 'tasks', 'projects') AND data LIKE ?1 ESCAPE '\\' COLLATE NOCASE")?;
    let rows = statement.query_map(params![needle], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    for row in rows {
        let (namespace, id, data) = row?;
        let data: Value = serde_json::from_str(&data)?;
        let title = text(&data, "title", &id).to_string();
        match namespace.as_str() {
            "notes" => results.push(result(
                "note",
                id,
                title,
                format!("笔记 · {}", text(&data, "tag", "未分类")),
                "library",
                None,
                None,
            )),
            "tasks" => results.push(result(
                "task",
                id,
                title,
                format!("任务 · {}", text(&data, "subject", "未分类")),
                "plan",
                None,
                None,
            )),
            "projects" => results.push(result(
                "project",
                id,
                title,
                format!("项目 · {}", text(&data, "due", "未设置日期")),
                "projects",
                None,
                None,
            )),
            _ => {}
        }
    }
    drop(statement);
    let mut statement = store.conn.prepare("SELECT id, title, kind, status FROM materials WHERE title LIKE ?1 ESCAPE '\\' COLLATE NOCASE")?;
    let materials = statement
        .query_map(params![needle], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (id, title, kind, status) in materials {
        results.push(result(
            "material",
            id.to_string(),
            title,
            format!("{kind} · {status}"),
            "library",
            None,
            None,
        ));
    }
    for plan in super::learning::list(&state.learning)? {
        if plan.goal.to_lowercase().contains(&query.to_lowercase()) {
            results.push(result(
                "plan",
                plan.id.clone(),
                plan.goal.clone(),
                format!("学习方案 · {}", plan.status),
                "plan",
                Some(plan.id.clone()),
                None,
            ));
        }
        if let Some(plan_result) = plan.result {
            // 学习空间只渲染已采用方案：未采用方案的节点不能作为跳转结果，
            // 否则点击后会落到无关方案的内容
            if plan.adopted_project_id.is_none() {
                continue;
            }
            for node in plan_result.graph.nodes {
                // 13#4：字段间加空格，避免标题尾字+摘要首字拼出查询词的跨边界误命中
                if !format!("{} {}", node.title, node.summary)
                    .to_lowercase()
                    .contains(&query.to_lowercase())
                {
                    continue;
                }
                let mastery: Option<f64> = store.conn.query_row("SELECT AVG(score / max_score) FROM learning_node_evidence WHERE plan_id = ?1 AND node_id = ?2", params![super::learning::plan_id(&plan.id)?, node.id], |row| row.get(0))?;
                let stage_id = plan_result
                    .stages
                    .iter()
                    .find(|stage| stage.node_ids.contains(&node.id))
                    .map(|stage| stage.id.clone());
                results.push(result(
                    "node",
                    node.id.clone(),
                    node.title,
                    mastery
                        .map(|value| format!("知识节点 · 掌握度 {:.0}%", value * 100.0))
                        .unwrap_or_else(|| "知识节点 · 尚无证据".into()),
                    "map",
                    Some(plan.id.clone()),
                    stage_id,
                ));
            }
        }
    }
    let lower = query.to_lowercase();
    // 13#3：原按 kind 字母序（material<node<note<page）排序，命中 ≥40 时
    // "功能页面"导航入口被系统性挤出；改为页面导航优先，内容类随后
    let kind_priority = |kind: &str| match kind {
        "page" => 0u8,
        "task" | "plan" => 1,
        "note" | "material" => 2,
        _ => 3,
    };
    results.sort_by_key(|item| {
        (
            !item.title.to_lowercase().starts_with(&lower),
            kind_priority(item.kind.as_str()),
            item.kind.clone(),
            item.title.clone(),
        )
    });
    results.truncate(40);
    Ok(results)
}

fn quiet(preferences: &Value, now: OffsetDateTime) -> bool {
    if !preferences
        .get("quietEnabled")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        return false;
    }
    let minutes = now.hour() as u32 * 60 + now.minute() as u32;
    let start = text(preferences, "quietStart", "22:00")
        .split(':')
        .filter_map(|part| part.parse::<u32>().ok())
        .collect::<Vec<_>>();
    let end = text(preferences, "quietEnd", "08:00")
        .split(':')
        .filter_map(|part| part.parse::<u32>().ok())
        .collect::<Vec<_>>();
    if start.len() != 2 || end.len() != 2 {
        return false;
    }
    let (start, end) = (start[0] * 60 + start[1], end[0] * 60 + end[1]);
    if start == end {
        true
    } else if start < end {
        minutes >= start && minutes < end
    } else {
        minutes >= start || minutes < end
    }
}

fn add_notification(
    conn: &Connection,
    id: &str,
    kind: &str,
    title: &str,
    body: &str,
    page: &str,
) -> Result<()> {
    conn.execute("INSERT OR IGNORE INTO notifications (id, kind, title, body, page, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)", params![id, kind, title, body, page, timestamp(now()?)?])?;
    Ok(())
}

fn notification_overview(state: &EngagementState) -> Result<NotificationOverview> {
    let store = state.store()?;
    let preferences = preferences(&store)?;
    if preferences
        .get("notifications")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let local = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
        let today = local.date().to_string();
        // 边界转 UTC 后格式化：due_at 以 UTC 存储，字典序比较要求两侧时区后缀一致
        let tomorrow = timestamp(
            (local
                .date()
                .with_time(time::Time::MIDNIGHT)
                .assume_offset(local.offset())
                + Duration::days(1))
            .to_offset(time::UtcOffset::UTC),
        )?;
        let due: i64 = store.conn.query_row(
            "SELECT COUNT(*) FROM review_cards WHERE deleted = 0 AND suspended = 0 AND due_at < ?1",
            params![tomorrow],
            |row| row.get(0),
        )?;
        if due > 0 {
            let minutes = (due * 2).min(60);
            add_notification(
                &store.conn,
                &format!("review-due-{today}"),
                "review",
                &format!("{due} 张记忆卡已到期"),
                &format!("现在适合进行一次主动回忆，预计 {minutes} 分钟。"),
                "review",
            )?;
        }
        // C-10：与 today 同源取本地日期——此前 recent_floor 用 UTC now()，
        // 东八区凌晨窗口实际跨越 8-9 个日历日
        let recent_floor = (local - Duration::days(7)).date().to_string();
        // 13#2：原 WHERE date <= today 无下界，全部历史逾期都计入；
        // 收敛为近 7 天窗口，数字不再随陈年未勾选任务虚高。C-10：标题
        // 与查询口径一致（此前查询 7 天标题却写「今日」）
        let mut statement = store.conn.prepare(
            "SELECT task_id FROM learning_plan_schedule_tasks WHERE date <= ?1 AND date >= ?2",
        )?;
        let ids = statement
            .query_map(params![today, recent_floor], |row| row.get::<_, i64>(0))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(statement);
        let tasks = store.collection("tasks")?.value;
        let pending = ids
            .into_iter()
            .filter(|id| {
                tasks.as_array().into_iter().flatten().any(|task| {
                    task.get("id").and_then(Value::as_i64) == Some(*id)
                        && !task.get("done").and_then(Value::as_bool).unwrap_or(false)
                })
            })
            .collect::<Vec<_>>();
        if !pending.is_empty() {
            add_notification(
                &store.conn,
                &format!("tasks-due-{today}"),
                "plan",
                &format!("近 7 天有 {} 项方案任务待完成", pending.len()),
                "请按方案完成当前任务，也可在方案中调整安排。",
                "plan",
            )?;
        }
        let end = timestamp(now()? + Duration::hours(24))?;
        let mut statement = store.conn.prepare("SELECT id, title, start_at FROM community_rooms WHERE status = 'scheduled' AND start_at <= ?1 AND start_at >= ?2")?;
        let rooms = statement
            .query_map(params![end, timestamp(now()?)?], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(statement);
        for (id, title, start) in rooms {
            let reserved: bool = store.conn.query_row("SELECT EXISTS(SELECT 1 FROM community_reservations WHERE room_id = ?1 AND cancelled_at IS NULL)", params![id], |row| row.get(0))?;
            if reserved {
                add_notification(
                    &store.conn,
                    &format!("room-{id}"),
                    "community",
                    &format!("学习预约「{title}」即将开始"),
                    &format!("开始时间：{start}"),
                    "community",
                )?;
            }
        }
        if preferences
            .get("weeklyReport")
            .and_then(Value::as_bool)
            .unwrap_or(true)
            && local.weekday() == time::Weekday::Sunday
        {
            let report = super::reports::get_report(
                &super::reports::ReportState::new(&state.learning),
                Some("4w"),
            )?;
            add_notification(
                &store.conn,
                &format!("weekly-report-{today}"),
                "report",
                "本周学习复盘已准备好",
                &format!(
                    "真实记录：{} 秒有效学习，{} 道主动练习。",
                    report.metrics.focus_seconds, report.metrics.practice_count
                ),
                "reports",
            )?;
        }
        let since = timestamp(now()? - Duration::hours(24))?;
        let mut statement = store.conn.prepare("SELECT id, input_json, version FROM learning_plans WHERE status IN ('done', 'degraded') AND updated_at >= ?1")?;
        let plans = statement
            .query_map(params![since], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(statement);
        for (id, input, version) in plans {
            let goal = serde_json::from_str::<Value>(&input)?
                .get("goal")
                .and_then(Value::as_str)
                .unwrap_or("学习方案")
                .to_string();
            add_notification(
                &store.conn,
                &format!("plan-ready-{id}-{version}"),
                "plan",
                "学习方案已生成",
                &format!("「{goal}」已保存，可查看完整日程。"),
                "plan",
            )?;
        }
    }
    let unread_count: i64 = store.conn.query_row(
        "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL",
        [],
        |row| row.get(0),
    )?;
    let mut statement = store.conn.prepare("SELECT id, kind, title, body, page, created_at, read_at FROM notifications ORDER BY created_at DESC LIMIT 50")?;
    let items = statement
        .query_map([], |row| {
            Ok(NotificationItem {
                id: row.get(0)?,
                kind: row.get(1)?,
                title: row.get(2)?,
                body: row.get(3)?,
                page: row.get(4)?,
                created_at: row.get(5)?,
                read_at: row.get(6)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    Ok(NotificationOverview {
        items,
        unread_count: unread_count as u64,
        suppressed: quiet(
            &preferences,
            OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc()),
        ),
    })
}

pub fn get_notifications(state: &EngagementState) -> Result<NotificationOverview> {
    notification_overview(state)
}

pub fn mark_all_read<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
) -> Result<NotificationOverview> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE notifications SET read_at = ?1 WHERE read_at IS NULL",
        params![timestamp(now()?)?],
    )?;
    let event = Store::event(&tx, "notifications.read_all", json!({}))?;
    tx.commit()?;
    emit(app, &event);
    notification_overview(state)
}

pub fn mark_read<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
    id: String,
) -> Result<NotificationOverview> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE notifications SET read_at = COALESCE(read_at, ?1) WHERE id = ?2",
        params![timestamp(now()?)?, id],
    )?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "通知不存在"));
    }
    let event = Store::event(&tx, "notifications.read", json!({"id":id}))?;
    tx.commit()?;
    emit(app, &event);
    notification_overview(state)
}

/// 首页/报告的目标方案口径（13#6）：最近采用的方案优先，无采用记录回退最新。
fn adopted_plan_id(conn: &rusqlite::Connection) -> Option<i64> {
    conn.query_row(
        "SELECT plan_id FROM learning_plan_adoptions ORDER BY id DESC LIMIT 1",
        [],
        |row| row.get::<_, i64>(0),
    )
    .ok()
}

pub fn dashboard<R: Runtime>(
    app: &AppHandle<R>,
    state: &EngagementState,
) -> Result<DashboardOverview> {
    let profile = get_profile(app, state)?;
    let store = state.store()?;
    // 13#6：原恒取最新方案（list().next()），未采用的新方案会悄悄切换首页
    // 目标；改为优先"最近采用的方案"（与学习空间口径一致），无采用记录
    // 时才回退最新方案
    let all_plans = super::learning::list(&state.learning)?;
    let plan = match adopted_plan_id(&store.conn) {
        Some(adopted) => all_plans
            .iter()
            .find(|plan| super::learning::plan_id(&plan.id).ok() == Some(adopted))
            .cloned()
            .or_else(|| all_plans.first().cloned()),
        None => all_plans.first().cloned(),
    };
    let next_task = plan.as_ref().and_then(|plan| {
        let plan_id = super::learning::plan_id(&plan.id).ok()?;
        plan.result.as_ref().and_then(|result| {
            result.stages.iter().find(|stage| {
                let completed: bool = store.conn.query_row("SELECT COALESCE((SELECT completed FROM learning_reading_progress WHERE plan_id = ?1 AND stage_id = ?2), 0)", params![plan_id, stage.id], |row| row.get(0)).unwrap_or(false);
                !completed
            }).map(|stage| DashboardTask { title: stage.title.clone(), detail: format!("{} · 当前方案阶段", plan.goal), minutes: stage.minutes as u64, kind: "学习".into() })
        })
    }).or_else(|| {
        store.collection("tasks").ok()?.value.as_array()?.iter().find(|task| !task.get("done").and_then(Value::as_bool).unwrap_or(false)).map(|task| DashboardTask { title: text(task, "title", "学习任务").into(), detail: format!("{} · {}", text(task, "subject", "未分类"), text(task, "kind", "学习")), minutes: task.get("duration").and_then(Value::as_u64).unwrap_or(0), kind: text(task, "kind", "学习").into() })
    });
    let progress = plan.as_ref().map(|plan| plan.progress).unwrap_or(0);
    let deadline = plan.as_ref().and_then(|plan| {
        plan.result
            .as_ref()
            .map(|result| result.schedule.end_date.clone())
    });
    let review = super::review::stats(&super::review::ReviewState::new(
        state.learning.database_path(),
        &state.learning,
    ))?;
    let calendar =
        super::reports::get_calendar(&super::reports::ReportState::new(&state.learning))?;
    let report = super::reports::get_report(
        &super::reports::ReportState::new(&state.learning),
        Some("4w"),
    )?;
    let week = calendar
        .days
        .iter()
        .rev()
        .take(7)
        .rev()
        .map(|day| super::reports::TimeSlice {
            kind: day.date.clone(),
            seconds: day.minutes * 60,
        })
        .collect::<Vec<_>>();
    let goal = if profile.goal.is_empty() {
        plan.as_ref()
            .map(|plan| plan.goal.clone())
            .unwrap_or_else(|| "尚未设置学习目标".into())
    } else {
        profile.goal
    };
    let mut upcoming = Vec::new();
    let mut statement = store.conn.prepare("SELECT plan_id, total_questions, exam_deadline, pass_score_percent FROM practice_sets WHERE mode = 'exam' AND status = 'ready' AND exam_deadline IS NOT NULL AND exam_deadline > ?1 ORDER BY exam_deadline LIMIT 3")?;
    let exams = statement
        .query_map(params![timestamp(now()?)?], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (plan_id, count, date, pass) in exams {
        upcoming.push(DashboardItem {
            kind: "exam".into(),
            title: format!("模拟考试 · 方案 {plan_id}"),
            detail: format!("{count} 题 · 及格 {pass}%"),
            date,
            page: "practice".into(),
        });
    }
    let mut project_items = Vec::new();
    for project in store
        .collection("projects")?
        .value
        .as_array()
        .into_iter()
        .flatten()
    {
        let remaining = project
            .get("milestones")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter(|milestone| {
                !milestone
                    .get("done")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
            })
            .count();
        // 13#5：里程碑全部完成的项目不再列为待办（原样混入会显示
        // “0 个里程碑待完成”并挤占考试等真实待办）
        if remaining == 0 {
            continue;
        }
        project_items.push(DashboardItem {
            kind: "project".into(),
            title: text(project, "title", "项目").into(),
            detail: format!("{remaining} 个里程碑待完成"),
            date: text(project, "due", "未设置日期").into(),
            page: "projects".into(),
        });
    }
    // 13#5：项目待办按截止日升序，最多 3 条（与考试口径一致）
    project_items.sort_by_key(|item| item.date.clone());
    project_items.truncate(3);
    upcoming.extend(project_items);
    let recommendation = report.recommendations.into_iter().next();
    Ok(DashboardOverview {
        name: profile.name,
        goal,
        next_task,
        progress,
        deadline,
        review_due: review.due_today,
        review_minutes: review.estimated_minutes,
        streak: calendar.streak,
        week,
        focus_seconds: report.metrics.focus_seconds,
        practice_count: report.metrics.practice_count,
        retention_percent: report.metrics.retention_percent,
        upcoming,
        recommendation,
    })
}
