use super::super::{error::Result, BackendState};
use super::{
    DashboardOverview, EngagementState, LearningProfile, NotificationOverview, ProfileInput,
    SearchResult,
};
use tauri::{AppHandle, Manager, Runtime, State};

// 73#13c：全库搜索（kv 三命名空间 uni_lower+LIKE 无索引全扫 + materials 扫描
// + 全量方案图逐节点 contains）大库 100ms-1s——原先同步命令在主线程执行，
// 全局搜索每击键去抖后仍冻结窗口。移后台（knowledge::search 44#2 同款）
#[tauri::command(async)]
pub async fn search_workspace<R: Runtime + 'static>(
    app: AppHandle<R>,
    query: String,
) -> Result<Vec<SearchResult>> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::search(&EngagementState::new(&state.learning), query)
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "工作区搜索任务执行异常终止")
    })?
}

#[tauri::command]
pub fn get_notifications(state: State<'_, BackendState>) -> Result<NotificationOverview> {
    super::get_notifications(&EngagementState::new(&state.learning))
}

#[tauri::command]
pub fn mark_all_notifications_read<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<NotificationOverview> {
    super::mark_all_read(&app, &EngagementState::new(&state.learning))
}

#[tauri::command]
pub fn mark_notification_read<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<NotificationOverview> {
    super::mark_read(&app, &EngagementState::new(&state.learning), id)
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

#[tauri::command]
pub fn get_dashboard<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<DashboardOverview> {
    super::dashboard(&app, &EngagementState::new(&state.learning))
}
