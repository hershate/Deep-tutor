use super::super::{
    error::{BackendError, Result},
    BackendState,
};
use super::{
    CommunityOverview, GroupInput, GroupView, PostInput, PostView, ReminderView, ReplyView,
    RoomInput, RoomView,
};
use tauri::{AppHandle, Runtime, State};

// 124#3：社区概览是重读命令（500 帖 + 回复 + 100 组 + 100 房间），原先
// 主线程同步执行——帖子量增长后窗口冻结面线性放大（engagement/sync/
// sharing 均已移后台，此处是最后缺口）；数据为单机本地（作者恒「本机
// 用户」），无 principal 依赖
#[tauri::command(async)]
pub async fn get_community_overview(
    state: State<'_, BackendState>,
    kind: Option<String>,
    query: Option<String>,
) -> Result<CommunityOverview> {
    let community = state.community.shared();
    tauri::async_runtime::spawn_blocking(move || {
        super::overview(&community, kind.as_deref(), query.as_deref())
    })
    .await
    .map_err(|_| BackendError::new("internal_error", "社区概览读取异常终止"))?
}

#[tauri::command]
pub fn create_community_post<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: PostInput,
) -> Result<PostView> {
    super::create_post(&app, &state.community, input)
}

#[tauri::command]
pub fn reply_community_post<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    post_id: i64,
    body: String,
) -> Result<ReplyView> {
    super::reply(&app, &state.community, post_id, body)
}

#[tauri::command]
pub fn accept_community_reply<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    reply_id: i64,
    accepted: bool,
) -> Result<ReplyView> {
    super::accept_reply(&app, &state.community, reply_id, accepted)
}

#[tauri::command]
pub fn toggle_community_reaction<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    post_id: i64,
    kind: String,
) -> Result<PostView> {
    super::toggle_reaction(&app, &state.community, post_id, kind)
}

#[tauri::command]
pub fn create_community_group<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: GroupInput,
) -> Result<GroupView> {
    super::create_group(&app, &state.community, input)
}

#[tauri::command]
pub fn toggle_community_group<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
    joined: bool,
) -> Result<GroupView> {
    super::toggle_group(&app, &state.community, id, joined)
}

#[tauri::command]
pub fn create_community_room<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    input: RoomInput,
) -> Result<RoomView> {
    super::create_room(&app, &state.community, input)
}

#[tauri::command]
pub fn reserve_community_room<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<RoomView> {
    super::reserve(&app, &state.community, id)
}

#[tauri::command]
pub fn cancel_community_reservation<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<RoomView> {
    super::cancel_reservation(&app, &state.community, id)
}

#[tauri::command]
pub fn cancel_community_room<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<RoomView> {
    super::cancel_room(&app, &state.community, id)
}

#[tauri::command]
pub fn claim_community_reminders<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Vec<ReminderView>> {
    super::claim_due_reminders(&app, &state.community)
}
