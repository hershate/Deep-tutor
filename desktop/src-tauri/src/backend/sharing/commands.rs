use super::super::{error::BackendError, error::Result, BackendState};
use super::{CreatedShare, ShareMetadata, SharedPlanView};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

// 62#1：分享域四个命令原先是同步命令，在主线程执行——令牌未命中索引时要
// 遍历 users/ 下每个用户工作区库并逐个完整打开（建表、指纹、迁移），创建
// 要开两个连接做两次提交并对快照做两轮完整序列化，列表要逐行反序列化
// payload_json；窗口冻结随分享规模增长。移入阻塞线程池，命令名与参数
// 契约不变（前端调用方式不变）。

// 23#7：async 命令体运行在 tokio worker，guard 在 IPC 线程设置的
// thread-local principal 到不了那里——从会话派生当前用户再显式传播进
// 阻塞线程，否则会打开错误的工作区库（写命令用 session_principal，与
// 其余写命令一致；读命令用 _existing，不自动签发新会话）
#[tauri::command(async)]
pub async fn create_shared_plan<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    plan_id: String,
    expires_in_days: Option<u32>,
) -> Result<CreatedShare> {
    let sharing = state.sharing.shared();
    let principal = super::super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::create(&app, &sharing, &plan_id, expires_in_days)
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "分享创建任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn list_shared_plans<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    plan_id: Option<String>,
) -> Result<Vec<ShareMetadata>> {
    let sharing = state.sharing.shared();
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::list(&sharing, plan_id.as_deref())
        })
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "分享列表加载任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn read_shared_plan(
    state: State<'_, BackendState>,
    token: String,
) -> Result<SharedPlanView> {
    let sharing = state.sharing.shared();
    tauri::async_runtime::spawn_blocking(move || {
        // 31#10 语义保持：分享令牌按令牌跨用户定位——security/guard.rs 的
        // read_shared_plan 白名单不挂 principal（登录态与未登录都能读），
        // 这里显式以无 principal 上下文执行，与原同步命令一致
        super::super::security::with_principal(None, || super::read_token(&sharing, &token))
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "分享读取任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn revoke_shared_plan<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ShareMetadata> {
    let sharing = state.sharing.shared();
    let principal = super::super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::revoke(&app, &sharing, id))
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "分享撤销任务执行异常终止"))?
}

// 103#7：分享包读/写为单文件 IO（较轻），原先同步命令在主线程执行——
// 慢盘上可感卡顿；移后台
#[tauri::command(async)]
pub async fn read_shared_plan_package<R: tauri::Runtime + 'static>(
    app: tauri::AppHandle<R>,
    package: String,
) -> Result<SharedPlanView> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    let sharing = {
        let state = app.state::<BackendState>();
        super::SharingState::new(&state.learning)
    };
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::read_package(&sharing, &package)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "分享包读取任务执行异常终止")
    })?
}

#[tauri::command(async)]
pub async fn export_shared_plan_package<R: tauri::Runtime + 'static>(
    app: tauri::AppHandle<R>,
    package: String,
    destination: String,
) -> Result<Value> {
    use tauri::Manager;
    // 114#3：补数据根拒绝——全仓写盘入口（08#2/25#18 威胁模型：注入脚本
    // 借导出命令覆盖工作区库/原件）此前唯一漏网；对照 restore_sync_version
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    let principal = super::super::security::session_principal(&app)?;
    let sharing = {
        let state = app.state::<BackendState>();
        super::SharingState::new(&state.learning)
    };
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let result =
                super::export_package(&sharing, &package, std::path::Path::new(&destination));
            if result.is_ok() {
                // 72#2 同款：导出回执登记（reveal_path 白名单）
                super::super::reveal::register(std::path::Path::new(&destination));
            }
            result
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "分享包导出任务执行异常终止")
    })?
}

#[tauri::command(async)]
pub async fn read_shared_plan_file<R: tauri::Runtime + 'static>(
    app: tauri::AppHandle<R>,
    path: String,
) -> Result<SharedPlanView> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    let sharing = {
        let state = app.state::<BackendState>();
        super::SharingState::new(&state.learning)
    };
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::read_package_file(&sharing, std::path::Path::new(&path))
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "分享包读取任务执行异常终止")
    })?
}
