use super::super::{error::Result, BackendState};
use super::{ExportInput, ExportResult};
use tauri::{AppHandle, Runtime};

// 90#1：全量导出（全集合读库 → 渲染 → 内存打包 → 整包哈希 → 写盘）原先
// 同步命令在主线程执行，数十 MB 文本的库全程窗口冻结；移后台（73#8 范式，
// state 经 AppHandle 在阻塞线程内获取）
#[tauri::command(async)]
pub async fn export_content<R: Runtime + 'static>(
    app: AppHandle<R>,
    input: ExportInput,
    destination: String,
) -> Result<ExportResult> {
    use tauri::Manager;
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    // 23#7：principal 传播——guard 在 IPC 线程设置的 thread-local 到不了
    // 阻塞线程，由会话派生当前用户
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
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
        })
    })
    .await
    .map_err(|_| super::super::error::BackendError::new("internal_error", "导出任务执行异常终止"))?
}

// 90#2：笔记归档全量读库 + 内存 ZIP 压缩（Deflate CPU 密集）移后台，
// 与 90#1 同族
#[tauri::command(async)]
pub async fn export_notes_archive<R: Runtime + 'static>(
    app: AppHandle<R>,
    destination: String,
) -> Result<super::notes::NotesArchiveResult> {
    use tauri::Manager;
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
            let result =
                super::notes::archive(&app, &state.learning, std::path::Path::new(&destination));
            if result.is_ok() {
                // 72#2：导出回执登记（reveal_path 白名单）
                super::super::reveal::register(std::path::Path::new(&destination));
            }
            result
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "笔记归档导出任务执行异常终止")
    })?
}
