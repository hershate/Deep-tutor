use super::super::{
    error::{BackendError, Result},
    security::{session_principal, with_principal},
    BackendState,
};
use super::{CleanupResult, MaintenanceReport, MaintenanceService, VacuumResult};
use std::sync::{Mutex, MutexGuard};
use tauri::{AppHandle, Manager, Runtime};

static OPERATION: Mutex<()> = Mutex::new(());

fn blocking_panic() -> BackendError {
    BackendError::new("internal_error", "运维任务执行异常终止")
}

fn store<'a>(
    state: &'a tauri::State<'_, BackendState>,
) -> Result<MutexGuard<'a, super::super::store::Store>> {
    state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))
}

// 73#4：体检页打开即触发 integrity_check 全库扫描 + 全目录树 walk——原先
// 同步命令在主线程执行，随库与文件数线性冻结 0.3-5s。移后台（44#1 同款）；
// 23#7 principal 先派生再传播
#[tauri::command(async)]
pub async fn get_maintenance_report<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<MaintenanceReport> {
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::scan(&app, &MaintenanceService::new(&state.learning))
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}

// 73#3：清理 = stop_all（最坏 ~140s 串行停机）+ 全目录 walk + 500 项删除，
// 原先全程主线程。移后台；OPERATION 互斥在后台线程内获取，重入仍被拒
#[tauri::command(async)]
pub async fn cleanup_orphan_files<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<CleanupResult> {
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let state = app.state::<BackendState>();
            let _operation = OPERATION.lock().map_err(|_| {
                BackendError::new("maintenance_unavailable", "运维任务状态暂不可用")
            })?;
            let result = (|| {
                super::super::security::guard::stop_all(&state)?;
                let _store = store(&state)?;
                super::cleanup(&app, &MaintenanceService::new(&state.learning))
            })();
            super::super::sync::scheduler::start_scheduler(app.clone());
            result
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}

// 73#2：integrity_check + 事件裁剪 + VACUUM 整库重写——几十 MB 库 1-5s、
// GB 级 30-60s+，原先同步命令在主线程执行，Windows 5 秒即标「未响应」。移后台
#[tauri::command(async)]
pub async fn vacuum_database<R: Runtime + 'static>(app: AppHandle<R>) -> Result<VacuumResult> {
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let state = app.state::<BackendState>();
            let _operation = OPERATION.lock().map_err(|_| {
                BackendError::new("maintenance_unavailable", "运维任务状态暂不可用")
            })?;
            let result = (|| {
                super::super::security::guard::stop_all(&state)?;
                let store = store(&state)?;
                super::vacuum(&app, &store, &MaintenanceService::new(&state.learning))
            })();
            super::super::sync::scheduler::start_scheduler(app.clone());
            result
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}
