use super::super::{error::Result, BackendState};
use super::{
    AdoptionResult, CalendarReport, CheckinInput, FocusOverview, FocusSession, LearningReport,
    ReportDownload, ReportState,
};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_learning_report(
    state: State<'_, BackendState>,
    period: Option<String>,
) -> Result<LearningReport> {
    super::get_report(&ReportState::new(&state.learning), period.as_deref())
}

#[tauri::command]
pub fn get_learning_calendar(state: State<'_, BackendState>) -> Result<CalendarReport> {
    super::get_calendar(&ReportState::new(&state.learning))
}

#[tauri::command]
pub fn get_focus_overview(state: State<'_, BackendState>) -> Result<FocusOverview> {
    super::get_focus(&ReportState::new(&state.learning))
}

#[tauri::command]
pub fn start_focus_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    task_id: Option<i64>,
    planned_seconds: u64,
) -> Result<FocusSession> {
    super::start_focus(
        &app,
        &ReportState::new(&state.learning),
        task_id,
        planned_seconds,
    )
}

#[tauri::command]
pub fn complete_focus_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<FocusSession> {
    super::complete_focus(&app, &ReportState::new(&state.learning))
}

#[tauri::command]
pub fn pause_focus_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<FocusSession> {
    super::pause_focus(&app, &ReportState::new(&state.learning))
}

#[tauri::command]
pub fn resume_focus_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<FocusSession> {
    super::resume_focus(&app, &ReportState::new(&state.learning))
}

#[tauri::command]
pub fn cancel_focus_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<FocusSession> {
    super::cancel_focus(&app, &ReportState::new(&state.learning))
}

#[tauri::command]
pub fn save_study_checkin<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: CheckinInput,
) -> Result<CalendarReport> {
    super::save_checkin(&app, &ReportState::new(&state.learning), input)
}

#[tauri::command]
pub fn adopt_report_recommendations<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    ids: Vec<String>,
) -> Result<AdoptionResult> {
    super::adopt_recommendations(&app, &ReportState::new(&state.learning), ids)
}

#[tauri::command]
pub fn export_learning_report<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
    period: Option<String>,
) -> Result<ReportDownload> {
    super::export_report(
        &app,
        &ReportState::new(&state.learning),
        std::path::Path::new(&destination),
        period.as_deref(),
    )
}
