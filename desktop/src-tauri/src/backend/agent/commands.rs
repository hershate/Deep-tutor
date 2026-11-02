use super::super::{error::Result, BackendState};
use super::{AgentRun, AgentSession};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn create_agent_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    title: Option<String>,
    plan_id: Option<String>,
    budget_tokens: Option<u64>,
) -> Result<AgentSession> {
    super::new_session(&app, &state.agent, title, plan_id, budget_tokens)
}

#[tauri::command]
pub fn list_agent_sessions(state: State<'_, BackendState>) -> Result<Vec<AgentSession>> {
    super::list(&state.agent)
}

#[tauri::command]
pub fn get_agent_session(state: State<'_, BackendState>, id: String) -> Result<AgentSession> {
    super::get(&state.agent, &id)
}

#[tauri::command]
pub fn send_agent_message<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    session_id: String,
    content: String,
) -> Result<AgentRun> {
    super::send(&app, &state.agent, &session_id, content)
}

#[tauri::command]
pub fn get_agent_run(state: State<'_, BackendState>, id: String) -> Result<AgentRun> {
    super::get_run(&state.agent, super::numeric_id(&id)?)
}

#[tauri::command]
pub fn cancel_agent_run<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<AgentRun> {
    super::cancel(&app, &state.agent, &id)
}

#[tauri::command]
pub fn resume_agent_run<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<AgentRun> {
    super::resume(&app, &state.agent, &id)
}

#[tauri::command]
pub fn delete_agent_session<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    super::delete_session(&app, &state.agent, &id)
}

#[tauri::command]
pub fn approve_agent_request<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    super::tools::approve(&app, &state.agent, &id)
}

#[tauri::command]
pub fn cancel_agent_approval<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<()> {
    super::tools::cancel_approval(&app, &state.agent, &id)
}
