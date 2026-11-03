use super::super::{error::Result, BackendState};
use super::{KnowledgeHit, KnowledgeSession};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn search_knowledge(
    state: State<'_, BackendState>,
    query: String,
) -> Result<Vec<KnowledgeHit>> {
    super::search(&state.knowledge, &query)
}

#[tauri::command]
pub fn ask_knowledge<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    question: String,
    material_ids: Vec<i64>,
) -> Result<KnowledgeSession> {
    super::ask(&app, &state.knowledge, question, material_ids)
}

#[tauri::command]
pub fn list_knowledge_sessions(state: State<'_, BackendState>) -> Result<Vec<KnowledgeSession>> {
    super::list(&state.knowledge)
}

#[tauri::command]
pub fn get_knowledge_session(state: State<'_, BackendState>, id: i64) -> Result<KnowledgeSession> {
    super::get(&state.knowledge, id)
}

#[tauri::command]
pub fn cancel_knowledge_answer<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<KnowledgeSession> {
    super::cancel(&app, &state.knowledge, id)
}

#[tauri::command]
pub fn resume_knowledge_answer<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<KnowledgeSession> {
    super::resume(&app, &state.knowledge, id)
}

#[tauri::command]
pub fn delete_knowledge_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<()> {
    super::delete(&app, &state.knowledge, id)
}

#[tauri::command]
pub fn clear_knowledge_history<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<()> {
    super::clear_history(&app, &state.knowledge)
}
