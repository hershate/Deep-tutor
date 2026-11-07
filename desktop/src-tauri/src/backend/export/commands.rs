use super::super::{error::Result, BackendState};
use super::{ExportInput, ExportResult};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn export_content<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: ExportInput,
    destination: String,
) -> Result<ExportResult> {
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    super::export(
        &app,
        &state.learning,
        input,
        std::path::Path::new(&destination),
    )
}

#[tauri::command]
pub fn export_notes_archive<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<super::notes::NotesArchiveResult> {
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    super::notes::archive(&app, &state.learning, std::path::Path::new(&destination))
}
