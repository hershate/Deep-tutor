use super::super::{error::Result, BackendState};
use super::{CreatedShare, ShareMetadata, SharedPlanView};
use serde_json::Value;
use tauri::{AppHandle, Runtime, State};

#[tauri::command]
pub fn create_shared_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    plan_id: String,
    expires_in_days: Option<u32>,
) -> Result<CreatedShare> {
    super::create(&app, &state.sharing, &plan_id, expires_in_days)
}

#[tauri::command]
pub fn list_shared_plans(
    state: State<'_, BackendState>,
    plan_id: Option<String>,
) -> Result<Vec<ShareMetadata>> {
    super::list(&state.sharing, plan_id.as_deref())
}

#[tauri::command]
pub fn read_shared_plan(state: State<'_, BackendState>, token: String) -> Result<SharedPlanView> {
    super::read_token(&state.sharing, &token)
}

#[tauri::command]
pub fn revoke_shared_plan<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: i64,
) -> Result<ShareMetadata> {
    super::revoke(&app, &state.sharing, id)
}

#[tauri::command]
pub fn read_shared_plan_package(
    state: State<'_, BackendState>,
    package: String,
) -> Result<SharedPlanView> {
    super::read_package(&state.sharing, &package)
}

#[tauri::command]
pub fn export_shared_plan_package(
    state: State<'_, BackendState>,
    package: String,
    destination: String,
) -> Result<Value> {
    super::export_package(&state.sharing, &package, std::path::Path::new(&destination))
}

#[tauri::command]
pub fn read_shared_plan_file(
    state: State<'_, BackendState>,
    path: String,
) -> Result<SharedPlanView> {
    super::read_package_file(&state.sharing, std::path::Path::new(&path))
}
