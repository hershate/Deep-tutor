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
pub mod reveal;
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
        .unwrap_or_else(|_| lexical_absolute(&root));
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
    // P-056：两侧形态可能不同——canonicalize 产出 `\\?\C:\…` verbatim 前缀
    // （Prefix::VerbatimDisk），词法绝对化产出 `C:\…`（Prefix::Disk），按组件
    // 精确比较恒不相等：父目录不存在时目的地回退词法形态，遏制被绕过，
    // 数据根内可新建子目录并写盘。归一后逐组件比较
    if path_within(&candidate, &root) || path_within(&candidate, &default_root) {
        return Err(error::BackendError::new(
            "unsafe_export_destination",
            "导出目的地不能位于应用数据目录内",
        ));
    }
    Ok(())
}

/// P-056：路径归属比较——两侧归一到同一形态后逐组件比较，语义等价于
/// `Path::starts_with`，但不受 canonicalize（verbatim 前缀）与词法绝对化
/// （普通前缀）形态差异影响。Windows 路径本身不区分大小写，比较时忽略
/// 大小写（`Path::starts_with` 按字节比较，大小写差异同样会漏判）。
pub(crate) fn path_within(candidate: &std::path::Path, root: &std::path::Path) -> bool {
    let candidate = path_components(candidate);
    let root = path_components(root);
    candidate.len() >= root.len() && candidate[..root.len()] == root[..]
}

/// 归一后的路径组件：剥掉 Windows verbatim 前缀（`\\?\C:\…` 与
/// `\\?\UNC\…`）、统一分隔符，Windows 下再统一小写；空段与 `.` 段丢弃，
/// 组件边界与 `Path::components` 一致。
fn path_components(path: &std::path::Path) -> Vec<String> {
    let text = path.to_string_lossy();
    let text = if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        rest.to_string()
    } else {
        text.to_string()
    };
    let text = text.replace('\\', "/");
    let text = if cfg!(windows) {
        text.to_lowercase()
    } else {
        text
    };
    text.split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .map(str::to_string)
        .collect()
}

/// 词法绝对化：清除 `.`/`..` 组件但不解析符号链接，用于路径尚不存在、
/// canonicalize 不可用时的守卫比较（A-12）。
pub(crate) fn lexical_absolute(path: &std::path::Path) -> std::path::PathBuf {
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
                // P-056：原先无条件 pop 会连根/盘符一起弹出（「C:\..\x」退化为
                // 盘符相对路径「C:x」），比较与后续落盘都落到意外位置——
                // 父目录不存在时回退的就是本函数的词法形态，必须保持绝对
                let text = result.to_string_lossy();
                if !(text.ends_with('/') || text.ends_with('\\')) {
                    result.pop();
                }
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
    // 72#2：落盘成功即登记导出回执——reveal_path 仅放行数据根与本表内路径
    reveal::register(path);
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
