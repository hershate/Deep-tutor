use super::super::{error::Result, BackendState};
use super::{
    active, deep_link, delete, export_config, list, set_active, test, upsert, ChatMessage,
    ProfileInput, ProviderProfile, ProviderTestResult,
};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn list_provider_profiles(state: State<'_, BackendState>) -> Result<Vec<ProviderProfile>> {
    list(&state.providers)
}

#[tauri::command]
pub fn get_active_provider(state: State<'_, BackendState>) -> Result<Option<String>> {
    active(&state.providers)
}

#[tauri::command(async)]
pub async fn upsert_provider_profile<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: ProfileInput,
) -> Result<ProviderProfile> {
    let state = state.providers.shared();
    tauri::async_runtime::spawn_blocking(move || upsert(&app, &state, input))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("task_join_failed", "供应商档案保存任务异常终止")
        })?
}

#[tauri::command]
pub fn set_active_provider<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    set_active(&app, &state.providers, &id)
}

#[tauri::command]
pub fn delete_provider_profile<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    delete(&app, &state.providers, &id)
}

#[tauri::command]
pub fn export_provider_config(state: State<'_, BackendState>) -> Result<Value> {
    export_config(&state.providers)
}

#[tauri::command]
pub fn import_provider_deep_link<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    link: String,
) -> Result<ProviderProfile> {
    deep_link(&app, &state.providers, &link)
}

#[tauri::command(async)]
pub async fn test_provider<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    real_request: bool,
) -> Result<ProviderTestResult> {
    let state = state.providers.shared();
    tauri::async_runtime::spawn_blocking(move || test(&app, &state, &id, real_request))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("task_join_failed", "供应商检测任务异常终止")
        })?
}

#[tauri::command(async)]
pub async fn stream_provider_chat<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
    profile_id: String,
    messages: Vec<ChatMessage>,
    max_tokens: u32,
) -> Result<super::ProviderRequest> {
    let state = state.providers.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::stream_chat(
            &app,
            &state,
            &request_id,
            &profile_id,
            &messages,
            max_tokens,
        )
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "模型流式任务异常终止")
    })?
}

#[tauri::command(async)]
pub async fn stream_provider_chat_with_tools<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
    profile_id: String,
    messages: Vec<ChatMessage>,
    max_tokens: u32,
    tools: Option<Vec<super::tools::ToolDefinition>>,
) -> Result<super::ProviderRequest> {
    let state = state.providers.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::stream_chat_with_tools(
            &app,
            &state,
            &request_id,
            &profile_id,
            &messages,
            max_tokens,
            tools.as_deref(),
        )
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "工具调用流式任务异常终止")
    })?
}

#[tauri::command]
pub fn cancel_provider_chat<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
) -> Result<()> {
    super::cancel_chat_with_app(&app, &state.providers, &request_id)
}

#[tauri::command]
pub fn list_provider_circuits(
    state: State<'_, BackendState>,
) -> Result<Vec<super::routing::CircuitSnapshot>> {
    super::routing::circuits(&state.providers)
}

#[tauri::command]
pub fn get_provider_chat(
    state: State<'_, BackendState>,
    request_id: String,
) -> Result<super::ProviderRequest> {
    super::get_chat(&state.providers, &request_id)
}

#[tauri::command]
pub fn get_routing_settings(
    state: State<'_, BackendState>,
) -> Result<super::routing::RoutingSettings> {
    super::routing::settings(&state.providers)
}

#[tauri::command]
pub fn save_routing_settings<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    settings: super::routing::RoutingSettings,
) -> Result<super::routing::RoutingSettings> {
    super::routing::save_settings(&app, &state.providers, settings)
}

#[tauri::command]
pub fn list_provider_route_logs(
    state: State<'_, BackendState>,
) -> Result<Vec<super::routing::RouteLog>> {
    super::routing::logs(&state.providers)
}

#[tauri::command(async)]
pub async fn route_provider_chat<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
    task: String,
    profile_id: Option<String>,
    model: Option<String>,
    messages: Vec<ChatMessage>,
    max_tokens: u32,
    tools: Option<Vec<super::tools::ToolDefinition>>,
) -> Result<super::ProviderRequest> {
    let state = state.providers.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::routing::stream_chat(
            &app,
            &state,
            &request_id,
            &task,
            profile_id.as_deref(),
            &messages,
            max_tokens,
            model.as_deref(),
            tools.as_deref(),
            false,
        )
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "路由模型任务异常终止")
    })?
}
