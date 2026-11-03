mod archive;
pub mod commands;
#[cfg(test)]
mod tests;
mod transfer;

use super::commands::emit;
use super::error::Result;
use super::learning::LearningState;
use super::store::Store;
use serde_json::{json, Value};
use std::path::Path;
use tauri::{AppHandle, Runtime};

pub use archive::{BackupPreview, BackupResult};

pub fn export<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    state: &LearningState,
    destination: &Path,
) -> Result<BackupResult> {
    archive::export(app, store, state, destination)
}

pub fn preview(state: &LearningState, path: &Path) -> Result<BackupPreview> {
    archive::open(path, state).and_then(|backup| backup.preview())
}

pub fn import<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    state: &LearningState,
    path: &Path,
) -> Result<Value> {
    let mut backup = archive::open(path, state)?;
    let counts = transfer::import(&mut backup, store, state)?;
    super::sharing::reindex(&super::sharing::SharingState::new(state))?;
    let files = backup.manifest.files.len();
    let event = Store::event(
        &store.conn,
        "backup.imported",
        json!({"counts": counts, "files": files, "appendOnly": true}),
    )?;
    emit(app, &event);
    super::learning::recover(app, state)?;
    super::agent::recover(
        app,
        &super::agent::AgentState::new(state.database_path(), &state.provider_state(), state),
    )?;
    super::practice::recover(
        app,
        &super::practice::PracticeState::new(state.database_path(), &state.provider_state(), state),
    )?;
    super::knowledge::recover(
        app,
        &super::knowledge::KnowledgeState::new(
            state.database_path(),
            &state.provider_state(),
            &state.material_state(),
        ),
    )?;
    super::providers::recover_chats(app, &state.provider_state())?;
    super::providers::routing::recover(app, &state.provider_state())?;
    super::materials::recover(app, &state.material_state())?;
    super::sync::recover(app, &super::sync::SyncService::new(state))?;
    store.app_state()
}
