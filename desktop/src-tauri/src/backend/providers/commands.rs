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

#[tauri::command]
pub fn cancel_provider_chat(state: State<'_, BackendState>, request_id: String) -> Result<()> {
    super::cancel_chat(&state.providers, &request_id)
}

#[tauri::command]
pub fn get_provider_chat(
    state: State<'_, BackendState>,
    request_id: String,
) -> Result<super::ProviderRequest> {
    super::get_chat(&state.providers, &request_id)
}
