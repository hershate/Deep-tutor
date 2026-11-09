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
    // 73#16：锁中毒恢复而非永久拒绝——事务 unwind 时 Drop 回滚，连接一致可复用
    Ok(state
        .store
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()))
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
                // 73#15：备份导入等长事务持 store 锁时先探测占用立即拒绝——
                // 原先要等满 busy_timeout（10s）才报 database_locked；
                // 探测在停机之前，未停任何任务直接返回
                if state.store.try_lock().is_err() {
                    return Err(BackendError::new(
                        "maintenance_busy",
                        "数据层正被长任务占用（如备份导入），请稍后再试",
                    ));
                }
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
                // 73#15：备份导入等长事务持 store 锁时先探测占用立即拒绝——
                // 原先要等满 busy_timeout（10s）才报 database_locked；
                // 探测在停机之前，未停任何任务直接返回
                if state.store.try_lock().is_err() {
                    return Err(BackendError::new(
                        "maintenance_busy",
                        "数据层正被长任务占用（如备份导入），请稍后再试",
                    ));
                }
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
