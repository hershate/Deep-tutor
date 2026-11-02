use super::super::{error::Result, BackendState};
use super::{CardInput, GradeResult, ReviewCard, ReviewHistoryEntry, ReviewStats};
use tauri::{AppHandle, Runtime, State};

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

#[tauri::command]
pub fn grade_review_card<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    grade: String,
) -> Result<GradeResult> {
    super::grade(&app, &state.review, id, &grade)
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
