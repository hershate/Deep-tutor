use super::super::{error::Result, BackendState};
use super::{Plan, PlanInput};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn start_learning_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: PlanInput,
) -> Result<Plan> {
    super::start(&app, &state.learning, input)
}

#[tauri::command]
pub fn list_learning_plans(state: State<'_, BackendState>) -> Result<Vec<Plan>> {
    super::list(&state.learning)
}

#[tauri::command]
pub fn get_learning_plan(state: State<'_, BackendState>, id: String) -> Result<Plan> {
    super::get(&state.learning, &id)
}

#[tauri::command]
pub fn cancel_learning_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<Plan> {
    super::cancel(&app, &state.learning, &id)
}

#[tauri::command]
pub fn resume_learning_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<Plan> {
    super::resume(&app, &state.learning, &id)
}
