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

// 90#10：方案列表对每行做完整 result JSON 反序列化（知识图谱+排期，单方案
// 可达数百 KB × 最多 100 行）——列表消费方（导出中心/任务中心）确实读取
// result.stages，摘要化会破坏契约，故移后台消除主线程打开延迟
#[tauri::command(async)]
pub async fn list_learning_plans<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Vec<Plan>> {
    // 23#7：principal 传播（guard 的 thread-local 到不了阻塞线程）
    let principal = super::super::security::session_principal_existing(&app)?;
    let learning = state.learning.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::list(&learning))
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "方案列表读取任务执行异常终止")
    })?
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

#[tauri::command]
pub fn adopt_learning_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<serde_json::Value> {
    super::actions::adopt(&app, &state.learning, &id)
}

#[tauri::command]
pub fn regenerate_learning_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    scope: u8,
) -> Result<Plan> {
    super::actions::regenerate(&app, &state.learning, &id, scope)
}

#[tauri::command]
pub fn get_knowledge_overview(
    state: State<'_, BackendState>,
    id: String,
) -> Result<super::knowledge::KnowledgeOverview> {
    super::knowledge::progress(&state.learning, &id)
}

#[tauri::command]
pub fn get_knowledge_path(
    state: State<'_, BackendState>,
    id: String,
    from: String,
    to: String,
) -> Result<Vec<String>> {
    super::knowledge::shortest_path(&state.learning, &id, &from, &to)
}

#[tauri::command]
pub fn add_knowledge_task<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    node_id: String,
) -> Result<serde_json::Value> {
    super::knowledge::add_task(&app, &state.learning, &id, &node_id)
}

#[tauri::command]
pub fn get_reading_progress(
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
) -> Result<super::knowledge::ReadingProgress> {
    super::knowledge::reading(&state.learning, &id, &stage_id)
}

#[tauri::command]
pub fn set_reading_progress<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
    completed: bool,
    bookmarked: Option<bool>,
) -> Result<super::knowledge::ReadingProgress> {
    super::knowledge::set_reading(&app, &state.learning, &id, &stage_id, completed, bookmarked)
}

#[tauri::command]
pub fn get_lecture(
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
    version: Option<u32>,
) -> Result<super::lecture::Lecture> {
    super::lecture::get(&state.learning, &id, &stage_id, version)
}

#[tauri::command]
pub fn generate_lecture<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
    force: bool,
) -> Result<super::lecture::Lecture> {
    super::lecture::generate(&app, &state.learning, &id, &stage_id, force)
}

#[tauri::command]
pub fn cancel_lecture<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
) -> Result<super::lecture::Lecture> {
    super::lecture::cancel(&app, &state.learning, &id, &stage_id)
}

#[tauri::command]
pub fn save_study_note<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
    content: String,
) -> Result<serde_json::Value> {
    super::knowledge::save_note(&app, &state.learning, &id, &stage_id, content)
}
