use super::super::security::{session_principal, with_principal};
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
    // 23#7：async 命令体运行在 tokio worker（respond_async spawn），
    // guard 在 IPC 线程设置的 thread-local principal 不可见——从会话派生
    // 当前用户再显式传播进阻塞线程，否则 Store::new 会打开错误的工作区
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || upsert(&app, &state, input))
    })
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

/// 37#5：脱敏配置 JSON 改为用户选择位置后由原生后端落盘（替代 WebView Blob 下载）
#[tauri::command]
pub fn export_provider_config_file<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<Value> {
    let content = export_config(&state.providers)?;
    let bytes = serde_json::to_vec_pretty(&content).map_err(|_| {
        super::super::error::BackendError::new("export_failed", "供应商配置序列化失败")
    })?;
    super::super::write_export_bytes(&app, &destination, &bytes)
}

// 81#3：async 化对齐 upsert/test 范本——deep_link 内经 endpoint() 做
// 阻塞 DNS 解析与凭据库/SQLite 读写，原先同步命令在主线程执行，弱网下
// 点击 deep link 界面冻结数秒（44#1/73#5 同型）
#[tauri::command(async)]
pub async fn import_provider_deep_link<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    link: String,
) -> Result<ProviderProfile> {
    let state = state.providers.shared();
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || deep_link(&app, &state, &link))
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "Deep Link 导入任务异常终止")
    })?
}

#[tauri::command(async)]
pub async fn test_provider<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    real_request: bool,
) -> Result<ProviderTestResult> {
    let state = state.providers.shared();
    // 23#7：同 upsert——会话派生 principal 随闭包显式传播到阻塞线程
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || test(&app, &state, &id, real_request))
    })
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
    // 23#7：同 upsert——会话派生 principal 随闭包显式传播到阻塞线程
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            super::stream_chat(
                &app,
                &state,
                &request_id,
                &profile_id,
                &messages,
                max_tokens,
            )
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "模型流式任务异常终止")
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
    // 23#7：同 upsert——会话派生 principal 随闭包显式传播到阻塞线程
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
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
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "路由模型任务异常终止")
    })?
}

#[tauri::command]
pub fn get_usage_overview(
    state: State<'_, BackendState>,
    days: Option<u32>,
) -> Result<super::usage::UsageOverview> {
    super::usage::overview(&state.providers, days)
}

#[tauri::command]
pub fn save_usage_settings<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    settings: super::usage::UsageSettings,
) -> Result<super::usage::UsageSettings> {
    super::usage::save_settings(&app, &state.providers, settings)
}

#[tauri::command]
pub fn upsert_usage_rate<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: super::usage::RateInput,
) -> Result<super::usage::UsageRate> {
    super::usage::upsert_rate(&app, &state.providers, input)
}

/// 37#5：用量 CSV 改为用户选择位置后由原生后端落盘（替代 WebView Blob 下载）；
/// 61 号：内存版 export_usage_csv 命令已删（前端零调用）
#[tauri::command]
pub fn export_usage_csv_file<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<Value> {
    let content = super::usage::export_csv(&state.providers)?;
    super::super::write_export_bytes(&app, &destination, content.as_bytes())
}
