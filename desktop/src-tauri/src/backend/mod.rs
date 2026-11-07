pub mod agent;
pub mod backup;
pub mod commands;
pub mod community;
pub mod engagement;
pub mod error;
pub mod export;
pub mod knowledge;
pub mod learning;
pub mod maintenance;
pub mod materials;
pub mod notes;
pub mod planning;
pub mod plugins;
pub mod practice;
pub mod projects;
pub mod providers;
pub(crate) mod random;
pub mod reports;
pub mod review;
pub mod security;
pub mod sharing;
pub mod store;

/// 导出/写盘目的地的第二道遏制：拒绝指向应用数据目录内部的路径。
/// 正常 UI 经系统对话框选择任意位置不受影响；注入脚本若借导出命令
/// 写盘，也不能覆盖工作区数据库、原件或配置（08#2）。
pub(crate) fn reject_data_root_destination<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    destination: &std::path::Path,
) -> error::Result<()> {
    use tauri::Manager;
    let Some(state) = app.try_state::<crate::startup::StartupState>() else {
        return Ok(());
    };
    let root = state.data_root();
    let Ok(root) = root.canonicalize() else {
        return Ok(());
    };
    // 目标可能尚不存在：规范化其父目录再拼接文件名
    let parent = destination.parent().unwrap_or(destination);
    let candidate = match parent.canonicalize() {
        Ok(parent) => parent.join(destination.file_name().unwrap_or_default()),
        Err(_) => return Ok(()),
    };
    if candidate.starts_with(&root) {
        return Err(error::BackendError::new(
            "unsafe_export_destination",
            "导出目的地不能位于应用数据目录内",
        ));
    }
    Ok(())
}
pub mod sync;
pub mod web;

use std::sync::Mutex;

pub struct BackendState {
    pub store: Mutex<store::Store>,
    pub materials: materials::MaterialState,
    pub providers: providers::ProviderState,
    pub learning: learning::LearningState,
    pub agent: agent::AgentState,
    pub practice: practice::PracticeState,
    pub review: review::ReviewState,
    pub knowledge: knowledge::KnowledgeState,
    pub community: community::CommunityState,
    pub plugins: plugins::PluginState,
    pub sharing: sharing::SharingState,
}
