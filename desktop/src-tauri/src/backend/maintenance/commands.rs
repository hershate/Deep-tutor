use super::super::{
    error::{BackendError, Result},
    BackendState,
};
use super::{CleanupResult, MaintenanceReport, MaintenanceService, VacuumResult};
use std::sync::{Mutex, MutexGuard};
use tauri::{AppHandle, Runtime, State};

static OPERATION: Mutex<()> = Mutex::new(());

// 56#2：与登出/切换共用 security::guard::stop_all（逐域独立停并汇总错误，
// 含 focus 收口）——原先此处另有一份串行 `?` 副本，同样会被 agent 超时短路
fn stop_all(state: &State<'_, BackendState>) -> Result<()> {
    super::super::security::guard::stop_all(state)
}

fn store<'a>(
    state: &'a State<'_, BackendState>,
) -> Result<MutexGuard<'a, super::super::store::Store>> {
    state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))
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
