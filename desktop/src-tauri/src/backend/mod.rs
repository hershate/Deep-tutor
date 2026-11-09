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
    // A-12：root 规范化失败回退词法形态而非放行——三处 fail-open 使
    // 「数据根内新建子目录」可穿透（先 create_dir_all 再写入，父目录
    // canonicalize 时已存在），守卫语义与声明不符
    let root = state.data_root();
    let root = root
        .canonicalize()
        .unwrap_or_else(|_| lexical_absolute(root));
    // 65#5：迁移数据目录后，旧默认目录仍存有历史库与原件——导出目的地
    // 选在旧默认目录内同样拒绝，防止覆盖历史数据
    let default_root = state.default_data_root();
    let default_root = default_root
        .canonicalize()
        .unwrap_or_else(|_| lexical_absolute(default_root));
    // 目标可能尚不存在：优先规范化已存在的父目录；不存在时词法绝对化
    let candidate = match destination
        .parent()
        .and_then(|parent| parent.canonicalize().ok())
    {
        Some(parent) => parent.join(destination.file_name().unwrap_or_default()),
        None => lexical_absolute(destination),
    };
    if candidate.starts_with(&root) || candidate.starts_with(&default_root) {
        return Err(error::BackendError::new(
            "unsafe_export_destination",
            "导出目的地不能位于应用数据目录内",
        ));
    }
    Ok(())
}

/// 词法绝对化：清除 `.`/`..` 组件但不解析符号链接，用于路径尚不存在、
/// canonicalize 不可用时的守卫比较（A-12）。
fn lexical_absolute(path: &std::path::Path) -> std::path::PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    };
    let mut result = std::path::PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::ParentDir => {
                result.pop();
            }
            std::path::Component::CurDir => {}
            other => result.push(other.as_os_str()),
        }
    }
    result
}

/// 37#5：四处原先由前端 WebView Blob 静默下载的内容（错题 JSON、脱敏
/// 供应商配置、用量 CSV、开发者资产）统一改为原生保存对话框——由用户
/// 选择位置后经此通道落盘。沿用 reject_data_root_destination 二道遏制
/// 与 materials 的原子写，返回与导出域一致的 bytes/sha256 回执。
pub(crate) fn write_export_bytes<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    destination: &str,
    bytes: &[u8],
) -> error::Result<serde_json::Value> {
    use sha2::{Digest, Sha256};
    let path = std::path::Path::new(destination);
    reject_data_root_destination(app, path)?;
    materials::atomic_export(path, bytes)?;
    Ok(serde_json::json!({
        "bytes": bytes.len(),
        "sha256": format!("{:x}", Sha256::digest(bytes)),
    }))
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
