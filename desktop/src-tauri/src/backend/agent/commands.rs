use super::super::{
    error::{BackendError, Result},
    BackendState,
};
use super::{AgentRun, AgentSession};
use tauri::{AppHandle, Runtime, State};

/// 61#3：Agent 命令族 9 个原为同步命令在主线程执行——list_agent_sessions
/// 串行 51 次建连查询（每次 Store::new 开连接、切 WAL、PRAGMA、
/// CREATE TABLE 批处理并计算 sqlite_master 指纹），get_agent_session 在
/// 每次事件刷新时重复全套查询（100 条消息 + 10 条运行 + 全部产物 +
/// 审批 + 记忆）；主线程被占，界面掉帧。移后台对齐 learning 域 90#10
/// 与 backend/commands.rs 103#6/127#1 的口径。
/// 61#3 返工：命令体改由 guard::scoped 执行——async 命令体在 tokio
/// worker 上运行，guard 的 WORKSPACE_SWITCH 读锁与 with_user 只覆盖 IPC
/// 派发瞬间（RwLockReadGuard 非 Send，不能随 future 迁出 tokio），账号
/// 切换持写锁可在命令体真正开始前完成，命令体随后解析出的 principal
/// 已是切换后的用户：删除类命令（delete_agent_session 递归删目录）会作用
/// 到另一用户的工作区。scoped 在命令体的阻塞线程上取读锁并于锁内解析
/// principal：命令体运行期与切换互斥，写入始终落在该 principal 自己的
/// 工作区。
/// 23#7：principal 必须显式传播——async 命令体运行在 tokio worker，guard
/// 在 IPC 线程设置的 thread-local 到不了那里；缺 principal 时 Store::new
/// 以「本地会话身份缺失」拒绝打开数据层。scoped 用 session_principal_existing
/// 派生（不自动签发会话）：guard 已保证会话存在，派生出的用户与 guard 的
/// current() 同源；无 guard 的测试环境退回 thread-local，写入仍落在测试
/// 工作区（与迁移前的同步语义一致）。
async fn background<R: Runtime + 'static, T: Send + 'static>(
    app: AppHandle<R>,
    task: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || super::super::security::guard::scoped(&app, task))
        .await
        .map_err(|_| BackendError::new("internal_error", "Agent 任务执行异常终止"))?
}

#[tauri::command(async)]
pub async fn create_agent_session<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    title: Option<String>,
    plan_id: Option<String>,
    budget_tokens: Option<u64>,
) -> Result<AgentSession> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || {
        super::new_session(&inner, &agent, title, plan_id, budget_tokens)
    })
    .await
}

#[tauri::command(async)]
pub async fn list_agent_sessions<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Vec<AgentSession>> {
    let agent = state.agent.shared();
    background(app, move || super::list(&agent)).await
}

#[tauri::command(async)]
pub async fn get_agent_session<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<AgentSession> {
    let agent = state.agent.shared();
    background(app, move || super::get(&agent, &id)).await
}

#[tauri::command(async)]
pub async fn send_agent_message<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    session_id: String,
    content: String,
) -> Result<AgentRun> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || {
        super::send(&inner, &agent, &session_id, content)
    })
    .await
}

#[tauri::command(async)]
pub async fn cancel_agent_run<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<AgentRun> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || super::cancel(&inner, &agent, &id)).await
}

#[tauri::command(async)]
pub async fn resume_agent_run<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<AgentRun> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || super::resume(&inner, &agent, &id)).await
}

#[tauri::command(async)]
pub async fn delete_agent_session<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || super::delete_session(&inner, &agent, &id)).await
}

#[tauri::command(async)]
pub async fn approve_agent_request<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || super::tools::approve(&inner, &agent, &id)).await
}

#[tauri::command(async)]
pub async fn cancel_agent_approval<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    let agent = state.agent.shared();
    let inner = app.clone();
    background(app, move || {
        super::tools::cancel_approval(&inner, &agent, &id)
    })
    .await
}
