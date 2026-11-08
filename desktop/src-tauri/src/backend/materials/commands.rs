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

#[tauri::command(async)]
pub async fn import_material_files<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    paths: Vec<String>,
) -> Result<Vec<ImportOutcome>> {
    // 44#1：≤32×50MB 逐文件读+校验+哈希+写盘的重 IO 移出主线程（原先
    // 同步命令冻结窗口）；23#7：阻塞线程传播 principal
    if paths.len() > 32 {
        return Err(BackendError::new(
            "too_many_files",
            "单次最多导入 32 份资料",
        ));
    }
    let materials = state.materials.shared();
    let principal = super::super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            Ok(import_files(&app, &materials, paths))
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "导入任务执行异常终止"))?
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
pub fn export_material_original<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    destination: String,
) -> Result<Value> {
    super::super::reject_data_root_destination(&app, Path::new(&destination))?;
    export_original(&state.materials, id, Path::new(&destination))
}

#[tauri::command]
pub fn export_material_text<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    destination: String,
) -> Result<Value> {
    super::super::reject_data_root_destination(&app, Path::new(&destination))?;
    super::export_text(&state.materials, id, Path::new(&destination))
}

#[tauri::command]
pub fn get_material_media_info(
    state: State<'_, BackendState>,
    id: i64,
) -> Result<super::media::MediaInfo> {
    super::media::info(&state.materials, id)
}

#[tauri::command]
pub fn read_material_media_chunk(
    state: State<'_, BackendState>,
    id: i64,
    offset: u64,
    length: u64,
) -> Result<super::media::MediaChunk> {
    super::media::chunk(&state.materials, id, offset, length)
}

#[tauri::command(async)]
pub async fn import_material_archive<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<super::archive::ArchiveImportResult> {
    // 44#1：≤200MB ZIP 预检+逐项解压落盘的重 IO 移出主线程
    let materials = state.materials.shared();
    let principal = super::super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::archive::import_archive(&app, &materials, Path::new(&path))
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "导入任务执行异常终止"))?
}
