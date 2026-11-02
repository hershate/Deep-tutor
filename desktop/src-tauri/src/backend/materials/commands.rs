use super::super::{
    error::{BackendError, Result},
    BackendState,
};
use super::{
    cancel, delete, detail, enqueue, export_original, import_files, list, ImportOutcome,
    IngestResult, JobAccepted, Material, MaterialDetail,
};
use serde_json::Value;
use std::path::Path;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn import_material_files<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    paths: Vec<String>,
) -> Result<Vec<ImportOutcome>> {
    if paths.len() > 32 {
        return Err(BackendError::new(
            "too_many_files",
            "单次最多导入 32 份资料",
        ));
    }
    Ok(import_files(&app, &state.materials, paths))
}

#[tauri::command]
pub fn import_material_text<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    title: String,
    content: String,
) -> Result<IngestResult> {
    super::import_text(&app, &state.materials, title, content)
}

#[tauri::command]
pub fn list_materials(state: State<'_, BackendState>) -> Result<Vec<Material>> {
    list(&state.materials)
}

#[tauri::command]
pub fn get_material(state: State<'_, BackendState>, id: i64) -> Result<MaterialDetail> {
    detail(&state.materials, id)
}

#[tauri::command]
pub fn enqueue_material_parse<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<JobAccepted> {
    enqueue(&app, &state.materials, id)
}

#[tauri::command]
pub fn cancel_material_parse<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<Material> {
    cancel(&app, &state.materials, id)
}

#[tauri::command]
pub fn delete_material<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<Value> {
    delete(&app, &state.materials, id)?;
    state.materials.store()?.app_state()
}

#[tauri::command]
pub fn export_material_original(
    state: State<'_, BackendState>,
    id: i64,
    destination: String,
) -> Result<Value> {
    export_original(&state.materials, id, Path::new(&destination))
}

#[tauri::command]
pub fn export_material_text(
    state: State<'_, BackendState>,
    id: i64,
    destination: String,
) -> Result<Value> {
    super::export_text(&state.materials, id, Path::new(&destination))
}
