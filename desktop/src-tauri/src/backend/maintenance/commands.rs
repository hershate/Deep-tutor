use super::super::{
    error::{BackendError, Result},
    BackendState,
};
use super::{CleanupResult, MaintenanceReport, MaintenanceService, VacuumResult};
use std::sync::{Mutex, MutexGuard};
use tauri::{AppHandle, Runtime, State};

static OPERATION: Mutex<()> = Mutex::new(());

fn store<'a>(
    state: &'a State<'_, BackendState>,
) -> Result<MutexGuard<'a, super::super::store::Store>> {
    state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))
}

fn stop_all(state: &State<'_, BackendState>) -> Result<()> {
    super::super::agent::stop(&state.agent)?;
    super::super::practice::stop(&state.practice)?;
    super::super::knowledge::stop(&state.knowledge)?;
    super::super::learning::stop(&state.learning)?;
    super::super::providers::stop_chats(&state.providers)?;
    super::super::sync::stop(&super::super::sync::SyncService::new(&state.learning))?;
    super::super::materials::stop(&state.materials)
}

#[tauri::command]
pub fn get_maintenance_report<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<MaintenanceReport> {
    super::scan(&app, &MaintenanceService::new(&state.learning))
}

#[tauri::command]
pub fn cleanup_orphan_files<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<CleanupResult> {
    let _operation = OPERATION
        .lock()
        .map_err(|_| BackendError::new("maintenance_unavailable", "运维任务状态暂不可用"))?;
    let result = (|| {
        stop_all(&state)?;
        let _store = store(&state)?;
        super::cleanup(&app, &MaintenanceService::new(&state.learning))
    })();
    super::super::sync::scheduler::start_scheduler(app);
    result
}

#[tauri::command]
pub fn vacuum_database<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<VacuumResult> {
    let _operation = OPERATION
        .lock()
        .map_err(|_| BackendError::new("maintenance_unavailable", "运维任务状态暂不可用"))?;
    let result = (|| {
        stop_all(&state)?;
        let store = store(&state)?;
        super::vacuum(&app, &store, &MaintenanceService::new(&state.learning))
    })();
    super::super::sync::scheduler::start_scheduler(app);
    result
}
