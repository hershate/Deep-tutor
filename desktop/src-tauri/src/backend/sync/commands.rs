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

#[tauri::command]
pub fn get_sync_versions(
    state: State<'_, BackendState>,
) -> Result<Vec<super::versions::SyncVersion>> {
    let service = SyncService::new(&state.learning);
    let config = super::config(&service)?.ok_or_else(|| {
        super::super::error::BackendError::new("sync_not_configured", "请先保存同步配置")
    })?;
    super::versions::list(&service, &config)
}

#[tauri::command]
pub fn restore_sync_version<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    version_path: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    super::restore(
        &app,
        &SyncService::new(&state.learning),
        version_path,
        destination,
    )
}
