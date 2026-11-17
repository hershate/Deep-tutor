use super::super::{error::Result, BackendState};
use super::{CardInput, GradeResult, ReviewCard, ReviewHistoryEntry, ReviewStats};
use tauri::{AppHandle, Runtime, State};

/// P-069：评分写事务用 BEGIN IMMEDIATE 取写锁——备份导入/VACUUM/重置的
/// 长写事务期间，原先同步命令在主线程上等待 busy_timeout（10 秒），
/// 窗口被系统标记「未响应」；移后台（127#2/141#2 同款，61#3 返工的
/// guard::scoped 保证写入落在该用户自己的工作区）
async fn background<R: Runtime + 'static, T: Send + 'static>(
    app: AppHandle<R>,
    task: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || super::super::security::guard::scoped(&app, task))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("internal_error", "复习任务执行异常终止")
        })?
}

#[tauri::command]
pub fn create_review_card<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: CardInput,
) -> Result<ReviewCard> {
    super::create(&app, &state.review, input)
}

#[tauri::command]
pub fn update_review_card<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    input: CardInput,
) -> Result<ReviewCard> {
    super::update(&app, &state.review, id, input)
}

#[tauri::command]
pub fn delete_review_card<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<()> {
    super::delete(&app, &state.review, id)
}

#[tauri::command]
pub fn set_review_card_suspended<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    suspended: bool,
) -> Result<ReviewCard> {
    super::set_suspended(&app, &state.review, id, suspended)
}

#[tauri::command]
pub fn next_review_card(state: State<'_, BackendState>) -> Result<Option<ReviewCard>> {
    super::next(&state.review)
}

// P-069：评分写事务用 BEGIN IMMEDIATE 取写锁——备份导入/VACUUM/重置的
// 长写事务期间，原先同步命令在主线程上等待 busy_timeout（10 秒），
// 窗口被系统标记「未响应」；移后台
#[tauri::command(async)]
pub async fn grade_review_card<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    grade: String,
) -> Result<GradeResult> {
    let review = state.review.shared();
    let inner = app.clone();
    background(app, move || super::grade(&inner, &review, id, &grade)).await
}

#[tauri::command]
pub fn get_review_stats(state: State<'_, BackendState>) -> Result<ReviewStats> {
    super::stats(&state.review)
}

#[tauri::command]
pub fn list_review_cards(state: State<'_, BackendState>) -> Result<Vec<ReviewCard>> {
    super::list(&state.review)
}

#[tauri::command]
pub fn list_review_history(
    state: State<'_, BackendState>,
    card_id: i64,
) -> Result<Vec<ReviewHistoryEntry>> {
    super::history(&state.review, card_id)
}
