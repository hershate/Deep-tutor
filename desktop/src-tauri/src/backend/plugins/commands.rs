use super::super::{error::Result, BackendState};
use super::{AssetContent, PluginEventView, PluginOverview, PluginView};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn install_plugin<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    content: String,
) -> Result<PluginView> {
    super::install(&app, &state.plugins, content)
}

#[tauri::command]
pub fn set_plugin_enabled<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    enabled: bool,
) -> Result<PluginView> {
    super::set_enabled(&app, &state.plugins, id, enabled)
}

#[tauri::command]
pub fn reload_plugin<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<PluginView> {
    super::reload(&app, &state.plugins, id)
}

#[tauri::command]
pub fn uninstall_plugin<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    super::uninstall(&app, &state.plugins, id)
}

#[tauri::command]
pub fn emit_plugin_ping<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<i64> {
    super::ping(&app, &state.plugins)
}

#[tauri::command]
pub fn get_plugin_overview(state: State<'_, BackendState>) -> Result<PluginOverview> {
    super::overview(&state.plugins)
}

#[tauri::command]
pub fn list_plugin_events(
    state: State<'_, BackendState>,
    plugin_id: Option<String>,
    before: Option<i64>,
) -> Result<Vec<PluginEventView>> {
    super::events(&state.plugins, plugin_id, before)
}

#[tauri::command]
pub fn get_developer_asset(state: State<'_, BackendState>, id: String) -> Result<AssetContent> {
    super::asset(&state.plugins, &id)
}
