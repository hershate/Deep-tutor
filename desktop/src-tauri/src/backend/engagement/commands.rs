use super::super::{error::Result, BackendState};
use super::{
    DashboardOverview, EngagementState, LearningProfile, NotificationOverview, ProfileInput,
    SearchResult,
};
use tauri::{AppHandle, Manager, Runtime, State};

// 73#13c：全库搜索（kv 三命名空间 uni_lower+LIKE 无索引全扫 + materials 扫描
// + 全量方案图逐节点 contains）大库 100ms-1s——原先同步命令在主线程执行，
// 全局搜索每击键去抖后仍冻结窗口。移后台（knowledge::search 44#2 同款）
// 114#1：移后台必须带 principal——guard 的 thread-local 到不了阻塞线程池
// （23#7），Store::new 生产分支无 principal 直接拒绝，非空查询整体失败
#[tauri::command(async)]
pub async fn search_workspace<R: Runtime + 'static>(
    app: AppHandle<R>,
    query: String,
) -> Result<Vec<SearchResult>> {
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::search(&EngagementState::new(&state.learning), query)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "工作区搜索任务执行异常终止")
    })?
}

#[tauri::command(async)]
pub async fn get_notifications<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<NotificationOverview> {
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::get_notifications(&EngagementState::new(&state.learning))
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "通知读取任务执行异常终止")
    })?
}

// 114#6：两命令收尾都跑同一个 notification 刷新（写库 + 周日全量聚合），
// 与 get_notifications 同成本类别——同步在主线程执行同样有冻结面；且经
// session principal 解析（23#7）保证与 async 读命令落在同一用户工作区
// （测试拓扑挂 SecurityState 时，sync 命令走 thread-local 会指到 root 库，
// 与 async 命令的 user1 工作区各写各的）
#[tauri::command(async)]
pub async fn mark_all_notifications_read<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<NotificationOverview> {
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::mark_all_read(&app, &EngagementState::new(&state.learning))
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "通知已读任务执行异常终止")
    })?
}

#[tauri::command(async)]
pub async fn mark_notification_read<R: Runtime + 'static>(
    app: AppHandle<R>,
    id: String,
) -> Result<NotificationOverview> {
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::mark_read(&app, &EngagementState::new(&state.learning), id)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "通知已读任务执行异常终止")
    })?
}

#[tauri::command]
pub fn get_learning_profile<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<LearningProfile> {
    super::get_profile(&app, &EngagementState::new(&state.learning))
}

#[tauri::command]
pub fn save_learning_profile<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: ProfileInput,
) -> Result<LearningProfile> {
    super::save_profile(&app, &EngagementState::new(&state.learning), input)
}

#[tauri::command]
pub fn restart_learning_diagnostic<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<LearningProfile> {
    super::restart_diagnostic(&app, &EngagementState::new(&state.learning))
}

// 103#4：dashboard = list_with + stats_with + calendar_with + report_with("4w")
// 四类全量聚合（90#8/90#10 自述要移后台的同一套成本），且被前端每个
// 后端事件触发——原先同步命令在主线程执行，首页热路径的冻结随历史
// 数据量线性增长；只读，principal 用 _existing
#[tauri::command(async)]
pub async fn get_dashboard<R: Runtime + 'static>(app: AppHandle<R>) -> Result<DashboardOverview> {
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::dashboard(&app, &EngagementState::new(&state.learning))
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "仪表盘加载任务执行异常终止")
    })?
}
