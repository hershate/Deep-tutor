use super::super::{
    error::{BackendError, Result},
    materials::{self, IngestResult},
    security::{session_principal, with_principal},
    BackendState,
};
use super::fetch;
use tauri::{AppHandle, Manager, Runtime};

// 61 号：fetch_webpage IPC 壳已删（前端零调用）——抓取链仅供 import_webpage
// 内部复用，测试直调 web::fetch

// 73#5：抓取是网络 IO（connect 5s/总 12s 超时）——原先同步命令在主线程
// 执行，弱网/挂起站点冻结窗口最长 ~12-17s。移后台（44#1 同款）；
// 23#7 principal 先派生再传播（ingest 写入当前用户行）
#[tauri::command(async)]
pub async fn import_webpage<R: Runtime + 'static>(
    app: AppHandle<R>,
    url: String,
) -> Result<IngestResult> {
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let state = app.state::<BackendState>();
            let page = fetch(&url)?;
            materials::import_text_with_source(
                &app,
                &state.materials,
                page.title,
                page.text,
                Some(&page.url),
                page.warning.as_deref(),
            )
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "网页导入任务执行异常终止"))?
}
