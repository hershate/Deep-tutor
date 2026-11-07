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

fn reject<R: Runtime>(invoke: Invoke<R>, error: BackendError) -> bool {
    invoke.resolver.reject(error);
    true
}

// 31#7：logout 与 PAT 认证切换复用同一停机集合，保持与 login switch 一致
pub(super) fn stop_all(state: &BackendState) -> Result<()> {
    super::super::agent::stop(&state.agent)?;
    super::super::practice::stop(&state.practice)?;
    super::super::knowledge::stop(&state.knowledge)?;
    super::super::learning::stop(&state.learning)?;
    super::super::providers::stop_chats(&state.providers)?;
    super::super::sync::stop(&super::super::sync::SyncService::new(&state.learning))?;
    super::super::materials::stop(&state.materials)
}

fn current<R: Runtime>(app: &AppHandle<R>) -> Result<i64> {
    let security = app.state::<SecurityState>();
    Ok(initialize(app, &security)?.0.id)
}

fn prepare_store<R: Runtime>(app: &AppHandle<R>, user_id: i64) -> Result<()> {
    let Some(state) = app.try_state::<BackendState>() else {
        return Ok(());
    };
    let mut store = state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))?;
    if store.owner_id != Some(user_id) {
        *store = Store::at_user(state.learning.database_path().to_path_buf(), user_id)?;
    }
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
    }
    let handled = handler(invoke);
    let actual = current(&app).ok();
    if actual != Some(target) {
        if let (Some(state), Some(previous)) = (&backend, previous_store) {
            if let Ok(mut store) = state.store.lock() {
                *store = previous;
            }
        }
    }
    resume(actual);
    handled
}

// 31#34：PAT 认证的停机/撤旧签新段持 WORKSPACE_SWITCH 写锁——与 switch
// 对称，多窗口/异步化后不存在旧用户上下文以新用户库执行的交错
pub(super) fn exclusive<T>(operation: impl FnOnce() -> T) -> Result<T> {
    let _write = WORKSPACE_SWITCH.write().map_err(|_| {
        BackendError::new("workspace_unavailable", "工作区切换锁不可用")
    })?;
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
