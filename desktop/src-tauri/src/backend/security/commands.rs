use super::super::error::Result;
use super::{CreatedToken, LocalUser, SecurityOverview, SecurityState};
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn get_security_overview<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
) -> Result<SecurityOverview> {
    super::overview(&app, &state)
}

#[tauri::command]
pub fn login_local_user<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    user_id: i64,
) -> Result<SecurityOverview> {
    super::login(&app, &state, user_id)
}

#[tauri::command]
pub fn logout_local_user<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
) -> Result<()> {
    super::logout(&app, &state)
}

#[tauri::command]
pub fn create_local_user<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    name: String,
    role: String,
) -> Result<SecurityOverview> {
    super::create_user(&app, &state, name, role)
}

#[tauri::command]
pub fn set_local_user_active<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    user_id: i64,
    active: bool,
) -> Result<SecurityOverview> {
    super::set_active(&app, &state, user_id, active)
}

#[tauri::command]
pub fn remove_local_user<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    user_id: i64,
) -> Result<SecurityOverview> {
    super::remove_user(&app, &state, user_id)
}

#[tauri::command]
pub fn create_access_token<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    name: String,
) -> Result<CreatedToken> {
    super::create_token(&app, &state, name)
}

#[tauri::command]
pub fn revoke_access_token<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    token_id: i64,
) -> Result<SecurityOverview> {
    super::revoke_token(&app, &state, token_id)
}

#[tauri::command]
pub fn authenticate_access_token<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    token: String,
) -> Result<LocalUser> {
    super::authenticate_token(&app, &state, token)
}

#[tauri::command]
pub fn set_security_quota<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, SecurityState>,
    user_id: i64,
    resource: String,
    limit: u64,
) -> Result<SecurityOverview> {
    super::set_quota(&app, &state, user_id, resource, limit)
}
