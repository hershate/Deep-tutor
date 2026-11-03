use super::super::{error::Result, BackendState};
use super::{fetch, WebPage};
use tauri::State;

#[tauri::command]
pub fn fetch_webpage(_state: State<'_, BackendState>, url: String) -> Result<WebPage> {
    fetch(&url)
}
