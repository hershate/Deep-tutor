use super::super::{error::Result, BackendState};
use super::{AnswerResult, GenerateInput, Mistake, PracticeSet, PracticeSetDetail};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn generate_practice<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: GenerateInput,
) -> Result<PracticeSetDetail> {
    super::generate(&app, &state.practice, input)
}

#[tauri::command]
pub fn get_practice_set<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<PracticeSetDetail> {
    // 47#1：考试计时从首次拉卷起算（生成完成不再吃掉考试时长）
    super::start_exam_timer(&state.practice, id)?;
    // 13#24：考试到期不再永久停留 ready——读取详情时惰性收卷
    //（未答按 0 分落定），遗忘交卷的试卷不会悬空成绩与错题
    super::lazy_finalize_expired_exam(&app, &state.practice, id)?;
    super::detail(&state.practice, id)
}

#[tauri::command]
pub fn list_practice_history(state: State<'_, BackendState>) -> Result<Vec<PracticeSet>> {
    super::history(&state.practice)
}

#[tauri::command]
pub fn cancel_practice_generation<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<PracticeSetDetail> {
    super::cancel(&app, &state.practice, id)?;
    super::detail(&state.practice, id)
}

#[tauri::command]
pub fn resume_practice_generation<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<PracticeSetDetail> {
    super::resume(&app, &state.practice, id)
}

#[tauri::command]
pub fn submit_practice_answer<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    set_id: i64,
    question_id: i64,
    answer: Value,
) -> Result<AnswerResult> {
    super::submit(&app, &state.practice, set_id, question_id, answer)
}

#[tauri::command]
pub fn submit_exam<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<PracticeSetDetail> {
    super::exams::finalize(&app, &state.practice, id)
}

#[tauri::command]
pub fn list_practice_mistakes(state: State<'_, BackendState>) -> Result<Vec<Mistake>> {
    super::mistakes(&state.practice)
}

/// 37#5：错题 JSON 改为用户选择位置后由原生后端落盘（替代 WebView Blob 下载）；
/// 61 号：内存版 export_practice_mistakes 命令已删（前端零调用）
#[tauri::command]
pub fn export_practice_mistakes_file<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    destination: String,
) -> Result<Value> {
    let content = super::export_mistakes(&state.practice)?;
    let bytes = serde_json::to_vec_pretty(&content).map_err(|_| {
        super::super::error::BackendError::new("export_failed", "错题内容序列化失败")
    })?;
    super::super::write_export_bytes(&app, &destination, &bytes)
}

#[tauri::command]
pub fn set_practice_bookmark<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    question_id: i64,
    bookmarked: bool,
) -> Result<()> {
    super::bookmark(&app, &state.practice, question_id, bookmarked)
}

#[tauri::command]
pub fn set_practice_mistake_mastered<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    question_id: i64,
    mastered: bool,
) -> Result<()> {
    super::set_mistake_mastered(&app, &state.practice, question_id, mastered)
}

#[tauri::command]
pub fn cancel_practice_answer<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    answer_id: i64,
) -> Result<PracticeSetDetail> {
    super::subjective::cancel(&app, &state.practice, answer_id)
}

#[tauri::command]
pub fn resume_practice_answer<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    answer_id: i64,
) -> Result<PracticeSetDetail> {
    super::subjective::resume(&app, &state.practice, answer_id)
}
