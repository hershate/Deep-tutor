use super::super::security::{session_principal, with_principal};
use super::super::{error::Result, BackendState};
use super::{
    active, deep_link, delete, export_config, list, set_active, test, upsert, ChatMessage,
    ProfileInput, ProviderProfile, ProviderTestResult,
};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

/// P-067/P-068：供应商域的重读与写命令原为同步命令——命令体在主线程执行：
/// list 逐档案读一次系统密钥库（N+1 凭据调用），save_routing_settings 在
/// 同一命令里再做写事务与事件落盘，route_logs 对 100 行运行再逐行查
/// attempts（约 101 次 prepare/query），get_provider_chat 承载流式期间
/// 每 150ms 一次的 MB 级载荷物化。
/// 移后台执行（61#3 返工同款 guard::scoped：命令体在阻塞线程上重新取
/// 工作区切换读锁并解析 principal，读写都落在该用户自己的工作区）
async fn background<R: Runtime + 'static, T: Send + 'static>(
    app: AppHandle<R>,
    task: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || super::super::security::guard::scoped(&app, task))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("internal_error", "供应商任务执行异常终止")
        })?
}

// P-067：档案列表对每个档案读一次系统密钥库填充 has_key（N+1 凭据读取）
// ——原先同步命令在主线程执行，供应商事件密集期界面掉帧；移后台
#[tauri::command(async)]
pub async fn list_provider_profiles<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Vec<ProviderProfile>> {
    let providers = state.providers.shared();
    background(app, move || list(&providers)).await
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

// P-067：取消流式请求要开库改状态并提交事件；原先同步命令在主线程执行，
// 备份导入等长写事务期间会在 busy_timeout（10 秒）上阻塞主线程；移后台
#[tauri::command(async)]
pub async fn cancel_provider_chat<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
) -> Result<()> {
    let providers = state.providers.shared();
    let inner = app.clone();
    background(app, move || {
        super::cancel_chat_with_app(&inner, &providers, &request_id)
    })
    .await
}

#[tauri::command]
pub fn list_provider_circuits(
    state: State<'_, BackendState>,
) -> Result<Vec<super::routing::CircuitSnapshot>> {
    super::routing::circuits(&state.providers)
}

// P-068：流式输出期间前端按 150ms 节流拉取整条请求（output/reasoning
// 累计上限 1 MiB），每次都要开库取整行并做 MB 级序列化——原先同步命令
// 在主线程执行，长输出阶段持续占用主线程导致打字与动画掉帧；移后台
#[tauri::command(async)]
pub async fn get_provider_chat<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    request_id: String,
) -> Result<super::ProviderRequest> {
    let providers = state.providers.shared();
    let practice = state.practice.shared();
    background(app, move || {
        let mut request = super::get_chat(&providers, &request_id)?;
        // P-010：主观评分的模型原始终回文本按反馈公开口径遮蔽——判分 JSON
        // 含逐项得分与总体反馈，公开前回传即等于提前泄题（deferred 练习与
        // 模拟考试的自测效度依赖该遮蔽）
        super::super::practice::mask_grading_output(&practice, &mut request)?;
        Ok(request)
    })
    .await
}

#[tauri::command]
pub fn get_routing_settings(
    state: State<'_, BackendState>,
) -> Result<super::routing::RoutingSettings> {
    super::routing::settings(&state.providers)
}

// P-067：保存路由设置先枚举全部档案（逐个读密钥库），再做写事务与事件
// 落盘——原先同步命令在主线程执行；移后台
#[tauri::command(async)]
pub async fn save_routing_settings<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    settings: super::routing::RoutingSettings,
) -> Result<super::routing::RoutingSettings> {
    let providers = state.providers.shared();
    let inner = app.clone();
    background(app, move || {
        super::routing::save_settings(&inner, &providers, settings)
    })
    .await
}

// P-067：路由日志取 100 行运行后逐行再查 attempts（约 101 次
// prepare/query）——原先同步命令在主线程执行，路由事件密集期占用主线程；
// 移后台
#[tauri::command(async)]
pub async fn list_provider_route_logs<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Vec<super::routing::RouteLog>> {
    let providers = state.providers.shared();
    background(app, move || super::routing::logs(&providers)).await
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
            // 106#7：路由请求 panic 时写入终态，避免残留 running 记录
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
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
            }));
            match outcome {
                Ok(result) => result,
                Err(_) => {
                    let _ = super::fail_chat_panicked(&app, &state, &request_id);
                    Err(super::super::error::BackendError::new(
                        "task_panicked",
                        "路由模型任务异常终止",
                    ))
                }
            }
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("task_join_failed", "路由模型任务异常终止")
    })?
}

// 90#4：用量总览对 provider_requests（库中最大表，86#1 裁剪后仍至多
// 2 万行）做五表 JOIN 全量物化 + 聚合，原先同步命令在主线程执行，行数
// 多时秒级冻结；移后台（73#8 范式）
#[tauri::command(async)]
pub async fn get_usage_overview<R: Runtime + 'static>(
    app: AppHandle<R>,
    days: Option<u32>,
) -> Result<super::usage::UsageOverview> {
    use tauri::Manager;
    // 23#7：principal 传播（guard 的 thread-local 到不了阻塞线程）。
    // 103#11：读类用 _existing（见 materials::get_material 注释）
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
            super::usage::overview(&state.providers, days)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "用量统计任务执行异常终止")
    })?
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

/// 37#5：用量 CSV 改为用户选择位置后由原生后端落盘（替代 WebView Blob 下载）。
/// 90#4：CSV 走同一条五表 JOIN 全量物化，移后台与 get_usage_overview 同族
#[tauri::command(async)]
pub async fn export_usage_csv_file<R: Runtime + 'static>(
    app: AppHandle<R>,
    destination: String,
) -> Result<Value> {
    use tauri::Manager;
    // 103#11：读类用 _existing（见 materials::get_material 注释）
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::super::security::with_principal(principal, || {
            let content = super::usage::export_csv(&state.providers)?;
            super::super::write_export_bytes(&app, &destination, content.as_bytes())
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "用量导出任务执行异常终止")
    })?
}
