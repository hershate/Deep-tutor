use super::super::{error::Result, BackendState};
use super::{IterationNoteInput, ProjectState, ProjectWorkspace, SubmissionInput};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

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

#[tauri::command]
pub fn submit_project_version<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: SubmissionInput,
) -> Result<ProjectWorkspace> {
    super::submit(&app, &ProjectState::new(&state.learning), input)
}

#[tauri::command]
pub fn cancel_project_feedback<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ProjectWorkspace> {
    super::cancel(&app, &ProjectState::new(&state.learning), id)
}

#[tauri::command]
pub fn resume_project_feedback<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ProjectWorkspace> {
    super::resume(&app, &ProjectState::new(&state.learning), id)
}

#[tauri::command]
pub fn save_project_iteration_note<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: IterationNoteInput,
) -> Result<Value> {
    super::iteration_note(&app, &ProjectState::new(&state.learning), input)
}
