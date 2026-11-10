use super::super::{error::Result, BackendState};
use super::{CreatedShare, ShareMetadata, SharedPlanView};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn create_shared_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    plan_id: String,
    expires_in_days: Option<u32>,
) -> Result<CreatedShare> {
    super::create(&app, &state.sharing, &plan_id, expires_in_days)
}

#[tauri::command]
pub fn list_shared_plans(
    state: State<'_, BackendState>,
    plan_id: Option<String>,
) -> Result<Vec<ShareMetadata>> {
    super::list(&state.sharing, plan_id.as_deref())
}

#[tauri::command]
pub fn read_shared_plan(state: State<'_, BackendState>, token: String) -> Result<SharedPlanView> {
    super::read_token(&state.sharing, &token)
}

#[tauri::command]
pub fn revoke_shared_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ShareMetadata> {
    super::revoke(&app, &state.sharing, id)
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
