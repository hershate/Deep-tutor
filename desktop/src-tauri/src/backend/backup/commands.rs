use super::super::{
    agent,
    error::{BackendError, Result},
    knowledge, learning, practice, providers, BackendState,
};
use super::{BackupPreview, BackupResult};
use serde_json::Value;
use std::sync::MutexGuard;
use tauri::{AppHandle, Runtime, State};

fn store<'a>(
    state: &'a State<'_, BackendState>,
) -> Result<MutexGuard<'a, super::super::store::Store>> {
    state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))
}

#[tauri::command]
pub fn export_full_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<BackupResult> {
    super::super::sync::stop(&super::super::sync::SyncService::new(&state.learning))?;
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    let store = store(&state)?;
    let result = super::export(
        &app,
        &store,
        &state.learning,
        std::path::Path::new(&destination),
    );
    super::super::sync::scheduler::start_scheduler(app.clone());
    result
}

#[tauri::command]
pub fn preview_full_backup(state: State<'_, BackendState>, path: String) -> Result<BackupPreview> {
    super::preview(&state.learning, std::path::Path::new(&path))
}

#[tauri::command]
pub fn import_full_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<Value> {
    agent::stop(&state.agent)?;
    practice::stop(&state.practice)?;
    knowledge::stop(&state.knowledge)?;
    learning::stop(&state.learning)?;
    providers::stop_chats(&state.providers)?;
    super::super::sync::stop(&super::super::sync::SyncService::new(&state.learning))?;
    let store = store(&state)?;
    let result = super::import(&app, &store, &state.learning, std::path::Path::new(&path));
    super::super::sync::scheduler::start_scheduler(app.clone());
    result
}
