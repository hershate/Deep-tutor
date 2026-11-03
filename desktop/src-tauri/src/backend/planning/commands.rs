use super::super::{error::Result, BackendState};
use super::{GoalInput, PlanAlternative, PlanOverview, PlanningState};
use crate::backend::learning::Plan;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_plan_overview(
    state: State<'_, BackendState>,
    week_start: Option<String>,
) -> Result<PlanOverview> {
    super::get_overview(&PlanningState::new(&state.learning), week_start)
}

#[tauri::command]
pub fn get_plan_alternatives(
    state: State<'_, BackendState>,
    week_start: Option<String>,
) -> Result<Vec<PlanAlternative>> {
    super::alternatives(&PlanningState::new(&state.learning), week_start)
}

#[tauri::command]
pub fn apply_plan_intensity<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    intensity: String,
    week_start: Option<String>,
) -> Result<PlanOverview> {
    super::apply_intensity(
        &app,
        &PlanningState::new(&state.learning),
        intensity,
        week_start,
    )
}

#[tauri::command]
pub fn update_learning_goal<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: GoalInput,
) -> Result<Plan> {
    super::update_goal(&app, &PlanningState::new(&state.learning), input)
}

#[tauri::command]
pub fn assign_task_date<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    task_id: i64,
    date_value: String,
) -> Result<PlanOverview> {
    super::assign_task_date(
        &app,
        &PlanningState::new(&state.learning),
        task_id,
        date_value,
    )
}
