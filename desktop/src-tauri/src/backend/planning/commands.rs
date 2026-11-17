use super::super::{error::BackendError, error::Result, BackendState};
use super::{GoalInput, PlanAlternative, PlanOverview, PlanningState};
use crate::backend::learning::Plan;
use tauri::{AppHandle, Runtime, State};

// 64#1：planning 域五个命令原先全部是同步命令，在主线程执行——overview 要
// 把 tasks 集合整包读出并反序列化两遍、逐行按 id 线性查找并对每个阶段单发
// 一次阅读状态点查，强度切换还要在一个事务里逐条重写全集合并整体序列化版本
// 快照；前端对每个 plan.*/state.changed 事件立即发起一次全量拉取，翻周与排期
// 期间窗口冻结随任务量线性增长。移入阻塞线程池，命令名与参数契约不变。

// 23#7：async 命令体运行在 tokio worker，guard 在 IPC 线程设置的
// thread-local principal 到不了那里——从会话派生当前用户再显式传播进阻塞
// 线程，否则会打开错误的工作区库。五个命令一律用 _existing（不自动签发
// 会话）：guard 已保证会话存在，_existing 派生出的用户与 guard 的
// current() 同源；写命令用 session_principal 会在无 guard 的测试环境里
// 触发自动管理员签发并把写入落到 users/1 工作区（与迁移前的同步语义不符）。
// 生产会话失效时 Store::new 以 store_unavailable 拒绝，不会写错工作区
#[tauri::command(async)]
pub async fn get_plan_overview<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    week_start: Option<String>,
    // P-138：显式指定方案（报告页按方案查看）；缺省仍是 adopted-first，
    // 命令契约向后兼容——旧调用方不传 planId 时行为不变
    plan_id: Option<String>,
) -> Result<PlanOverview> {
    let planning = PlanningState::new(&state.learning);
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::get_overview(&planning, week_start, plan_id.as_deref())
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "排期总览加载任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn get_plan_alternatives<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    week_start: Option<String>,
    plan_id: Option<String>,
) -> Result<Vec<PlanAlternative>> {
    let planning = PlanningState::new(&state.learning);
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::alternatives(&planning, week_start, plan_id.as_deref())
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "排期方案对比加载任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn apply_plan_intensity<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    intensity: String,
    week_start: Option<String>,
    plan_id: Option<String>,
) -> Result<PlanOverview> {
    let planning = PlanningState::new(&state.learning);
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::apply_intensity(&app, &planning, intensity, week_start, plan_id.as_deref())
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "排期强度切换任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn update_learning_goal<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: GoalInput,
) -> Result<Plan> {
    let planning = PlanningState::new(&state.learning);
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::update_goal(&app, &planning, input)
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "学习目标更新任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn assign_task_date<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    task_id: i64,
    date_value: String,
    // P-138：排期写入的方案必须与界面显示的方案一致
    plan_id: Option<String>,
) -> Result<PlanOverview> {
    let planning = PlanningState::new(&state.learning);
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::assign_task_date(&app, &planning, task_id, date_value, plan_id.as_deref())
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "任务排期任务执行异常终止"))?
}
