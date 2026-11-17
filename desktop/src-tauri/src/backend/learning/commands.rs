use super::super::{error::Result, BackendState};
use super::{Plan, PlanInput};
use tauri::{AppHandle, Runtime, State};

/// P-059/P-066：学习域的重读命令与采用命令原为同步命令——命令体在主线程
/// 执行：get_learning_plan 反序列化整份 artifacts/result JSON（artifacts
/// 的 step0 快照内含资料解析正文，单份上限 5 MiB、方案最多 8 份），
/// get_lecture 先重解析整份方案再取全部单元正文并逐单元读用量，
/// adopt 按排期条目逐条建任务（条目数随方案总分钟数放大）。
/// 移后台执行（61#3 返工同款 guard::scoped：命令体在阻塞线程上重新取
/// 工作区切换读锁并解析 principal，写入始终落在该用户自己的工作区）
async fn background<R: Runtime + 'static, T: Send + 'static>(
    app: AppHandle<R>,
    task: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || super::super::security::guard::scoped(&app, task))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("internal_error", "学习任务执行异常终止")
        })?
}

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

// P-059：方案读取要反序列化整份 input/artifacts/result JSON（step0 快照
// 内含资料解析正文）——原先同步命令在主线程执行，方案生成期每条 plan.* 事件
// 都全量拉一次，窗口间歇卡顿；移后台
#[tauri::command(async)]
pub async fn get_learning_plan<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<Plan> {
    let learning = state.learning.shared();
    background(app, move || super::get(&learning, &id)).await
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

// P-066：采用方案按排期条目逐条建任务（每条一次 next_free_id 聚合扫描
// + 两条 INSERT），条目数随方案总分钟数与每日时长放大（极端约 3600 条）
// ——原先同步命令在主线程执行，点击「采用此方案」冻结窗口数秒且中途
// 不可取消；移后台
#[tauri::command(async)]
pub async fn adopt_learning_plan<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<serde_json::Value> {
    let learning = state.learning.shared();
    let inner = app.clone();
    background(app, move || super::actions::adopt(&inner, &learning, &id)).await
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

// P-059：讲义读取先重解析整份方案，再取全部单元正文并对每个带
// request_id 的单元单独取一次用量（单元内容上限 12 段 × 20000 字符）
// ——原先同步命令在主线程执行，前端每条 lecture.* 事件都调一次；
// 移后台
#[tauri::command(async)]
pub async fn get_lecture<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    stage_id: String,
    version: Option<u32>,
) -> Result<super::lecture::Lecture> {
    let learning = state.learning.shared();
    background(app, move || {
        super::lecture::get(&learning, &id, &stage_id, version)
    })
    .await
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
