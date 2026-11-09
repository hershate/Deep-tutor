use super::super::{error::Result, BackendState};
use super::{SyncConfigInput, SyncConfigView, SyncJobView, SyncOverview, SyncService};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_sync_overview(state: State<'_, BackendState>) -> Result<SyncOverview> {
    super::overview(&SyncService::new(&state.learning))
}

#[tauri::command]
pub fn save_sync_config<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: SyncConfigInput,
) -> Result<SyncConfigView> {
    super::save_config(&app, &SyncService::new(&state.learning), input)
}

#[tauri::command]
pub fn start_sync_job<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    kind: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    // 23#10：下载/恢复写盘与 backup/export 等域共用第二道遏制（08#2）——
    // 目的地不得指向应用数据目录内，防止注入脚本借同步下载覆盖工作区文件
    if let Some(target) = destination.as_deref() {
        super::super::reject_data_root_destination(&app, std::path::Path::new(target))?;
    }
    super::start(&app, &SyncService::new(&state.learning), kind, destination)
}

#[tauri::command]
pub fn get_sync_job(state: State<'_, BackendState>, id: String) -> Result<SyncJobView> {
    super::job(&SyncService::new(&state.learning), &id)
}

#[tauri::command]
pub fn cancel_sync_job<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<SyncJobView> {
    super::cancel(&app, &SyncService::new(&state.learning), &id)
}

// 73#6：版本列表来自 S3/WebDAV 真实网络请求（client 总超时 45s + 阻塞式
// DNS 解析）——原先同步命令在主线程执行，打开同步版本列表可冻结至远端
// 响应，挂起站点最长 45-50s。移后台（44#1 同款）
#[tauri::command(async)]
pub async fn get_sync_versions<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<Vec<super::versions::SyncVersion>> {
    use tauri::Manager;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        let service = SyncService::new(&state.learning);
        let config = super::config(&service)?.ok_or_else(|| {
            super::super::error::BackendError::new("sync_not_configured", "请先保存同步配置")
        })?;
        super::versions::list(&service, &config)
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "同步版本列表任务执行异常终止")
    })?
}

#[tauri::command]
pub fn restore_sync_version<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    version_path: String,
    destination: Option<String>,
) -> Result<SyncJobView> {
    // 23#10：同 start_sync_job——恢复写盘目的地不得位于应用数据目录内
    if let Some(target) = destination.as_deref() {
        super::super::reject_data_root_destination(&app, std::path::Path::new(target))?;
    }
    super::restore(
        &app,
        &SyncService::new(&state.learning),
        version_path,
        destination,
    )
}
