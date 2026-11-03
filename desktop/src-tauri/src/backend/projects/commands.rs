use super::super::{error::Result, BackendState};
use super::{IterationNoteInput, ProjectState, ProjectWorkspace, SubmissionInput};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_project_workspace(state: State<'_, BackendState>) -> Result<ProjectWorkspace> {
    super::workspace(&ProjectState::new(&state.learning))
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
