use super::super::{error::Result, BackendState};
use super::NotesImportResult;
use std::path::Path;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn import_notes<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<NotesImportResult> {
    super::import(&app, &state, Path::new(&path))
}
