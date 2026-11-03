use super::super::{
    error::Result,
    materials::{self, IngestResult},
    BackendState,
};
use super::{fetch, WebPage};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn fetch_webpage(_state: State<'_, BackendState>, url: String) -> Result<WebPage> {
    fetch(&url)
}

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
    )
}
