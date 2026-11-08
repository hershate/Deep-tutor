use super::super::{error::Result, BackendState};
use super::{KnowledgeHit, KnowledgeSession};
use tauri::{AppHandle, Runtime, State};

#[tauri::command(async)]
pub async fn search_knowledge(
    state: State<'_, BackendState>,
    query: String,
) -> Result<Vec<KnowledgeHit>> {
    // 44#2：全库笔记+资料解析文本的内存逐条扫描是击键路径（120ms 防抖），
    // 大库单次可达上百毫秒——原先同步命令在主线程执行，连续击键时窗口
    // 拖动/点击有可感迟滞；移到阻塞线程池
    let knowledge = state.knowledge.shared();
    tauri::async_runtime::spawn_blocking(move || super::search(&knowledge, &query))
        .await
        .map_err(|_| {
            super::super::error::BackendError::new("internal_error", "检索任务执行异常终止")
        })?
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
