use super::super::{
    error::{BackendError, Result},
    security::{session_principal, with_principal},
    BackendState,
};
use super::{BackupPreview, BackupResult};
use serde_json::Value;
use tauri::{AppHandle, Manager, Runtime, State};

fn blocking_panic() -> BackendError {
    BackendError::new("internal_error", "备份任务执行异常终止")
}

// 44#1：备份三命令的函数体是重 IO（解包整库/全量写入/预演等同导入量级），
// 非 async 命令在主线程执行——大库期间窗口不可拖动、后续 invoke（含取消）
// 排队，超过 ~5 秒被 Windows 标记「未响应」。改投阻塞线程池执行；
// 23#7：阻塞线程看不到 IPC 线程的 thread-local principal，先派生再传播
// （export 依赖 principal_id 注入当前用户行，丢失会静默少备份 local_users）。
#[tauri::command(async)]
pub async fn export_full_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<BackupResult> {
    let learning = state.learning.shared();
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let backend = app.state::<BackendState>();
            // 124#1：sync::stop 首行即 join 掉调度线程；其后任何 `?` 早退
            //（sync 超时/数据根拒绝/导出失败）原先直接返回，start_scheduler
            // 永不执行——auto_sync 静默停摆到偶然重启点。改 defer 型出口：
            // 停机后所有出口统一先重启调度器再透传结果（未启用时调度循环
            // 首行配置检查即空转，无副作用）
            let outcome = (|| -> Result<BackupResult> {
                super::super::sync::stop(&super::super::sync::SyncService::new(&learning))?;
                super::super::reject_data_root_destination(
                    &app,
                    std::path::Path::new(&destination),
                )?;
                let result = {
                    let store = backend
                        .store
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    super::export(&app, &store, &learning, std::path::Path::new(&destination))
                };
                if result.is_ok() {
                    // 72#2：导出回执登记（reveal_path 白名单）
                    super::super::reveal::register(std::path::Path::new(&destination));
                }
                result
            })();
            super::super::sync::scheduler::start_scheduler(app.clone());
            outcome
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}

#[tauri::command(async)]
pub async fn preview_full_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<BackupPreview> {
    let learning = state.learning.shared();
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let backend = app.state::<BackendState>();
            let store = backend
                .store
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            super::preview(&learning, &store, std::path::Path::new(&path))
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}

#[tauri::command(async)]
pub async fn import_full_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    path: String,
) -> Result<Value> {
    let learning = state.learning.shared();
    let principal = session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        with_principal(principal, || {
            let backend = app.state::<BackendState>();
            // 56#2：与登出/切换/清理共用逐域独立停并汇总错误的停机集合
            //（原先此处另串行停六域，agent 超时会短路且不停 focus）。
            // 124#1+135#1：defer 型出口。失败（含 stop_all 自身失败早退）时
            // stop_all 的八域停机必须复位——materials accepting 闸门否则停摆
            // 到进程重启（107#1 同型漏网）；统一走 resume_background（recover
            // 家族 + 调度器重启，与 switch/PAT/维护/重置同口径、幂等可重入）。
            // 成功路径由 super::import 内的逐域恢复清单负责，仅重启调度器
            let outcome = (|| -> Result<Value> {
                super::super::security::guard::stop_all(&backend)?;
                let result = {
                    let store = backend
                        .store
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    super::import(&app, &store, &learning, std::path::Path::new(&path))
                };
                result
            })();
            if outcome.is_err() {
                super::super::security::guard::resume_background(&app);
            } else {
                super::super::sync::scheduler::start_scheduler(app.clone());
            }
            outcome
        })
    })
    .await
    .map_err(|_| blocking_panic())?
}
