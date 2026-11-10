use super::super::{
    error::{BackendError, Result},
    store::Store,
    BackendState,
};
use super::{active_user, initialize, with_user, SecurityState};
use std::sync::RwLock;
use tauri::{
    ipc::{Invoke, InvokeBody},
    AppHandle, Manager, Runtime,
};

static WORKSPACE_SWITCH: RwLock<()> = RwLock::new(());

// 73#1：store owner 镜像——prepare_store 稳态（owner 未变）完全绕开 store
// 互斥锁：备份导入/重置等长事务持锁期间，其余命令不再在主线程排队等锁
// （原先每条 invoke 都锁一次 store，最坏排队至导入结束）。镜像只在换库点
// 写入；不一致时走慢路径锁内复核——镜像过期只损失性能不损语义
static STORE_OWNER: RwLock<Option<i64>> = RwLock::new(None);

fn note_store_owner(owner: Option<i64>) {
    if let Ok(mut slot) = STORE_OWNER.write() {
        *slot = owner;
    }
}

fn reject<R: Runtime>(invoke: Invoke<R>, error: BackendError) -> bool {
    invoke.resolver.reject(error);
    true
}

// 31#7：logout 与 PAT 认证切换复用同一停机集合，保持与 login switch 一致
// 56#2：逐域独立停并汇总错误——原先串行 `?` 使 agent::stop 超时（run_active，
// 工具执行最长 ~12s 网络等待）直接短路，practice/knowledge/learning/
// providers/sync/materials 六域全停，logout 侧 `let _ =` 吞错后旧用户任务
// 全量续跑；focus 域补入停机集合（原先停留 running 按墙钟计时）。
// maintenance 的清理/重置共用同一实现（原先另有一份串行副本）。
// 73#9：八域并行停（scoped threads）——串行最坏为各域 deadline 之和
// （agent/practice/knowledge/learning/providers 各 15s + sync 50s ≈140s），
// 登出/切换/清理在主线程按此冻结；并行后等待上限收敛为最慢单域（sync 50s）
pub(crate) fn stop_all(state: &BackendState) -> Result<()> {
    let mut failed: Vec<&'static str> = Vec::new();
    let domains: [(&'static str, &(dyn Fn() -> Result<()> + Sync)); 8] = [
        ("Agent 任务", &|| super::super::agent::stop(&state.agent)),
        ("练习生成", &|| {
            super::super::practice::stop(&state.practice)
        }),
        ("知识问答", &|| {
            super::super::knowledge::stop(&state.knowledge)
        }),
        ("学习方案", &|| {
            super::super::learning::stop(&state.learning)
        }),
        ("模型请求", &|| {
            super::super::providers::stop_chats(&state.providers)
        }),
        ("同步任务", &|| {
            super::super::sync::stop(&super::super::sync::SyncService::new(&state.learning))
        }),
        ("资料解析", &|| {
            super::super::materials::stop(&state.materials)
        }),
        ("专注会话", &|| {
            super::super::reports::stop(&super::super::reports::ReportState::new(&state.learning))
        }),
    ];
    std::thread::scope(|scope| {
        let handles: Vec<_> = domains
            .iter()
            .map(|(name, stop)| {
                let stop = *stop;
                scope.spawn(move || stop().map_err(|error| (*name, error)))
            })
            .collect();
        for handle in handles {
            match handle.join() {
                Ok(Ok(())) => {}
                Ok(Err((name, error))) => {
                    eprintln!("stop_all: {name} 域停止失败 {}", error.code);
                    failed.push(name);
                }
                Err(_) => {
                    // 73#16 同口径：域内 panic 不再放大为整次停机 abort
                    failed.push("未知域（内部异常）");
                }
            }
        }
    });
    if failed.is_empty() {
        Ok(())
    } else {
        Err(BackendError::new(
            "stop_failed",
            &format!("部分后台任务未能停止（{}），请稍后重试", failed.join("、")),
        ))
    }
}

fn current<R: Runtime>(app: &AppHandle<R>) -> Result<i64> {
    let security = app.state::<SecurityState>();
    Ok(initialize(app, &security)?.0.id)
}

fn prepare_store<R: Runtime>(app: &AppHandle<R>, user_id: i64) -> Result<()> {
    let Some(state) = app.try_state::<BackendState>() else {
        return Ok(());
    };
    // 73#1：快路径——镜像命中即放行，不触碰 store 互斥锁（读锁瞬取瞬放）；
    // 镜像中毒/过期一律走慢路径，语义由锁内复核兜底
    let fast = STORE_OWNER
        .read()
        .map(|slot| *slot == Some(user_id))
        .unwrap_or(false);
    if fast {
        return Ok(());
    }
    let mut store = state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))?;
    if store.owner_id != Some(user_id) {
        *store = Store::at_user(state.learning.database_path().to_path_buf(), user_id)?;
    }
    note_store_owner(store.owner_id);
    Ok(())
}

fn switch<R: Runtime>(invoke: Invoke<R>, handler: &dyn Fn(Invoke<R>) -> bool) -> bool {
    let app = invoke.message.webview().app_handle().clone();
    let target = match invoke.message.payload() {
        InvokeBody::Json(value) => value.get("userId").and_then(|value| value.as_i64()),
        _ => None,
    };
    let Some(target) = target else {
        return reject(
            invoke,
            BackendError::invalid("invalid_user_id", "本地用户标识无效"),
        );
    };
    let security = app.state::<SecurityState>();
    if let Err(error) = security
        .store()
        .and_then(|store| active_user(&store.conn, target))
    {
        return reject(invoke, error);
    }
    let _write = match WORKSPACE_SWITCH.write() {
        Ok(guard) => guard,
        Err(_) => {
            return reject(
                invoke,
                BackendError::new("workspace_unavailable", "工作区切换锁不可用"),
            )
        }
    };
    let previous = match current(&app) {
        Ok(user) => Some(user),
        Err(error) if super::reauthentication_required(&error) => None,
        Err(error) => return reject(invoke, error),
    };
    let resume = |user| {
        if let Some(user) = user {
            with_user(user, || {
                super::super::sync::scheduler::start_scheduler(app.clone())
            });
        }
    };
    let backend = app.try_state::<BackendState>();
    if let (Some(state), Some(previous)) = (&backend, previous) {
        if let Err(error) = with_user(previous, || stop_all(state)) {
            resume(previous.into());
            return reject(invoke, error);
        }
    }
    let mut previous_store = None;
    if let Some(state) = &backend {
        let next = match with_user(target, || {
            Store::new(state.learning.database_path().to_path_buf())
        }) {
            Ok(store) => store,
            Err(error) => {
                resume(previous);
                return reject(invoke, error);
            }
        };
        let mut store = match state.store.lock() {
            Ok(store) => store,
            Err(_) => {
                resume(previous);
                return reject(
                    invoke,
                    BackendError::new("store_unavailable", "本地数据层暂不可用"),
                );
            }
        };
        previous_store = Some(std::mem::replace(&mut *store, next));
        // 73#1：换库后同步 owner 镜像，其余命令恢复无锁快路径
        note_store_owner(Some(target));
    }
    let handled = handler(invoke);
    let actual = current(&app).ok();
    if actual != Some(target) {
        if let (Some(state), Some(previous)) = (&backend, previous_store) {
            // 90#11：回滚路径锁中毒恢复继续用（73#16 语义，与 backend/
            // commands.rs 的 store 锁一致）——静默跳过会让 store 停留在
            // 切换目标的库上，会话已离开但数据面没回滚
            let mut store = state
                .store
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            note_store_owner(previous.owner_id);
            *store = previous;
        }
    }
    resume(actual);
    handled
}

// 31#34：PAT 认证的停机/撤旧签新段持 WORKSPACE_SWITCH 写锁——与 switch
// 对称，多窗口/异步化后不存在旧用户上下文以新用户库执行的交错
pub(super) fn exclusive<T>(operation: impl FnOnce() -> T) -> Result<T> {
    let _write = WORKSPACE_SWITCH
        .write()
        .map_err(|_| BackendError::new("workspace_unavailable", "工作区切换锁不可用"))?;
    Ok(operation())
}

pub fn guard_handler<R, F>(handler: F) -> impl Fn(Invoke<R>) -> bool + Send + Sync + 'static
where
    R: Runtime + 'static,
    F: Fn(Invoke<R>) -> bool + Send + Sync + 'static,
{
    move |invoke| {
        let command = invoke.message.command();
        if matches!(
            command,
            "get_storage_overview"
                | "retry_storage_initialization"
                | "set_storage_location"
                | "reset_storage_location"
                | "restart_application"
        ) {
            return handler(invoke);
        }
        let app = invoke.message.webview().app_handle().clone();
        if let Err(error) = crate::startup::ensure_ready(&app) {
            return reject(invoke, error);
        }
        if command == "login_local_user" {
            return switch(invoke, &handler);
        }
        if matches!(
            command,
            "get_security_overview"
                | "authenticate_access_token"
                | "read_shared_plan"
                | "read_shared_plan_file"
                | "read_shared_plan_package"
        ) {
            let app = invoke.message.webview().app_handle().clone();
            if command == "get_security_overview" {
                if let Ok(user) = current(&app) {
                    return with_user(user, || handler(invoke));
                }
            }
            return handler(invoke);
        }
        let _read = match WORKSPACE_SWITCH.read() {
            Ok(guard) => guard,
            Err(_) => {
                return reject(
                    invoke,
                    BackendError::new("workspace_unavailable", "工作区切换锁不可用"),
                )
            }
        };
        let app = invoke.message.webview().app_handle().clone();
        match current(&app) {
            Ok(user) => with_user(user, || match prepare_store(&app, user) {
                Ok(()) => handler(invoke),
                Err(error) => reject(invoke, error),
            }),
            Err(error) => reject(invoke, error),
        }
    }
}
