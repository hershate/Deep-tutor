use super::super::error::{BackendError, Result};
use super::{CreatedToken, LocalUser, SecurityOverview, SecurityState};
use tauri::{AppHandle, Manager, Runtime, State};

// 58#2：本命令族原先全部是同步命令——tauri 在主线程内联执行命令体，
// 切换用户（guard.rs 的 switch：八域并行停机 + 目标工作区首开 + 十域
// 恢复）、退出登录（stop_all）与令牌登录（停机 + 签发 + 换库）都在主
// 线程串行跑完，等待上限取最慢单域（sync 50s），窗口在此期间完全无
// 响应。改为 async 命令 + spawn_blocking 后，命令体在后台线程池执行，
// 主线程只负责派发（与 backend/commands.rs 的 103#6 / 106#4 / 127#1
// 同口径）。
//
// login_local_user 例外：它必须保持同步命令——guard 的 switch 在同一
// 临界段里「换库 → 派发命令体 → 复核会话用户 → 回滚或恢复」，命令体
// 异步化会让复核早于签发完成而误判回滚。切换段本身已整体移到后台
// 线程（guard.rs 的 switch / switch_blocking），命令体在该后台线程内
// 联执行。

/// 后台线程异常终止时的统一错误——命令体 panic 不会被 tauri 转成响应，
/// 只能由 spawn_blocking 的 JoinError 兜底。
fn aborted(task: &str) -> BackendError {
    BackendError::new("internal_error", &format!("{task}执行异常终止"))
}

#[tauri::command(async)]
pub async fn get_security_overview<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::overview(&handle, &state)
    })
    .await
    .map_err(|_| aborted("安全概览读取"))?
}

#[tauri::command]
pub fn login_local_user<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    user_id: i64,
) -> Result<SecurityOverview> {
    super::login(&app, &state, user_id)
}

#[tauri::command(async)]
pub async fn logout_local_user<R: Runtime + 'static>(app: AppHandle<R>) -> Result<()> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::logout(&handle, &state)
    })
    .await
    .map_err(|_| aborted("退出登录"))?
}

#[tauri::command(async)]
pub async fn create_local_user<R: Runtime + 'static>(
    app: AppHandle<R>,
    name: String,
    role: String,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::create_user(&handle, &state, name, role)
    })
    .await
    .map_err(|_| aborted("创建本地用户"))?
}

#[tauri::command(async)]
pub async fn set_local_user_active<R: Runtime + 'static>(
    app: AppHandle<R>,
    user_id: i64,
    active: bool,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::set_active(&handle, &state, user_id, active)
    })
    .await
    .map_err(|_| aborted("本地用户状态变更"))?
}

#[tauri::command(async)]
pub async fn remove_local_user<R: Runtime + 'static>(
    app: AppHandle<R>,
    user_id: i64,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::remove_user(&handle, &state, user_id)
    })
    .await
    .map_err(|_| aborted("删除本地用户"))?
}

#[tauri::command(async)]
pub async fn create_access_token<R: Runtime + 'static>(
    app: AppHandle<R>,
    name: String,
) -> Result<CreatedToken> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::create_token(&handle, &state, name)
    })
    .await
    .map_err(|_| aborted("创建访问令牌"))?
}

#[tauri::command(async)]
pub async fn revoke_access_token<R: Runtime + 'static>(
    app: AppHandle<R>,
    token_id: i64,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::revoke_token(&handle, &state, token_id)
    })
    .await
    .map_err(|_| aborted("吊销访问令牌"))?
}

#[tauri::command(async)]
pub async fn authenticate_access_token<R: Runtime + 'static>(
    app: AppHandle<R>,
    token: String,
) -> Result<LocalUser> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::authenticate_token(&handle, &state, token)
    })
    .await
    .map_err(|_| aborted("令牌登录"))?
}

#[tauri::command(async)]
pub async fn set_security_quota<R: Runtime + 'static>(
    app: AppHandle<R>,
    user_id: i64,
    resource: String,
    limit: u64,
) -> Result<SecurityOverview> {
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let state = handle.state::<SecurityState>();
        super::set_quota(&handle, &state, user_id, resource, limit)
    })
    .await
    .map_err(|_| aborted("配额设置"))?
}
