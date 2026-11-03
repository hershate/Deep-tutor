use super::super::{error::Result, BackendState};
use super::{SyncConfigInput, SyncConfigView, SyncJobView, SyncOverview, SyncService};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_sync_overview(state: State<'_, BackendState>) -> Result<SyncOverview> {
    super::overview(&SyncService::new(&state.learning))
}

#[tauri::command]
pub fn save_sync_config<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: SyncConfigInput,
) -> Result<SyncConfigView> {
    super::save_config(&app, &SyncService::new(&state.learning), input)
}

#[tauri::command]
pub fn start_sync_job<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    kind: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    super::start(&app, &SyncService::new(&state.learning), kind, destination)
}

#[tauri::command]
pub fn get_sync_job(state: State<'_, BackendState>, id: String) -> Result<SyncJobView> {
    super::job(&SyncService::new(&state.learning), &id)
}

#[tauri::command]
pub fn cancel_sync_job<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<SyncJobView> {
    super::cancel(&app, &SyncService::new(&state.learning), &id)
}
