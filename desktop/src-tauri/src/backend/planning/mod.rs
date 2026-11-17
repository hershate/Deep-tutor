pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{LearningState, Plan};
use super::store::Store;
use rusqlite::{params, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};
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
    /// P-002：方案的排期基数是强度记录引入前的历史值——库内没有任何字段
    /// 记录旧版 apply_intensity 当时用的缩放因子，真实基数不可反推。口径
    /// 声明为「库内现有分钟即基数」，本标记供界面明示该口径（界面显示的
    /// 强度与库内分钟数的对应关系以此为限）
    pub intensity_legacy: bool,
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
        .map_err(|_| BackendError::new("invalid_schedule_date", "排期日期格式无效"))
}

fn monday(value: time::Date) -> Result<time::Date> {
    Ok(value - Duration::days(i64::from(value.weekday().number_from_monday() - 1)))
}

/// 排期周窗口：周一起点 + 7 天。114#5 边界日期类型化报错（77#8/47#3 同型）
/// ——末周日期（如 9999-12-31 归一到周一后 +7 天）越界时 time 的 Add 实现
/// 直接 panic，而 planning 命令全部为同步命令属主线程级故障。
/// 单独成函数供写入前预检复用（P-133：先落库后报错会让用户看到失败提示
/// 却发现排期其实已保存）
fn week_bounds(start: time::Date) -> Result<(time::Date, time::Date)> {
    let week_start = monday(start)?;
    let week_end = week_start
        .checked_add(Duration::days(7))
        .ok_or_else(|| BackendError::new("invalid_schedule_date", "排期周已超出可表示日期范围"))?;
    Ok((week_start, week_end))
}

fn latest_plan(state: &PlanningState, id: Option<&str>) -> Result<Option<Plan>> {
    match id {
        Some(id) => Ok(Some(super::learning::get(&state.learning, id)?)),
        None => Ok(super::learning::list(&state.learning)?.into_iter().next()),
    }
}

/// 目标方案口径（25#5）：最近采用的方案优先，无采用记录回退最新——
/// 与首页/报告/学习空间一致，强度切换不得改写未采用的更新草稿。
fn adopted_first_plan(state: &PlanningState) -> Result<Option<Plan>> {
    let store = state.store()?;
    let all_plans = super::learning::list(&state.learning)?;
    Ok(match super::engagement::adopted_plan_id(&store.conn) {
        Some(adopted) => all_plans
            .iter()
            .find(|plan| super::learning::plan_id(&plan.id).ok() == Some(adopted))
            .cloned()
            .or_else(|| all_plans.first().cloned()),
        None => all_plans.first().cloned(),
    })
}

/// 目标方案口径（P-138）：显式给出 plan_id 时取该方案，否则回退
/// adopted-first（25#5）——报告页点开某个方案后，周历、强度与排期写操作
/// 必须落在同一个方案上，否则界面显示方案 B 而写入落到方案 A。
fn target_plan(state: &PlanningState, plan_id: Option<&str>) -> Result<Option<Plan>> {
    match plan_id {
        Some(id) => Ok(Some(super::learning::get(&state.learning, id)?)),
        None => adopted_first_plan(state),
    }
}

fn overview(state: &PlanningState, start: time::Date, plan: Option<Plan>) -> Result<PlanOverview> {
    let mut store = state.store()?;
    // 114#5：边界日期类型化报错（77#8/47#3 同型）——week_end 一经验证，
    // 下方 offset<7 的逐日加法必不越界
    let (week_start, week_end) = week_bounds(start)?;
    let start_text = week_start.to_string();
    let end_text = week_end.to_string();
    let mut events_by_day: BTreeMap<String, Vec<ScheduleEvent>> = BTreeMap::new();
    let plan_id = plan.as_ref().map(|plan| plan.id.clone());
    if let Some(id) = &plan_id {
        let numeric = super::learning::plan_id(id)?;
        // 10#17：kv 任务删除后，排期表没有按任务删除路径；周视图计算时
        // 惰性清理本方案的孤儿行，避免已删任务继续计入分钟统计。
        // 53#4：任务集合重读与两条 DELETE 收进同一 IMMEDIATE 事务——原先
        // 快照读在 autocommit、DELETE 各自独立提交，毫秒窗内并发的
        // adopt/replace_collection 新插入的排期行会被旧快照判为孤儿误删。
        // 126#8：读命令不再无条件拿写锁——Plan 页对每个 plan.* 事件全量
        // refresh，孤儿常态为零却每次开 IMMEDIATE 与真实写路径竞争；
        // 先只读探测，无孤儿直接跳过清理（并发新插入的孤儿留给下一轮
        // 惰性清理，删除路径仍走 53#4 的同事务快照）
        let tasks = {
            let snapshot = Store::collection_value(&store.conn, "tasks", &store.account_path)?;
            let live_ids: Vec<i64> = snapshot
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|task| task.get("id").and_then(Value::as_i64))
                .collect();
            let live_json = serde_json::to_string(&live_ids)?;
            let orphans: i64 = store.conn.query_row(
                "SELECT (SELECT COUNT(*) FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2))) + (SELECT COUNT(*) FROM learning_node_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2)))",
                params![numeric, live_json],
                |row| row.get(0),
            )?;
            if orphans == 0 {
                snapshot
            } else {
                let tx = store
                    .conn
                    .transaction_with_behavior(TransactionBehavior::Immediate)?;
                let tasks = Store::collection_value(&tx, "tasks", &store.account_path)?;
                let live_ids: Vec<i64> = tasks
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|task| task.get("id").and_then(Value::as_i64))
                    .collect();
                tx.execute(
                    "DELETE FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2))",
                    params![numeric, serde_json::to_string(&live_ids)?],
                )?;
                tx.execute(
                    "DELETE FROM learning_node_tasks WHERE plan_id = ?1 AND task_id NOT IN (SELECT value FROM json_each(?2))",
                    params![numeric, serde_json::to_string(&live_ids)?],
                )?;
                tx.commit()?;
                tasks
            }
        };
        let mut statement = store.conn.prepare("SELECT task_id, date, stage_id, minutes FROM learning_plan_schedule_tasks WHERE plan_id = ?1 AND date >= ?2 AND date < ?3 ORDER BY date, task_id")?;
        let rows = statement.query_map(params![numeric, start_text, end_text], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
            ))
        })?;
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
            // 114#5：week_end 已 checked 验证，offset<7 必不越界
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
    let (intensity, intensity_legacy) = match plan.as_ref() {
        // P-002：强度条显示方案自身的强度——全局偏好只是遗留值，
        // 新建方案的排期从未乘过它，按它显示等于宣告一个不存在的基数
        Some(plan) => plan_intensity_record(&store, plan)?,
        None => (
            preferences
                .get("planIntensity")
                .and_then(Value::as_str)
                .unwrap_or("标准")
                .to_string(),
            false,
        ),
    };
    let load_percent = ((weekly_minutes * 100) / (u64::from(weekly_hours) * 60).max(1)) as u32;
    // 80#3：待排期任务上限 500——任务配额 10000 × 每条 3 DOM 曾无界渲染；
    // 截断按数组序（新任务在前），前端对超限另行提示
    let unscheduled_cap = 500usize;
    let unscheduled = if let Some(id) = &plan_id {
        let numeric = super::learning::plan_id(id)?;
        let tasks = store.collection("tasks")?.value;
        // 126#7：已排期集合单查询——原先逐任务 EXISTS 点查（配额 1 万任务
        // 即万次点查，且 take(500) 在 filter 之后无法减少查询）
        let scheduled: HashSet<i64> = {
            let mut statement = store.conn.prepare(
                "SELECT DISTINCT task_id FROM learning_plan_schedule_tasks WHERE plan_id = ?1",
            )?;
            let ids = statement
                .query_map(params![numeric], |row| row.get::<_, i64>(0))?
                .collect::<std::result::Result<HashSet<_>, _>>()?;
            ids
        };
        tasks
            .as_array()
            .into_iter()
            .flatten()
            .filter(|task| {
                let Some(id) = task.get("id").and_then(Value::as_i64) else {
                    return false;
                };
                !scheduled.contains(&id)
            })
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
            .take(unscheduled_cap)
            .collect::<Vec<_>>()
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
            .take(unscheduled_cap)
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
        intensity_legacy,
        unscheduled,
    })
}

fn events_by_day_values(days: &[ScheduleDay]) -> u64 {
    days.iter()
        .flat_map(|day| day.events.iter())
        .map(|event| u64::from(event.minutes))
        .sum()
}

pub fn get_overview(
    state: &PlanningState,
    week_start: Option<String>,
    plan_id: Option<&str>,
) -> Result<PlanOverview> {
    let start = match week_start {
        Some(value) => date(&value)?,
        None => super::store::now_local().date(),
    };
    // 31#18：周视图与强度切换/报告同口径（adopted-first，25#5 修复补全）——
    // 原先取最新方案，已采用 plan-1 + 未采用 plan-2 时周历恒空而强度改 plan-1
    // P-138：给出 plan_id 时取该方案（报告页按方案查看），缺省仍是 adopted-first
    let plan = target_plan(state, plan_id)?;
    overview(state, start, plan)
}

fn factor(intensity: &str) -> Result<f64> {
    match intensity {
        "稳妥" => Ok(0.8),
        "标准" => Ok(1.0),
        "冲刺" => Ok(1.2),
        _ => Err(BackendError::new(
            "invalid_intensity",
            "学习强度须为稳妥、标准或冲刺",
        )),
    }
}

/// 方案自身已生效的强度与其基数来源——P-002：
///
/// - 强度记录写在 `learning_plans.input_json.intensity`：新建方案为 null
///   （排期从未乘过强度因子），强度切换后写入本次生效的强度。
/// - 全局偏好 `planIntensity` 只是最后一次强度切换留下的遗留值，与具体
///   方案无关；拿它当缩放基数会按遗留强度错缩（±17%~25%）并全链落盘。
/// - `legacy = true` 表示 input_json 里根本没有 intensity 键：方案创建于
///   强度记录引入之前，旧版 apply_intensity 可能已按全局遗留强度缩放过
///   它的排期，而库内没有任何字段记录当时的缩放因子——真实基数不可反推。
///   口径显式声明为「库内现有分钟即基数」：后续切换以库内现有分钟为基准，
///   不在未知因子上继续累积，并把该口径回传界面明示。此处不做一次性迁移
///   ——迁移同样无法反推历史因子，写死一个强度值等于用假数据覆盖历史。
///   首次切换把生效强度写回方案自身后记录即存在，遗留标记随之消失：此时
///   界面显示的强度与库内分钟数在「库内现有分钟即基数」口径下相互对应。
fn plan_intensity_record(store: &Store, plan: &Plan) -> Result<(String, bool)> {
    let numeric = super::learning::plan_id(&plan.id)?;
    let raw: String = store.conn.query_row(
        "SELECT input_json FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| row.get(0),
    )?;
    // 键缺失（强度记录引入前的历史方案）与键存在但为 null（新建方案，
    // 排期从未缩放过）必须区分：前者的排期分钟可能被旧版缩放过
    let legacy = serde_json::from_str::<Value>(&raw)
        .ok()
        .and_then(|value| value.get("intensity").cloned())
        .is_none();
    let intensity = serde_json::from_str::<super::learning::PlanInput>(&raw)
        .ok()
        .and_then(|input| input.intensity)
        .filter(|value| matches!(value.as_str(), "稳妥" | "标准" | "冲刺"))
        .unwrap_or_else(|| "标准".to_string());
    Ok((intensity, legacy))
}

/// 强度缩放：条目/任务/阶段分钟按比例取整，不套 10..=600 钳制——
/// 排期装填合法产出 1–9 分钟尾条目，旧实现对全部量 clamp(10,600) 使
/// 2 分钟尾条目切「冲刺」被抬到 10 分钟、再切回时单调卡死在 10（52#1）。
fn scaled(minutes: u32, current: &str, target: &str) -> Result<u32> {
    let ratio = factor(target)? / factor(current)?;
    // 77#12：已知取舍——0.8/1.2 往返经两次独立取整可漂移 -1 分钟/条目
    //（7→8→6）；比例因子从当前值链式计算，保留「无钳制卡死」（52#1）与
    // 简洁性，±1 分钟漂移不修正
    Ok(((minutes as f64) * ratio).round() as u32)
}

/// 每日分钟是排期步硬校验 10..=600 的唯一对象（learning validate 拒收
/// 界外值使重生成永久必败），缩放后仍钳回界内（07#7/31#20/52#1）。
fn scaled_daily(minutes: u32, current: &str, target: &str) -> Result<u32> {
    Ok(scaled(minutes, current, target)?.clamp(10, 600))
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
    plan_id: Option<&str>,
) -> Result<Vec<PlanAlternative>> {
    let overview = get_overview(state, week_start, plan_id)?;
    alternatives_for(&overview)
}

pub fn apply_intensity<R: Runtime>(
    app: &AppHandle<R>,
    state: &PlanningState,
    intensity: String,
    week_start: Option<String>,
    plan_id: Option<&str>,
) -> Result<PlanOverview> {
    factor(&intensity)?;
    // 25#5：目标方案口径与首页/报告一致（最近采用优先），否则会改写未采用的
    // 更新草稿，而界面仍展示已采用方案
    // P-138：给出 plan_id 时按该方案计算与改写，缺省仍是 adopted-first
    let start = match week_start.clone() {
        Some(value) => date(&value)?,
        None => super::store::now_local().date(),
    };
    let plan = target_plan(state, plan_id)?;
    let current = overview(state, start, plan)?;
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
        return overview(state, start, None);
    };
    // 31#17：存在「未完成方案」（result 未生成）时与无方案分支同口径——
    // 先落偏好再返回未缩放总览；原先事务内 plan_not_ready 整单回滚，
    // 连偏好都无法保存
    if plan.result.is_none() {
        let event = Store::event(
            &tx,
            "plan.intensity_updated",
            json!({"planId":plan.id,"intensity":intensity}),
        )?;
        tx.commit()?;
        emit(app, &event);
        return overview(state, start, Some(plan));
    }
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
        .clone()
        .ok_or_else(|| BackendError::new("plan_not_ready", "当前方案没有可调整的排期"))?;
    // 34#21：事务内复查方案状态——读取 result 与开事务之间可能被并发
    // regenerate 穿插（result 置 NULL、步骤重置），无守卫会复活旧结果并向
    // 已移除的 artifacts $.step5 写旧值；对照 regenerate 自身的三重前置校验
    let status: String = tx.query_row(
        "SELECT status FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| row.get(0),
    )?;
    if !["done", "degraded"].contains(&status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "方案正在生成或重生成，暂不能调整强度",
        ));
    }
    // 31#16：覆盖前记 versions（与 regenerate 同一「覆盖前保留历史」不变量）。
    // 同版本多次切强度以最近一次覆盖前快照为准（OR REPLACE 避免主键冲突）
    tx.execute(
        "INSERT OR REPLACE INTO learning_plan_versions (plan_id, version, result_json, created_at) VALUES (?1, ?2, ?3, ?4)",
        params![numeric, plan.version, serde_json::to_string(&result)?, timestamp(OffsetDateTime::now_utc())?],
    )?;
    for entry in &mut result.schedule.entries {
        entry.minutes = scaled(entry.minutes, &current.intensity, &intensity)?;
    }
    // 31#15：阶段视图分钟与排期同步缩放——PlanOverview.stages 读
    // result.stages[].minutes，原先不缩放导致强度切换后阶段卡停留旧值
    for stage in &mut result.stages {
        stage.minutes = scaled(stage.minutes, &current.intensity, &intensity)?;
    }
    result.schedule.total_minutes = result
        .schedule
        .entries
        .iter()
        .map(|entry| entry.minutes)
        .sum();
    // 31#16：同步 artifacts $.step5——finish_result 以旧 step5 重建会让
    // scope=6 重生成静默回退缩放前强度，且再切强度在错误基数上继续缩放。
    // 34#4：complete_step 是双写（step 表 output_json + artifacts_json），而
    // run() 重建读的是 step 表——两处必须同步更新，否则重建仍回退旧排期
    let step5_json = serde_json::to_string(&serde_json::json!({
        "schedule": result.schedule,
        "stages": result.stages,
    }))?;
    tx.execute(
        "UPDATE learning_plans SET artifacts_json = json_set(COALESCE(artifacts_json, '{}'), '$.step5', json(?1)) WHERE id = ?2",
        params![step5_json, numeric],
    )?;
    tx.execute(
        "UPDATE learning_plan_steps SET output_json = ?1, updated_at = ?2 WHERE plan_id = ?3 AND step_index = 5",
        params![step5_json, timestamp(OffsetDateTime::now_utc())?, numeric],
    )?;
    let mut input: super::learning::PlanInput = serde_json::from_str(&store.conn.query_row(
        "SELECT input_json FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| row.get::<_, String>(0),
    )?)?;
    input.daily_minutes = scaled_daily(input.daily_minutes, &current.intensity, &intensity)?;
    // P-002：把本次生效的强度记到方案自身——后续切换按它换算，
    // 不再拿全局偏好（遗留值）当基数
    input.intensity = Some(intensity.clone());
    // 23#1：within_deadline/within_milestone 是"结束日 <= 截止日"的日期语义
    // （learning/validate.rs），强度切换不改变日期——不得用容量不等式重算，
    // 否则逾期方案会被持久化翻成"在目标日内"；保持生成时的标志不动
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
    overview(state, start, adopted_first_plan(state)?)
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
    // 25#4：先做与 regenerate 相同的状态/次数校验再落库——原实现先提交
    // goal/deadline/偏好再由 regenerate 拒绝，留下"目标已改写但未重生成"的
    // 部分提交状态
    if !["done", "degraded"].contains(&plan.status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "只有完成或降级的方案可以调整目标",
        ));
    }
    let regeneration_limit =
        super::providers::usage::settings(state.learning.providers())?.regeneration_limit;
    if plan.version > u32::from(regeneration_limit) {
        return Err(BackendError::new(
            "regeneration_limit",
            &format!("每个方案最多局部重生成 {regeneration_limit} 次"),
        ));
    }
    let goal = input.goal.trim().to_string();
    // P-096：目标上限与创建入口（learning::validate_input 的 1000 字节）
    // 对齐——原先此处只许 200 字节，弹窗预填创建时合法的长目标后保存必被
    // 拒（invalid_goal），用户必须手工缩短当初合法输入的目标才能改任何
    // 目标参数。同入口的「当前掌握情况」本就是 1000 字节
    if goal.is_empty() || goal.len() > 1000 || !(1..=80).contains(&input.weekly_hours) {
        return Err(BackendError::new(
            "invalid_goal",
            "目标须为 1 到 1000 字节，每周投入须为 1 到 80 小时",
        ));
    }
    // 126#9：截止日不早于今天——原先仅验格式，过去日期静默落库，到
    // regenerate 才经 within_deadline=false 间接暴露（多一轮生成）
    if date(&input.deadline)? < super::store::now_local().date() {
        return Err(BackendError::new(
            "invalid_deadline",
            "目标截止日期不能早于今天",
        ));
    }
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
    // P-002：强度取方案自身已生效的强度，不用全局偏好——后者只是遗留值，
    // 别的方案切过强度后套到本方案会让重新规划按错基数排期
    let intensity = plan_input
        .intensity
        .clone()
        .unwrap_or_else(|| "标准".to_string());
    // 52#2：先乘后除全程浮点（整数除法 60/7=8 向零截断，余数档每周静默
    // 丢 1–4 分钟），钳制只在最终值做一次——基数预钳再乘强度会叠加抬升；
    // 10..=600 为排期步硬校验界，最终值必须落回界内
    plan_input.daily_minutes = ((f64::from(input.weekly_hours) * 60.0 / 7.0 * factor(&intensity)?)
        .round() as u32)
        .clamp(10, 600);
    if let Some(level) = input.level {
        // A-2：level 直接落 input_json 并随 regenerate 进入模型提示词——
        // generate 入口的 1000B 校验（learning）在本路径不重跑，超长基础
        // 描述会静默污染提示词，这里同界校验
        // 31#22：与 start 入口（learning::validate_input）对齐——空/全空白
        // level 同样拒绝，原先只查长度，空白可落 input_json 并进入提示词
        if level.trim().is_empty() || level.len() > 1000 {
            return Err(BackendError::new(
                "invalid_level",
                "当前掌握情况不能为空且不能超过 1000 字节",
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
    // P-074：事务内复查方案状态——事务外的状态/次数校验与开事务之间可能
    // 被并发 regenerate 穿插（status 置 running、result 置 NULL、version
    // 自增），无守卫会提交目标改写却不重生成，留下 25#4 要避免的部分提交
    // 状态（用户只看到「只有完成或降级的方案可以调整目标」，目标其实已改）。
    // 对照 apply_intensity 的 34#21 同型守卫与 regenerate 自身的三重前置校验
    let (status, version): (String, i64) = tx.query_row(
        "SELECT status, version FROM learning_plans WHERE id = ?1",
        params![numeric],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if !["done", "degraded"].contains(&status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "方案正在生成或重生成，暂不能调整目标",
        ));
    }
    if version > i64::from(regeneration_limit) {
        return Err(BackendError::new(
            "regeneration_limit",
            &format!("每个方案最多局部重生成 {regeneration_limit} 次"),
        ));
    }
    let adopted: Option<i64> = tx
        .query_row(
            "SELECT project_id FROM learning_plan_adoptions WHERE plan_id = ?1",
            params![numeric],
            |row| row.get(0),
        )
        .optional()?;
    if adopted.is_some() {
        return Err(BackendError::new(
            "plan_adopted",
            "已采用的方案不能直接重生成，请保留历史并创建新方案",
        ));
    }
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
    plan_id: Option<&str>,
) -> Result<PlanOverview> {
    // 31#18：任务排期与周视图同口径 adopted-first——原先挂到最新草稿，
    // 已采用方案的任务日期会写进另一个方案
    // P-138：给出 plan_id 时排期写入该方案（界面上显示的方案必须与写入的
    // 方案一致），缺省仍是 adopted-first
    let plan = target_plan(state, plan_id)?
        .ok_or_else(|| BackendError::new("no_plan", "请先创建学习方案，再为任务排期"))?;
    let numeric_plan_id = super::learning::plan_id(&plan.id)?;
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
    // P-133：先校验排期周可表示，再落库——原先写入提交后才计算周视图，
    // 边界日期（如 9999-12-31）越界时报错，用户看到「排期失败」却发现
    // 任务其实已保存到该日期，且按同日期重试永远得到同样错误
    week_bounds(day)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO learning_plan_schedule_tasks (plan_id, task_id, date, stage_id, node_ids_json, minutes) VALUES (?1, ?2, ?3, 'custom', '[]', ?4) ON CONFLICT(plan_id, task_id) DO UPDATE SET date = excluded.date, stage_id = excluded.stage_id, node_ids_json = excluded.node_ids_json, minutes = excluded.minutes", params![numeric_plan_id, task_id, day.to_string(), minutes])?;
    let event = Store::event(
        &tx,
        "plan.task_scheduled",
        json!({"planId":plan.id,"taskId":task_id,"date":date_value}),
    )?;
    tx.commit()?;
    emit(app, &event);
    overview(state, day, Some(plan))
}
