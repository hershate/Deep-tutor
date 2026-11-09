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
    let result = super::export(
        &app,
        &state.learning,
        input,
        std::path::Path::new(&destination),
    );
    if result.is_ok() {
        // 72#2：导出回执登记（reveal_path 白名单）
        super::super::reveal::register(std::path::Path::new(&destination));
    }
    result
}

#[tauri::command]
pub fn export_notes_archive<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<super::notes::NotesArchiveResult> {
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    let result = super::notes::archive(&app, &state.learning, std::path::Path::new(&destination));
    if result.is_ok() {
        // 72#2：导出回执登记（reveal_path 白名单）
        super::super::reveal::register(std::path::Path::new(&destination));
    }
    result
}
