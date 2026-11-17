use super::super::{error::Result, BackendState};
use super::{SyncConfigInput, SyncConfigView, SyncJobView, SyncOverview, SyncService};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_sync_overview(state: State<'_, BackendState>) -> Result<SyncOverview> {
    super::overview(&SyncService::new(&state.learning))
}

// P-159：保存同步配置先做 endpoint() 的阻塞 DNS 解析（to_socket_addrs 在
// 弱网或域名不可达时可在 Windows 上阻塞数秒），原先是同步命令——命令体在
// 主线程执行，点击保存后界面冻结至解析返回或超时；保存配置的域名恰是
// 用户刚输入、最可能不可达的地址。移后台（81#3/73#5 同型）
#[tauri::command(async)]
pub async fn save_sync_config<R: Runtime + 'static>(
    app: AppHandle<R>,
    input: SyncConfigInput,
) -> Result<SyncConfigView> {
    use tauri::Manager;
    // 23#7：async 命令体运行在 tokio worker（guard 在 IPC 线程设置的
    // thread-local principal 不可见）——从会话派生当前用户并显式传播进
    // 阻塞线程，否则 Store::new 会打开错误的工作区
    let principal = super::super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::save_config(&app, &SyncService::new(&state.learning), input)
        })
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "同步配置保存任务执行异常终止")
    })?
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
// 114#1：移后台必须带 principal（23#7 同因）——config() 首行即 Store::new，
// 生产分支无 principal 直接拒绝，版本列表整体失败
#[tauri::command(async)]
pub async fn get_sync_versions<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<Vec<super::versions::SyncVersion>> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            let service = SyncService::new(&state.learning);
            let config = super::config(&service)?.ok_or_else(|| {
                super::super::error::BackendError::new("sync_not_configured", "请先保存同步配置")
            })?;
            super::versions::list(&service, &config)
        })
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
