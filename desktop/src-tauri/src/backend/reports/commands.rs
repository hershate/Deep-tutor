use super::super::{error::Result, BackendState};
use super::{
    AdoptionResult, CalendarReport, CheckinInput, FocusOverview, FocusSession, LearningReport,
    ReportDownload, ReportState,
};
use tauri::{AppHandle, Runtime, State};

// 90#8：报表对 focus_sessions / learning_node_evidence / practice_answers
// 全量聚合 + JSON 解析，原先同步命令在主线程执行，历史数据多时打开延迟
// 线性增长；移后台（73#8 范式）
#[tauri::command(async)]
pub async fn get_learning_report(
    state: State<'_, BackendState>,
    period: Option<String>,
) -> Result<LearningReport> {
    let report = ReportState::new(&state.learning);
    tauri::async_runtime::spawn_blocking(move || {
        super::get_report(&report, period.as_deref())
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "报表统计任务执行异常终止")
    })?
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
    period: Option<String>,
) -> Result<AdoptionResult> {
    super::adopt_recommendations(&app, &ReportState::new(&state.learning), ids, period)
}

// 90#8：报表导出叠加全量聚合与 PDF 渲染，随报表命令一并移后台（90#1 同族）
#[tauri::command(async)]
pub async fn export_learning_report<R: Runtime + 'static>(
    app: AppHandle<R>,
    destination: String,
    period: Option<String>,
) -> Result<ReportDownload> {
    use tauri::Manager;
    super::super::reject_data_root_destination(&app, std::path::Path::new(&destination))?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendState>();
        super::export_report(
            &app,
            &ReportState::new(&state.learning),
            std::path::Path::new(&destination),
            period.as_deref(),
        )
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "报表导出任务执行异常终止")
    })?
}
