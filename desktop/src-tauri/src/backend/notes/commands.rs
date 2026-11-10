use super::super::{error::Result, BackendState};
use super::NotesImportResult;
use std::path::Path;
use tauri::{AppHandle, Runtime, State};

// 90#3：导入整读 ≤64MB + ZIP 解压 + 批量 INSERT + 全量状态重建原先同步
// 命令在主线程执行，归档越大冻结越久；移后台（对照 import_material_archive
// 已在 async 族，73#8 范式）
#[tauri::command(async)]
pub async fn import_notes<R: Runtime + 'static>(
    app: AppHandle<R>,
    path: String,
) -> Result<NotesImportResult> {
    use tauri::Manager;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::import(&app, &state, Path::new(&path))
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "笔记导入任务执行异常终止")
    })?
}

#[tauri::command]
pub fn get_library_overview(
    state: State<'_, BackendState>,
) -> Result<super::library::LibraryOverview> {
    super::library::overview(&state)
}
