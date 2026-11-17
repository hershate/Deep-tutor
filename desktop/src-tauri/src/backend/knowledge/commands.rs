use super::super::{error::Result, BackendState};
use super::{KnowledgeHit, KnowledgeSession};
use tauri::{AppHandle, Runtime, State};

#[tauri::command(async)]
pub async fn search_knowledge<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    query: String,
) -> Result<Vec<KnowledgeHit>> {
    // 44#2：全库笔记+资料解析文本的内存逐条扫描是击键路径（120ms 防抖），
    // 大库单次可达上百毫秒——原先同步命令在主线程执行，连续击键时窗口
    // 拖动/点击有可感迟滞；移到阻塞线程池
    // 114#1：移后台必须带 principal（23#7 同因）——search 首行即 Store::new，
    // 生产分支无 principal 直接拒绝，检索整体失败
    let principal = super::super::security::session_principal_existing(&app)?;
    let knowledge = state.knowledge.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::search(&knowledge, &query))
    })
    .await
    .map_err(|_| super::super::error::BackendError::new("internal_error", "检索任务执行异常终止"))?
}

// P-065：ask/resume 原为同步命令——命令体在主线程先 load_scope，对每份
// 选中资料调用 materials::detail（各自 Store::new 新开 SQLite 连接）读出
// 全文解析文本，单份上限 5 MiB、8 份最坏约 40 MiB；提问与恢复问答期间
// 窗口拖动/点击无响应。整段移入阻塞线程池（44#2 search、53#3 list 同口径）
#[tauri::command(async)]
pub async fn ask_knowledge<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    question: String,
    material_ids: Vec<i64>,
) -> Result<KnowledgeSession> {
    // 26 号决策：移后台必须带 principal（23#7 同因）——ask 首段即
    // load_scope→materials::detail→Store::new，生产分支无 principal 直接拒绝
    let principal = super::super::security::session_principal_existing(&app)?;
    let knowledge = state.knowledge.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || {
            super::ask(&app, &knowledge, question, material_ids)
        })
    })
    .await
    .map_err(|_| super::super::error::BackendError::new("internal_error", "提问任务执行异常终止"))?
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

// P-065：resume 走 session_scope→load_scope 同一 1+N 开库路径，与 ask 同理
// 移出主线程；恢复后的问答仍在 wiki-answer-{id} 后台线程执行模型请求
#[tauri::command(async)]
pub async fn resume_knowledge_answer<R: Runtime + 'static>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<KnowledgeSession> {
    let principal = super::super::security::session_principal_existing(&app)?;
    let knowledge = state.knowledge.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::super::security::with_principal(principal, || super::resume(&app, &knowledge, id))
    })
    .await
    .map_err(|_| {
        super::super::error::BackendError::new("internal_error", "恢复问答任务执行异常终止")
    })?
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
