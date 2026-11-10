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

// 90#12：资料详情整篇正文经 IPC 返回（MB 级 JSON 序列化 + material_text
// 读库），移后台减少详情页打开时主线程占用（契约不变）
#[tauri::command(async)]
pub async fn get_material<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<MaterialDetail> {
    // 23#7：principal 传播（guard 的 thread-local 到不了阻塞线程）
    let principal = super::super::security::session_principal(&app)?;
    let materials = state.materials.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || detail(&materials, id))
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "资料详情读取任务执行异常终止"))?
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

// 73#8：原件导出整读 ≤50MB + SHA256 + 写盘——原先同步命令在主线程执行，
// HDD 上 0.3-2s 冻结。移后台（44#1 同款）；72#2 回执登记随成功路径照常
#[tauri::command(async)]
pub async fn export_material_original<R: Runtime + 'static>(
    app: AppHandle<R>,
    id: i64,
    destination: String,
) -> Result<Value> {
    use tauri::Manager;
    super::super::reject_data_root_destination(&app, Path::new(&destination))?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        let result = export_original(&state.materials, id, Path::new(&destination));
        if result.is_ok() {
            // 72#2：导出回执登记（reveal_path 白名单）
            super::super::reveal::register(Path::new(&destination));
        }
        result
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "原件导出任务执行异常终止")
    })?
}

// 90#6：解析文本导出（整篇文本哈希 + 写盘）移后台，随导出族一并收口
#[tauri::command(async)]
pub async fn export_material_text<R: Runtime + 'static>(
    app: AppHandle<R>,
    id: i64,
    destination: String,
) -> Result<Value> {
    use tauri::Manager;
    super::super::reject_data_root_destination(&app, Path::new(&destination))?;
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
            let result = super::export_text(&state.materials, id, Path::new(&destination));
            if result.is_ok() {
                // 72#2：导出回执登记（reveal_path 白名单）
                super::super::reveal::register(Path::new(&destination));
            }
            result
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "解析文本导出任务执行异常终止"))?
}

// 90#6：info 全量流式 SHA-256（≤50MB 原件逐块读+哈希）移后台——
// E-3 只解决了不整读进内存，未解决线程位置
#[tauri::command(async)]
pub async fn get_material_media_info<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<super::media::MediaInfo> {
    let principal = super::super::security::session_principal_existing(&app)?;
    let materials = state.materials.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::media::info(&materials, id))
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "媒体信息读取任务执行异常终止"))?
}

// 90#12：媒体分块高频读（视频拖动进度条每次数十 ms 文件 IO）移后台，
// 25#4 分块协议不变
#[tauri::command(async)]
pub async fn read_material_media_chunk<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    offset: u64,
    length: u64,
) -> Result<tauri::ipc::Response> {
    let principal = super::super::security::session_principal_existing(&app)?;
    let materials = state.materials.shared();
    // 46#2：二进制应答（数字数组 JSON 通道的 3–4× 内存峰值消除）
    let bytes = tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::media::chunk_bytes(&materials, id, offset, length)
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "媒体数据读取任务执行异常终止"))??;
    Ok(tauri::ipc::Response::new(bytes))
}

// 90#6：完整性校验（≤50MB 原件流式 SHA-256）移后台
#[tauri::command(async)]
pub async fn verify_material_media<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<()> {
    let principal = super::super::security::session_principal_existing(&app)?;
    let materials = state.materials.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::media::verify(&materials, id))
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "媒体校验任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn import_material_archive<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<super::archive::ArchiveImportResult> {
    // 44#1：≤200MB ZIP 预检+逐项解压落盘的重 IO 移出主线程
    let materials = state.materials.shared();
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::archive::import_archive(&app, &materials, Path::new(&path))
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "导入任务执行异常终止"))?
}
