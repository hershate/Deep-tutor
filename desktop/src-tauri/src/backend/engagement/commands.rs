use super::super::{error::Result, BackendState};
use super::{
    DashboardOverview, EngagementState, LearningProfile, NotificationOverview, ProfileInput,
    SearchResult,
};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn search_workspace(
    state: State<'_, BackendState>,
    query: String,
) -> Result<Vec<SearchResult>> {
    super::search(&EngagementState::new(&state.learning), query)
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
