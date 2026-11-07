pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{LearningState, Plan};
use super::store::Store;
use rusqlite::params;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime};

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ScheduleEvent {
    pub task_id: Option<i64>,
    pub title: String,
    pub kind: String,
    pub minutes: u32,
    pub done: bool,
    pub date: Option<String>,
    pub stage_id: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScheduleDay {
    pub date: String,
    pub events: Vec<ScheduleEvent>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StageStatus {
    pub id: String,
    pub title: String,
    pub minutes: u32,
    pub difficulty: u8,
    pub completed: bool,
    pub node_ids: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanOverview {
    pub plan: Option<Plan>,
    pub week_start: String,
    pub days: Vec<ScheduleDay>,
    pub stages: Vec<StageStatus>,
    pub weekly_minutes: u64,
    pub weekly_hours: u32,
    pub load_percent: u32,
    pub intensity: String,
    pub unscheduled: Vec<ScheduleEvent>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanAlternative {
    pub intensity: String,
    pub weekly_minutes: u64,
    pub load_percent: u32,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GoalInput {
    pub plan_id: Option<String>,
    pub goal: String,
    pub deadline: String,
    pub weekly_hours: u32,
    pub level: Option<String>,
}

pub struct PlanningState {
    learning: LearningState,
}

impl PlanningState {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            learning: learning.shared(),
        }
    }

    fn store(&self) -> Result<Store> {
        self.learning.store()
    }
}

fn timestamp(value: OffsetDateTime) -> Result<String> {
    Ok(value.format(&Rfc3339)?)
}

fn date(value: &str) -> Result<time::Date> {
    time::Date::parse(value, &time::format_description::well_known::Iso8601::DATE)
        .map_err(|_| BackendError::invalid("invalid_schedule_date", "排期日期格式无效"))
}

fn monday(value: time::Date) -> Result<time::Date> {
    Ok(value - Duration::days(i64::from(value.weekday().number_from_monday() - 1)))
}

fn latest_plan(state: &PlanningState, id: Option<&str>) -> Result<Option<Plan>> {
    match id {
        Some(id) => Ok(Some(super::learning::get(&state.learning, id)?)),
        None => Ok(super::learning::list(&state.learning)?.into_iter().next()),
    }
}

fn overview(state: &PlanningState, start: time::Date, plan: Option<Plan>) -> Result<PlanOverview> {
    let store = state.store()?;
    let week_start = monday(start)?;
    let week_end = week_start + Duration::days(7);
    let start_text = week_start.to_string();
    let end_text = week_end.to_string();
    let mut events_by_day: BTreeMap<String, Vec<ScheduleEvent>> = BTreeMap::new();
    let plan_id = plan.as_ref().map(|plan| plan.id.clone());
    if let Some(id) = &plan_id {
        let numeric = super::learning::plan_id(id)?;
        let mut statement = store.conn.prepare("SELECT task_id, date, stage_id, minutes FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND date >= ?2 AND date < ?3 ORDER BY date, task_id")?;
        let rows = statement.query_map(params![numeric, start_text, end_text], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
            ))
        })?;
        let tasks = store.collection("tasks")?.value;
        // 10#17：kv 任务删除后，排期表没有按任务删除路径；周视图计算时
        // 惰性清理本方案的孤儿行，避免已删任务继续计入分钟统计
        let live_ids: Vec<i64> = tasks
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|task| task.get("id").and_then(Value::as_i64))
            .collect();
        store.conn.execute(
            "DELETE FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2))",
            params![numeric, serde_json::to_string(&live_ids)?],
        )?;
        store.conn.execute(
            "DELETE FROM learning_node_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2))",
            params![numeric, serde_json::to_string(&live_ids)?],
        )?;
        for row in rows {
            let (task_id, date, stage_id, minutes) = row?;
            let task = tasks
                .as_array()
                .into_iter()
                .flatten()
                .find(|task| task.get("id").and_then(Value::as_i64) == Some(task_id));
            let title = task
                .and_then(|task| task.get("title").and_then(Value::as_str))
                .unwrap_or("已删除任务")
                .to_string();
            let kind = task
                .and_then(|task| task.get("kind").and_then(Value::as_str))
                .unwrap_or("学习")
                .to_string();
            let done = task
                .and_then(|task| task.get("done").and_then(Value::as_bool))
                .unwrap_or(false);
            events_by_day
                .entry(date.clone())
                .or_default()
                .push(ScheduleEvent {
                    task_id: Some(task_id),
                    title,
                    kind,
                    minutes: minutes.max(0) as u32,
                    done,
                    date: Some(date),
                    stage_id: Some(stage_id),
                });
        }
    }
    let days = (0..7)
        .map(|offset| {
            let date = (week_start + Duration::days(offset)).to_string();
            let events = events_by_day.remove(&date).unwrap_or_default();
            ScheduleDay { date, events }
        })
        .collect::<Vec<_>>();
    let weekly_minutes = events_by_day_values(&days);
    let stage_plan_id = plan
        .as_ref()
        .map(|plan| super::learning::plan_id(&plan.id))
        .transpose()?;
    let stages = plan.as_ref().and_then(|plan| plan.result.as_ref()).map(|result| {
        result.stages.iter().map(|stage| {
            let completed: bool = store.conn.query_row("SELECT COALESCE((SELECT completed FROM learning_reading_progress WHERE plan_id = ?1 AND stage_id = ?2), 0)", params![stage_plan_id, stage.id], |row| row.get(0)).unwrap_or(false);
            StageStatus { id: stage.id.clone(), title: stage.title.clone(), minutes: stage.minutes, difficulty: stage.difficulty, completed, node_ids: stage.node_ids.clone() }
        }).collect::<Vec<_>>()
    }).unwrap_or_default();
    let preferences = store.collection("preferences")?.value;
    let weekly_hours = preferences
        .get("weeklyHours")
        .and_then(Value::as_u64)
        .unwrap_or(15) as u32;
    let intensity = preferences
        .get("planIntensity")
        .and_then(Value::as_str)
        .unwrap_or("标准")
        .to_string();
    let load_percent = ((weekly_minutes * 100) / (u64::from(weekly_hours) * 60).max(1)) as u32;
    let unscheduled = if let Some(id) = &plan_id {
        let numeric = super::learning::plan_id(id)?;
        let tasks = store.collection("tasks")?.value;
        tasks.as_array().into_iter().flatten().filter(|task| {
            let Some(id) = task.get("id").and_then(Value::as_i64) else { return false };
            !store.conn.query_row("SELECT EXISTS(SELECT 1 FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND task_id = ?2)", params![numeric, id], |row| row.get::<_, bool>(0)).unwrap_or(false)
        }).map(|task| ScheduleEvent { task_id: task.get("id").and_then(Value::as_i64), title: task.get("title").and_then(Value::as_str).unwrap_or("任务").into(), kind: task.get("kind").and_then(Value::as_str).unwrap_or("学习").into(), minutes: task.get("duration").and_then(Value::as_u64).unwrap_or(0) as u32, done: task.get("done").and_then(Value::as_bool).unwrap_or(false), date: None, stage_id: None }).collect::<Vec<_>>()
    } else {
        store
            .collection("tasks")?
            .value
            .as_array()
            .into_iter()
            .flatten()
            .map(|task| ScheduleEvent {
                task_id: task.get("id").and_then(Value::as_i64),
                title: task
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("任务")
                    .into(),
                kind: task
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("学习")
                    .into(),
                minutes: task.get("duration").and_then(Value::as_u64).unwrap_or(0) as u32,
                done: task.get("done").and_then(Value::as_bool).unwrap_or(false),
                date: None,
                stage_id: None,
            })
            .collect()
    };
    Ok(PlanOverview {
        plan,
        week_start: start_text,
        days,
        stages,
        weekly_minutes,
        weekly_hours,
        load_percent,
        intensity,
        unscheduled,
    })
}

fn events_by_day_values(days: &[ScheduleDay]) -> u64 {
    days.iter()
        .flat_map(|day| day.events.iter())
        .map(|event| u64::from(event.minutes))
        .sum()
}

pub fn get_overview(state: &PlanningState, week_start: Option<String>) -> Result<PlanOverview> {
    let start = match week_start {
        Some(value) => date(&value)?,
        None => OffsetDateTime::now_local()
            .unwrap_or_else(|_| OffsetDateTime::now_utc())
            .date(),
    };
    let plan = latest_plan(state, None)?;
    overview(state, start, plan)
}

fn factor(intensity: &str) -> Result<f64> {
    match intensity {
        "稳妥" => Ok(0.8),
        "标准" => Ok(1.0),
        "冲刺" => Ok(1.2),
        _ => Err(BackendError::invalid(
            "invalid_intensity",
            "学习强度须为稳妥、标准或冲刺",
        )),
    }
}

fn scaled(minutes: u32, current: &str, target: &str) -> Result<u32> {
    let ratio = factor(target)? / factor(current)?;
    // 排期步硬校验每日 10..=600 分钟：强度缩放后封顶，避免方案局部重生成永久必败
    Ok(((minutes as f64 * ratio).round() as u32).clamp(15, 600))
}

fn alternatives_for(overview: &PlanOverview) -> Result<Vec<PlanAlternative>> {
    ["稳妥", "标准", "冲刺"]
        .into_iter()
        .map(|intensity| {
            let weekly_minutes = overview
                .days
                .iter()
                .flat_map(|day| day.events.iter())
                .map(|event| -> Result<u64> {
                    Ok(u64::from(scaled(
                        event.minutes,
                        &overview.intensity,
                        intensity,
                    )?))
                })
                .collect::<Result<Vec<_>>>()?
                .into_iter()
                .sum::<u64>();
            Ok(PlanAlternative {
                intensity: intensity.into(),
                weekly_minutes,
                load_percent: ((weekly_minutes * 100)
                    / (u64::from(overview.weekly_hours) * 60).max(1))
                    as u32,
            })
        })
        .collect()
}

pub fn alternatives(
    state: &PlanningState,
    week_start: Option<String>,
) -> Result<Vec<PlanAlternative>> {
    let overview = get_overview(state, week_start)?;
    alternatives_for(&overview)
}

pub fn apply_intensity<R: Runtime>(
    app: &AppHandle<R>,
    state: &PlanningState,
    intensity: String,
    week_start: Option<String>,
) -> Result<PlanOverview> {
    factor(&intensity)?;
    let current = get_overview(state, week_start.clone())?;
    let store = state.store()?;
    let preferences_record = store.collection("preferences")?;
    let tasks_record = store.collection("tasks")?;
    let mut preferences = preferences_record.value;
    preferences["planIntensity"] = Value::String(intensity.clone());
    super::store::validate_preferences(&preferences)?;
    let tx = store.conn.unchecked_transaction()?;
    let preferences_revision = Store::next_revision(&tx, "preferences")?;
    if preferences_revision - 1 != preferences_record.revision {
        return Err(BackendError::new(
            "revision_conflict",
            "偏好已更新，请刷新后重试",
        ));
    }
    if current.plan.is_some() {
        let tasks_revision = Store::next_revision(&tx, "tasks")?;
        if tasks_revision - 1 != tasks_record.revision {
            return Err(BackendError::new(
                "revision_conflict",
                "任务已更新，请刷新后重试",
            ));
        }
    }
    super::store::Store::insert_json(&tx, "preferences", "current", &preferences)?;
    let Some(plan) = current.plan else {
        let event = Store::event(
            &tx,
            "plan.intensity_updated",
            json!({"planId":null,"intensity":intensity}),
        )?;
        tx.commit()?;
        emit(app, &event);
        return get_overview(state, week_start);
    };
    let numeric = super::learning::plan_id(&plan.id)?;
    let mut statement =
        tx.prepare("SELECT task_id, minutes FROM learning_plan_schedule_tasks WHERE plan_id = ?1")?;
    let scheduled = statement
        .query_map(params![numeric], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut tasks = tasks_record.value;
    for (task_id, minutes) in scheduled {
        let minutes = u32::try_from(minutes)
            .map_err(|_| BackendError::new("invalid_record", "排期分钟数无效"))?;
        let next = scaled(minutes, &current.intensity, &intensity)?;
        tx.execute("UPDATE learning_plan_schedule_tasks SET minutes = ?1 WHERE plan_id = ?2 AND task_id = ?3", params![next, numeric, task_id])?;
        if let Some(task) = tasks
            .as_array_mut()
            .into_iter()
            .flatten()
            .find(|task| task.get("id").and_then(Value::as_i64) == Some(task_id))
        {
            task["duration"] = Value::from(next);
        }
    }
    let mut result = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "当前方案没有可调整的排期"))?;
    for entry in &mut result.schedule.entries {
        entry.minutes = scaled(entry.minutes, &current.intensity, &intensity)?;
    }
    result.schedule.total_minutes = result
        .schedule
        .entries
        .iter()
        .map(|entry| entry.minutes)
        .sum();
    let mut input: super::learning::PlanInput = serde_json::from_str(&store.conn.query_row(
        "SELECT input_json FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| row.get::<_, String>(0),
    )?)?;
    input.daily_minutes = scaled(input.daily_minutes, &current.intensity, &intensity)?;
    let days = i64::from(result.schedule.days.max(1));
    result.schedule.within_deadline = date(&result.schedule.end_date)?
        >= date(&result.schedule.start_date)?
        && result.schedule.total_minutes <= input.daily_minutes.saturating_mul(days as u32);
    if let Some(milestone) = &result.schedule.milestone_date {
        let days = (date(milestone)? - date(&result.schedule.start_date)?)
            .whole_days()
            .max(1) as u32;
        result.schedule.within_milestone =
            Some(result.schedule.total_minutes <= input.daily_minutes.saturating_mul(days));
    }
    tx.execute("UPDATE learning_plans SET input_json = ?1, result_json = ?2, updated_at = ?3 WHERE id = ?4", params![serde_json::to_string(&input)?, serde_json::to_string(&result)?, timestamp(OffsetDateTime::now_utc())?, numeric])?;
    for task in tasks.as_array().into_iter().flatten() {
        super::store::Store::insert_json(
            &tx,
            "tasks",
            &task
                .get("id")
                .and_then(Value::as_i64)
                .ok_or_else(|| BackendError::new("invalid_record", "任务标识无效"))?
                .to_string(),
            task,
        )?;
    }
    let event = Store::event(
        &tx,
        "plan.intensity_updated",
        json!({"planId":plan.id,"intensity":intensity}),
    )?;
    tx.commit()?;
    emit(app, &event);
    get_overview(state, week_start)
}

pub fn update_goal<R: Runtime>(
    app: &AppHandle<R>,
    state: &PlanningState,
    input: GoalInput,
) -> Result<Plan> {
    let plan = latest_plan(state, input.plan_id.as_deref())?
        .ok_or_else(|| BackendError::new("no_plan", "请先创建学习方案，再调整目标"))?;
    if plan.adopted_project_id.is_some() {
        return Err(BackendError::new(
            "plan_adopted",
            "已采用的方案不能直接重生成，请保留历史并创建新方案",
        ));
    }
    let goal = input.goal.trim().to_string();
    if goal.is_empty() || goal.len() > 200 || !(1..=80).contains(&input.weekly_hours) {
        return Err(BackendError::invalid(
            "invalid_goal",
            "目标须为 1 到 200 字节，每周投入须为 1 到 80 小时",
        ));
    }
    date(&input.deadline)?;
    let numeric = super::learning::plan_id(&plan.id)?;
    let store = state.store()?;
    let mut plan_input: super::learning::PlanInput = serde_json::from_str(&store.conn.query_row(
        "SELECT input_json FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| row.get::<_, String>(0),
    )?)?;
    plan_input.goal = goal;
    plan_input.deadline = input.deadline;
    let preferences_record = store.collection("preferences")?;
    let mut preferences = preferences_record.value;
    // 每日分钟 = 基数 × 当前强度因子：保持"排期分钟已含强度"的前提，
    // 否则 goal 更新+重生成后再切强度会按未缩放基数计算（07#7）；
    // 排期步硬校验 10..=600 分钟，派生值整体封顶保证重生成可成功
    let intensity = preferences
        .get("planIntensity")
        .and_then(Value::as_str)
        .unwrap_or("标准")
        .to_string();
    let base = (input.weekly_hours * 60 / 7).clamp(15, 600);
    plan_input.daily_minutes = ((base as f64 * factor(&intensity)?).round() as u32).clamp(15, 600);
    if let Some(level) = input.level {
        // A-2：level 直接落 input_json 并随 regenerate 进入模型提示词——
        // generate 入口的 1000B 校验（learning）在本路径不重跑，超长基础
        // 描述会静默污染提示词，这里同界校验
        if level.len() > 1000 {
            return Err(BackendError::invalid(
                "invalid_level",
                "当前掌握情况不能超过 1000 字节",
            ));
        }
        plan_input.level = level;
    }
    preferences["weeklyHours"] = Value::from(input.weekly_hours);
    super::store::validate_preferences(&preferences)?;
    let tx = store.conn.unchecked_transaction()?;
    let revision = Store::next_revision(&tx, "preferences")?;
    if revision - 1 != preferences_record.revision {
        return Err(BackendError::new(
            "revision_conflict",
            "偏好已更新，请刷新后重试",
        ));
    }
    Store::insert_json(&tx, "preferences", "current", &preferences)?;
    tx.execute(
        "UPDATE learning_plans SET input_json = ?1, updated_at = ?2 WHERE id = ?3",
        params![
            serde_json::to_string(&plan_input)?,
            timestamp(OffsetDateTime::now_utc())?,
            numeric
        ],
    )?;
    let event = Store::event(
        &tx,
        "plan.goal_updated",
        json!({"planId":plan.id,"goal":plan_input.goal,"deadline":plan_input.deadline,"dailyMinutes":plan_input.daily_minutes}),
    )?;
    tx.commit()?;
    emit(app, &event);
    super::learning::actions::regenerate(app, &state.learning, &plan.id, 2)
}

pub fn assign_task_date<R: Runtime>(
    app: &AppHandle<R>,
    state: &PlanningState,
    task_id: i64,
    date_value: String,
) -> Result<PlanOverview> {
    let plan = latest_plan(state, None)?
        .ok_or_else(|| BackendError::new("no_plan", "请先创建学习方案，再为任务排期"))?;
    let plan_id = super::learning::plan_id(&plan.id)?;
    let day = date(&date_value)?;
    let store = state.store()?;
    let task = store.collection("tasks")?.value;
    let task = task
        .as_array()
        .into_iter()
        .flatten()
        .find(|task| task.get("id").and_then(Value::as_i64) == Some(task_id))
        .ok_or_else(|| BackendError::new("not_found", "任务不存在"))?;
    let minutes = i64::try_from(task.get("duration").and_then(Value::as_u64).unwrap_or(0))
        .map_err(|_| BackendError::new("invalid_record", "任务分钟数无效"))?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO learning_plan_schedule_tasks (plan_id, task_id, date, stage_id, node_ids_json, minutes) VALUES (?1, ?2, ?3, 'custom', '[]', ?4) ON CONFLICT(plan_id, task_id) DO UPDATE SET date = excluded.date, stage_id = excluded.stage_id, node_ids_json = excluded.node_ids_json, minutes = excluded.minutes", params![plan_id, task_id, day.to_string(), minutes])?;
    let event = Store::event(
        &tx,
        "plan.task_scheduled",
        json!({"planId":plan.id,"taskId":task_id,"date":date_value}),
    )?;
    tx.commit()?;
    emit(app, &event);
    overview(state, day, Some(plan))
}
