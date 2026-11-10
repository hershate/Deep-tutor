use super::super::{error::Result, BackendState};
use super::NotesImportResult;
use std::path::Path;
use tauri::{AppHandle, Runtime};

// 90#3：导入整读 ≤64MB + ZIP 解压 + 批量 INSERT + 全量状态重建原先同步
// 命令在主线程执行，归档越大冻结越久；移后台（对照 import_material_archive
// 已在 async 族，73#8 范式）
#[tauri::command(async)]
pub async fn import_notes<R: Runtime + 'static>(
    app: AppHandle<R>,
    path: String,
) -> Result<NotesImportResult> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
            super::import(&app, &state, Path::new(&path))
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "笔记导入任务执行异常终止")
    })?
}

// 103#7：总览 = 全量 notes 集合反序列化 + 全表 annotation + 每批注一次
// plan 全量 JSON 读（90#9 同款成本）且全程持 store 锁——原先同步命令在
// 主线程执行；移后台，锁获取随之挪进闭包
#[tauri::command(async)]
pub async fn get_library_overview<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<super::library::LibraryOverview> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::library::overview(&state)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "笔记总览加载任务执行异常终止")
    })?
}
