use super::super::{error::Result, BackendState};
use super::{IterationNoteInput, ProjectState, ProjectWorkspace, SubmissionInput};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

/// P-060：项目域四个写命令原为同步命令——命令体在主线程执行：submit/
/// cancel/resume 的返回值都经 workspace() 重建工作区（≤200 版本逐行
/// feedback_json 反序列化，103#7 认定必须移后台），iteration_note 收尾
/// 还要聚合全量 app_state。移后台执行（61#3 返工同款 guard::scoped：
/// 命令体在阻塞线程上重新取工作区切换读锁并解析 principal，写入始终
/// 落在该用户自己的工作区）
async fn background<R: Runtime + 'static, T: Send + 'static>(
    app: AppHandle<R>,
    task: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || super::super::security::guard::scoped(&app, task))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("internal_error", "项目任务执行异常终止")
        })?
}

// 103#7：≤200 版本逐行 JSON 反序列化，原先同步命令在主线程执行；移后台
#[tauri::command(async)]
pub async fn get_project_workspace<R: Runtime + 'static>(
    app: AppHandle<R>,
) -> Result<ProjectWorkspace> {
    use tauri::Manager;
    let principal = super::super::security::session_principal_existing(&app)?;
    let projects = {
        let state = app.state::<BackendState>();
        ProjectState::new(&state.learning)
    };
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::workspace(&projects))
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "项目工作区加载任务执行异常终止")
    })?
}

// P-060：提交版本后返回的工作区要走 ≤200 版本重建；移后台
#[tauri::command(async)]
pub async fn submit_project_version<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: SubmissionInput,
) -> Result<ProjectWorkspace> {
    let learning = state.learning.shared();
    let inner = app.clone();
    background(app, move || {
        super::submit(&inner, &ProjectState::new(&learning), input)
    })
    .await
}

// P-060：取消评价后返回的工作区要走 ≤200 版本重建；移后台
#[tauri::command(async)]
pub async fn cancel_project_feedback<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ProjectWorkspace> {
    let learning = state.learning.shared();
    let inner = app.clone();
    background(app, move || {
        super::cancel(&inner, &ProjectState::new(&learning), id)
    })
    .await
}

// P-060：恢复评价后返回的工作区要走 ≤200 版本重建；移后台
#[tauri::command(async)]
pub async fn resume_project_feedback<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ProjectWorkspace> {
    let learning = state.learning.shared();
    let inner = app.clone();
    background(app, move || {
        super::resume(&inner, &ProjectState::new(&learning), id)
    })
    .await
}

// P-060：保存迭代笔记后要聚合全量 app_state 快照；移后台
#[tauri::command(async)]
pub async fn save_project_iteration_note<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: IterationNoteInput,
) -> Result<Value> {
    let learning = state.learning.shared();
    let inner = app.clone();
    background(app, move || {
        super::iteration_note(&inner, &ProjectState::new(&learning), input)
    })
    .await
}
