mod archive;
pub mod commands;
#[cfg(test)]
mod tests;
mod transfer;

use super::commands::emit;
use super::error::Result;
use super::learning::LearningState;
use super::store::Store;
use serde_json::{json, Value};
use std::path::Path;
use tauri::{AppHandle, Runtime};

pub use archive::{BackupPreview, BackupResult};

pub fn export<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    state: &LearningState,
    destination: &Path,
) -> Result<BackupResult> {
    archive::export(app, store, state, destination)
}

pub fn preview(state: &LearningState, store: &Store, path: &Path) -> Result<BackupPreview> {
    // 13#19：预演跑导入同款 dry_run（回滚事务），ID 冲突与引用断链
    // 提前在预览阶段报出真实原因，而不是“预览可导入、导入最后一步失败”
    let mut backup = archive::open(path, state)?;
    transfer::dry_run(&mut backup, store, state)?;
    backup.preview()
}

pub fn import<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    state: &LearningState,
    path: &Path,
) -> Result<Value> {
    let mut backup = archive::open(path, state)?;
    let counts = transfer::import(&mut backup, store, state)?;
    let mut reindex_warning: Option<String> = None;
    let mut recover_warnings: Vec<String> = Vec::new();
    if let Err(error) = super::sharing::reindex(&super::sharing::SharingState::new(state)) {
        reindex_warning = Some(format!("分享索引重建失败：{}", error.message));
    }
    let files = backup.manifest.files.len();
    let event = Store::event(
        &store.conn,
        "backup.imported",
        json!({"counts": counts, "files": files, "appendOnly": true}),
    )?;
    emit(app, &event);
    // 13#20：数据已在导入事务提交后落盘；后续恢复步骤失败不再把整条
    // import 报为失败（用户按提示重试会造成全量重复追加），改为在
    // 快照状态里附警告，应用重启时的常规 recover 会补跑这些步骤
    for (name, result) in [
        ("learning", super::learning::recover(app, state).map(|_| ())),
        (
            "agent",
            super::agent::recover(
                app,
                &super::agent::AgentState::new(
                    state.database_path(),
                    &state.provider_state(),
                    state,
                ),
            )
            .map(|_| ()),
        ),
        (
            "practice",
            super::practice::recover(
                app,
                &super::practice::PracticeState::new(
                    state.database_path(),
                    &state.provider_state(),
                    state,
                ),
            )
            .map(|_| ()),
        ),
        (
            "projects",
            super::projects::recover(app, &super::projects::ProjectState::new(state))
                .map(|_| ()),
        ),
        (
            "knowledge",
            super::knowledge::recover(
                app,
                &super::knowledge::KnowledgeState::new(
                    state.database_path(),
                    &state.provider_state(),
                    &state.material_state(),
                ),
            )
            .map(|_| ()),
        ),
        (
            "providers",
            super::providers::recover_chats(app, &state.provider_state()).map(|_| ()),
        ),
        (
            "routing",
            super::providers::routing::recover(app, &state.provider_state()).map(|_| ()),
        ),
        (
            "materials",
            super::materials::recover(app, &state.material_state()).map(|_| ()),
        ),
        (
            "sync",
            super::sync::recover(app, &super::sync::SyncService::new(state)).map(|_| ()),
        ),
    ] {
        if let Err(error) = result {
            recover_warnings.push(format!("{name} 恢复未完成：{}", error.message));
        }
    }
    let mut snapshot = store.app_state()?;
    if let Some(warning) = reindex_warning {
        snapshot["backupImportWarnings"] = json!([warning]);
    }
    if !recover_warnings.is_empty() {
        snapshot["backupImportWarnings"] = match snapshot.get("backupImportWarnings") {
            Some(Value::Array(existing)) => {
                let mut combined = existing.clone();
                combined.extend(recover_warnings.iter().cloned().map(Value::String));
                Value::Array(combined)
            }
            _ => json!(recover_warnings),
        };
    }
    Ok(snapshot)
}
