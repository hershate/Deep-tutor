use super::super::{
    error::Result,
    materials::{self, IngestResult},
    BackendState,
};
use super::fetch;
use tauri::{AppHandle, Runtime, State};

// 61 号：fetch_webpage IPC 壳已删（前端零调用）——抓取链仅供 import_webpage
// 内部复用，测试直调 web::fetch

#[tauri::command]
pub fn import_webpage<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    url: String,
) -> Result<IngestResult> {
    let page = fetch(&url)?;
    materials::import_text_with_source(
        &app,
        &state.materials,
        page.title,
        page.text,
        Some(&page.url),
        page.warning.as_deref(),
    )
}
