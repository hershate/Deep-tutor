pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::LearningState;
use super::store::Store;
use rusqlite::params;
use serde::Serialize;
use serde_json::json;
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Runtime};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DatabaseHealth {
    pub integrity: String,
    pub foreign_key_violations: u64,
    pub journal_mode: String,
    pub schema_version: u32,
    pub page_count: u64,
    pub free_pages: u64,
    pub page_size: u64,
    pub bytes: u64,
    pub wal_bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileItem {
    pub path: String,
    pub bytes: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrphanReport {
    pub originals: Vec<FileItem>,
    pub scratch: Vec<FileItem>,
    pub plugins: Vec<FileItem>,
    pub tempdirs: Vec<FileItem>,
    /// 65#3：sync 目录的存量 json（超出最近 10 份）与崩溃遗留的
    /// .download-/.previous- 临时件
    pub sync: Vec<FileItem>,
    pub missing_originals: Vec<String>,
    pub issues: Vec<BackendError>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaintenanceReport {
    pub database: DatabaseHealth,
    pub orphans: OrphanReport,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupResult {
    pub deleted_files: u64,
    pub deleted_temp_items: u64,
    pub freed_bytes: u64,
    pub failures: Vec<BackendError>,
    /// B-12：未处理剩余项（成功+失败之外未轮到的孤儿），>0 时提示再清一次
    pub remaining: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VacuumResult {
    pub before_bytes: u64,
    pub after_bytes: u64,
    pub wal_busy: i64,
    pub wal_log: i64,
    pub wal_checkpointed: i64,
    pub journal_mode: String,
}

pub struct MaintenanceService {
    learning: LearningState,
}

impl MaintenanceService {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            learning: learning.shared(),
        }
    }

    fn store(&self) -> Result<Store> {
        self.learning.store()
    }

    fn root(&self) -> PathBuf {
        self.learning
            .workspace_path()
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf()
    }
}

fn health(service: &MaintenanceService) -> Result<DatabaseHealth> {
    let store = service.store()?;
    let integrity: String = store
        .conn
        .query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    if integrity != "ok" {
        return Err(BackendError::new(
            "integrity_failed",
            "SQLite 完整性检查未通过",
        ));
    }
    let foreign_key_violations: u64 =
        store
            .conn
            .query_row("SELECT COUNT(*) FROM pragma_foreign_key_check", [], |row| {
                row.get::<_, i64>(0)
            })? as u64;
    if foreign_key_violations > 0 {
        return Err(BackendError::new(
            "foreign_key_violation",
            &format!("数据库存在 {foreign_key_violations} 条失效外键，未执行清理"),
        ));
    }
    let path = service.learning.workspace_path();
    Ok(DatabaseHealth {
        integrity,
        foreign_key_violations,
        journal_mode: store
            .conn
            .query_row("PRAGMA journal_mode", [], |row| row.get::<_, String>(0))?
            .to_lowercase(),
        schema_version: store
            .conn
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))?
            as u32,
        page_count: store
            .conn
            .query_row("PRAGMA page_count", [], |row| row.get::<_, i64>(0))?
            as u64,
        free_pages: store
            .conn
            .query_row("PRAGMA freelist_count", [], |row| row.get::<_, i64>(0))?
            as u64,
        page_size: store
            .conn
            .query_row("PRAGMA page_size", [], |row| row.get::<_, i64>(0))?
            as u64,
        bytes: fs::metadata(&path)?.len(),
        wal_bytes: fs::metadata(path.with_extension("db-wal"))
            .map(|metadata| metadata.len())
            .unwrap_or(0),
    })
}

fn file_item(root: &Path, path: &Path) -> Result<FileItem> {
    Ok(FileItem {
        path: path
            .strip_prefix(root)
            .map_err(|_| BackendError::new("unsafe_path", "运维路径超出应用目录"))?
            .to_string_lossy()
            .replace('\\', "/"),
        bytes: fs::metadata(path)?.len(),
    })
}

fn walk(
    directory: &Path,
    depth: usize,
    files: &mut Vec<PathBuf>,
    issues: &mut Vec<BackendError>,
) -> Result<()> {
    if fs::symlink_metadata(directory)?.file_type().is_symlink() {
        issues.push(BackendError::new(
            "unsafe_path",
            "运维目录包含符号链接，已跳过",
        ));
        return Ok(());
    }
    if depth > 6 {
        issues.push(BackendError::new("unsafe_path", "运维目录嵌套过深，已跳过"));
        return Ok(());
    }
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() {
            issues.push(BackendError::new(
                "unsafe_path",
                "运维目录包含符号链接，已跳过",
            ));
            continue;
        }
        if metadata.is_dir() {
            walk(&path, depth + 1, files, issues)?;
        } else if metadata.is_file() {
            files.push(path);
        }
    }
    Ok(())
}

/// 进行中任务临时文件的宽限期：导入临时件（*.upload-*)、Agent 原子写残留
/// （.*.tmp / .*.previous）、Anki 导出库（anki-*-{pid}-{n}.db）与备份工作目录
/// （backup-work/{pid}-{n}）在宽限期内可能正被写入，不报告也不清理；
/// 超过宽限期（任务已崩溃遗留）才作为孤儿处理。
const IN_FLIGHT_GRACE: std::time::Duration = std::time::Duration::from_secs(24 * 60 * 60);

/// 14#20：单次清理上限 500 项，避免孤儿量大时长时间占库锁；剩余项下次
/// 清理继续。B-12：配额只按成功删除计——失败项（symlink/占用/拒绝）
/// 不占批次，否则 ≥500 个持续失败项会占满配额，其后可删除项永远轮不到，
/// 清理功能对该工作区失效
const CLEANUP_BATCH: usize = 500;

// 31#3：测试注入的宽限覆盖——Some(d) 时以 d 判定，用于把刚写入的临时件
// 立即移出宽限，避免测试操纵文件 mtime（std 无稳定写 mtime API）
#[cfg(test)]
static GRACE_OVERRIDE: std::sync::Mutex<Option<std::time::Duration>> = std::sync::Mutex::new(None);

fn in_flight_grace() -> std::time::Duration {
    #[cfg(test)]
    {
        GRACE_OVERRIDE
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .unwrap_or(IN_FLIGHT_GRACE)
    }
    #[cfg(not(test))]
    {
        IN_FLIGHT_GRACE
    }
}

fn modified_recently(path: &Path) -> bool {
    match fs::metadata(path).and_then(|metadata| metadata.modified()) {
        Ok(modified) => match modified.elapsed() {
            Ok(age) => age < in_flight_grace(),
            Err(_) => true, // mtime 在未来（时钟偏差）：保守视为进行中
        },
        Err(_) => false,
    }
}

fn walk_files(directory: &Path, issues: &mut Vec<BackendError>) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    if directory.exists() {
        walk(directory, 0, &mut files, issues)?;
    }
    Ok(files)
}

fn scan_orphans(service: &MaintenanceService) -> Result<OrphanReport> {
    let root = service.root();
    let store = service.store()?;
    let mut issues = Vec::new();
    let mut expected_originals = BTreeSet::new();
    let mut missing_originals = Vec::new();
    let mut statement = store
        .conn
        .prepare("SELECT sha256, kind FROM materials WHERE sha256 IS NOT NULL")?;
    let materials = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (sha256, kind) in materials {
        if sha256.len() != 64
            || !sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
            || kind.is_empty()
            || kind.len() > 12
            || !kind.bytes().all(|byte| byte.is_ascii_alphanumeric())
        {
            issues.push(BackendError::new(
                "invalid_material_path",
                "资料原件路径不符合应用约定",
            ));
            continue;
        }
        let relative = format!(
            "materials/originals/{}.{}",
            sha256,
            kind.to_ascii_lowercase()
        );
        expected_originals.insert(relative.clone());
        if !root.join(&relative).is_file() {
            missing_originals.push(relative);
        }
    }
    let mut originals = Vec::new();
    for path in walk_files(&root.join("materials/originals"), &mut issues)? {
        let item = file_item(&root, &path)?;
        if !expected_originals.contains(&item.path) {
            let name = path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or_default();
            // 65#7：补写原件的 .restore-{pid}-{n} 临时件与 .upload- 同为
            // 进行中写入，共享宽限——原先只匹配后者，前者被误报为孤儿
            if (name.contains(".upload-") || name.contains(".restore-")) && modified_recently(&path)
            {
                continue;
            }
            originals.push(item);
        }
    }
    let mut expected_scratch = BTreeSet::new();
    // 65#1：过期产物（expires_at 已过）不再视为「期望存在」——其文件在
    // 扫描报告中按孤儿呈现，清理命令先物理回收行再统一收文件
    let now =
        time::OffsetDateTime::now_utc().format(&time::format_description::well_known::Rfc3339)?;
    let mut statement = store
        .conn
        .prepare("SELECT session_id, path FROM agent_artifacts WHERE expires_at >= ?1")?;
    let artifacts = statement
        .query_map(params![now], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (session, path) in artifacts {
        expected_scratch.insert(format!("agent-workspaces/{session}/scratch/{path}"));
    }
    let mut scratch = Vec::new();
    for path in walk_files(&root.join("agent-workspaces"), &mut issues)? {
        let item = file_item(&root, &path)?;
        if !expected_scratch.contains(&item.path) {
            let name = path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or_default();
            if name.starts_with('.')
                && (name.ends_with(".tmp") || name.ends_with(".previous"))
                && modified_recently(&path)
            {
                continue;
            }
            scratch.push(item);
        }
    }
    let mut plugins = Vec::new();
    let plugin_root = root.join("plugins");
    if plugin_root.exists() {
        for entry in fs::read_dir(&plugin_root)? {
            let entry = entry?;
            let id = entry.file_name().to_string_lossy().into_owned();
            // 14#19：install 中途失败会遗留“无 manifest.json 的目录”与
            // .manifest-N.tmp——无清单目录不可能是合法插件，一并纳入孤儿。
            // B-13：tmp 与其他进行中临时文件同享宽限——install 在清单写入
            // 成功后、事务 commit 前的毫秒窗内若被并发清理删掉清单，
            // 装好的插件将报 plugin_manifest_missing
            if id.starts_with(".manifest-") && id.ends_with(".tmp") && entry.path().is_file() {
                if !modified_recently(&entry.path()) {
                    plugins.push(file_item(&root, &entry.path())?);
                }
                continue;
            }
            let path = entry.path().join("manifest.json");
            let installed: bool = store.conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM plugins WHERE id = ?1)",
                params![id],
                |row| row.get(0),
            )?;
            if !installed && !entry.path().is_dir() {
                // 非目录非清单的杂项文件也按孤儿处理
                plugins.push(file_item(&root, &entry.path())?);
                continue;
            }
            if !installed && entry.path().is_dir() && !path.exists() {
                plugins.push(FileItem {
                    path: format!("plugins/{id}/manifest.json"),
                    bytes: 0,
                });
                continue;
            }
            // B-13：已安装插件缺磁盘清单（并发清理误删等）此前零报告——
            // reload 将报 plugin_manifest_missing，体检应能提前暴露
            if installed && entry.path().is_dir() && !path.exists() {
                issues.push(BackendError::new(
                    "plugin_manifest_missing",
                    &format!("插件 {id} 已安装但磁盘清单缺失，重装该插件可恢复"),
                ));
            }
            if !installed && path.is_file() {
                match fs::read_to_string(&path)
                    .ok()
                    .and_then(|content| super::plugins::validate_manifest_content(&content).ok())
                {
                    Some(()) => plugins.push(file_item(&root, &path)?),
                    None => issues.push(BackendError::new(
                        "invalid_plugin_manifest",
                        "过期插件清单无效，未自动删除",
                    )),
                }
            }
        }
    }
    let mut tempdirs = Vec::new();
    let backup_root = root.join("backup-work");
    if backup_root.exists() {
        for entry in fs::read_dir(backup_root)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            // 31#3：宽限方向修正——backup-work/{pid}-{n} 在宽限期内可能正被
            // 写入，不报告；超过宽限期才视为孤儿目录
            if entry.file_type()?.is_dir()
                && name.split('-').count() == 2
                && name.split('-').all(|part| part.parse::<u64>().is_ok())
                && !modified_recently(&entry.path())
            {
                let files = walk_files(&entry.path(), &mut issues)?;
                tempdirs.push(FileItem {
                    path: entry
                        .path()
                        .strip_prefix(&root)
                        .map_err(|_| BackendError::new("unsafe_path", "运维临时目录超出应用目录"))?
                        .to_string_lossy()
                        .replace('\\', "/"),
                    bytes: files
                        .iter()
                        .map(|path| {
                            fs::metadata(path)
                                .map(|metadata| metadata.len())
                                .unwrap_or(0)
                        })
                        .sum(),
                });
            }
        }
    }
    let export_root = root.join("exports");
    if export_root.exists() {
        for entry in fs::read_dir(export_root)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            // 31#3：同 backup-work——导出中的 anki 库不报告，宽限期外才算孤儿
            if entry.file_type()?.is_file()
                && name.starts_with("anki-")
                && name.ends_with(".db")
                && name[5..name.len() - 3]
                    .split('-')
                    .all(|part| part.parse::<u64>().is_ok())
                && !modified_recently(&entry.path())
            {
                tempdirs.push(file_item(&root, &entry.path())?);
            }
        }
    }
    // 65#4：prepare_reset 的中间目录 materials.reset-{n} 在提交后清理失败
    // 或进程于 rename 与 finish 间崩溃时残留于用户目录根层，原先任何扫描
    // 面都看不见——宽限期内可能正处于 reset 进行中，不报告
    for entry in fs::read_dir(&root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(suffix) = name.strip_prefix("materials.reset-") else {
            continue;
        };
        if entry.file_type()?.is_dir()
            && !suffix.is_empty()
            && suffix.bytes().all(|byte| byte.is_ascii_digit())
            && !modified_recently(&entry.path())
        {
            let files = walk_files(&entry.path(), &mut issues)?;
            tempdirs.push(FileItem {
                path: entry
                    .path()
                    .strip_prefix(&root)
                    .map_err(|_| BackendError::new("unsafe_path", "运维临时目录超出应用目录"))?
                    .to_string_lossy()
                    .replace('\\', "/"),
                bytes: files
                    .iter()
                    .map(|path| fs::metadata(path).map(|meta| meta.len()).unwrap_or(0))
                    .sum(),
            });
        }
    }
    // 65#7：首次迁移旧根库的 staging 文件 {db}.migration-tmp 在复制与改名
    // 两步间崩溃时残留（进程内错误路径已自回收）——含全部账户数据副本，
    // 须对运维可见；宽限覆盖迁移刚发生的窗口
    let staged = root.join(format!(
        "{}.migration-tmp",
        service
            .learning
            .workspace_path()
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("deep-tutor.db")
    ));
    if staged.is_file() && !modified_recently(&staged) {
        tempdirs.push(file_item(&root, &staged)?);
    }
    // 65#3：sync 目录的自剪枝只在新下载任务时触发——下载中途崩溃的临时件
    // 与 reset 后的存量不进任何清理面。对齐 sync 模块自身的保留语义：
    // 只碰 sync- 前缀且段全为数字的本应用产物；.json 保留最近 10 份，
    // .download-/.previous- 临时件（with_extension 丢失 .json 扩展名，
    // 不满足自剪枝过滤）按宽限判定
    let mut sync = Vec::new();
    let sync_root = root.join("sync");
    if sync_root.exists() {
        let mut retained: Vec<(std::time::SystemTime, FileItem)> = Vec::new();
        for entry in fs::read_dir(&sync_root)? {
            let entry = entry?;
            if !entry.file_type()?.is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(rest) = name.strip_prefix("sync-") else {
                continue;
            };
            let path = entry.path();
            if let Some(stem) = rest.strip_suffix(".json") {
                if stem.split('-').count() == 2
                    && stem.split('-').all(|part| part.parse::<u64>().is_ok())
                {
                    if let Ok(modified) = fs::metadata(&path).and_then(|meta| meta.modified()) {
                        retained.push((modified, file_item(&root, &path)?));
                    }
                }
                continue;
            }
            for marker in [".download-", ".previous-"] {
                let Some((stem, tail)) = rest.split_once(marker) else {
                    continue;
                };
                if tail.bytes().all(|byte| byte.is_ascii_digit())
                    && !tail.is_empty()
                    && stem.split('-').count() == 2
                    && stem.split('-').all(|part| part.parse::<u64>().is_ok())
                    && !modified_recently(&path)
                {
                    sync.push(file_item(&root, &path)?);
                }
                break;
            }
        }
        if retained.len() > 10 {
            retained.sort_by_key(|(modified, _)| *modified);
            let excess = retained.len() - 10;
            sync.extend(retained.into_iter().take(excess).map(|(_, item)| item));
        }
    }
    Ok(OrphanReport {
        originals,
        scratch,
        plugins,
        tempdirs,
        sync,
        missing_originals,
        issues,
    })
}

/// 65#1：agent_artifacts 的 expires_at 过期后行与 scratch 文件原先永存
/// （唯一出口是提升时的惰性置 status='expired'），磁盘与备份体积随
/// Agent 使用单调增长——清理命令先物理回收：IMMEDIATE 事务内选定并删除
/// 过期行（防与并发同路径重提交竞态），提交后删文件；文件删除失败或
/// 超出批次配额的残留由随后的孤儿扫描按「无行引用」收走。
/// 返回（已删文件数, 释放字节数）。
fn purge_expired_artifacts(service: &MaintenanceService) -> Result<(usize, u64)> {
    let mut store = service.store()?;
    let now =
        time::OffsetDateTime::now_utc().format(&time::format_description::well_known::Rfc3339)?;
    let tx = store
        .conn
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let expired = tx
        .prepare("SELECT session_id, path FROM agent_artifacts WHERE expires_at < ?1")?
        .query_map(params![now], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    tx.execute(
        "DELETE FROM agent_artifacts WHERE expires_at < ?1",
        params![now],
    )?;
    tx.commit()?;
    drop(store);
    let root = service.root();
    let mut removed = 0usize;
    let mut bytes = 0u64;
    for (session, path) in &expired {
        if removed >= CLEANUP_BATCH {
            break;
        }
        let relative = format!("agent-workspaces/{session}/scratch/{path}");
        let file = root.join(&relative);
        let Ok(metadata) = fs::metadata(&file) else {
            continue;
        };
        if !metadata.is_file() || modified_recently(&file) {
            // 刚重写的文件可能正被并发的同路径重提交引用（新行尚未落入
            // 本轮 SELECT），留给下一轮；缺失则行已删、无需处理
            continue;
        }
        let item = FileItem {
            path: relative,
            bytes: metadata.len(),
        };
        if remove_file(&root, &item).is_ok() {
            removed += 1;
            bytes += item.bytes;
        }
    }
    Ok((removed, bytes))
}

fn emit_result<R: Runtime, T: Serialize>(
    app: &AppHandle<R>,
    store: &Store,
    kind: &str,
    result: Result<T>,
) -> Result<T> {
    match result {
        Ok(value) => {
            let event = Store::event(
                &store.conn,
                &format!("maintenance.{kind}_completed"),
                serde_json::to_value(&value)?,
            );
            let event = event?;
            emit(app, &event);
            Ok(value)
        }
        Err(error) => {
            let event = Store::event(
                &store.conn,
                "maintenance.failed",
                json!({"kind": kind, "error": error}),
            )?;
            emit(app, &event);
            Err(error)
        }
    }
}

pub fn scan<R: Runtime>(
    app: &AppHandle<R>,
    service: &MaintenanceService,
) -> Result<MaintenanceReport> {
    let result = (|| {
        Ok(MaintenanceReport {
            database: health(service)?,
            orphans: scan_orphans(service)?,
        })
    })();
    let store = service.store()?;
    emit_result(app, &store, "scan", result)
}

fn remove_file(root: &Path, item: &FileItem) -> Result<u64> {
    let path = root.join(&item.path);
    if !fs::canonicalize(&path)?.starts_with(fs::canonicalize(root)?)
        || fs::symlink_metadata(&path)?.file_type().is_symlink()
    {
        return Err(BackendError::new(
            "unsafe_path",
            "已阻止删除超出应用目录的文件",
        ));
    }
    fs::remove_file(path)?;
    Ok(item.bytes)
}

fn remove_tree(root: &Path, directory: &Path, depth: usize) -> Result<()> {
    if !fs::canonicalize(directory)?.starts_with(fs::canonicalize(root)?) {
        return Err(BackendError::new(
            "unsafe_path",
            "临时目录超出应用目录，未删除",
        ));
    }
    if depth > 6 {
        return Err(BackendError::new("unsafe_path", "临时目录嵌套过深，未删除"));
    }
    if fs::symlink_metadata(directory)?.file_type().is_symlink() {
        return Err(BackendError::new(
            "unsafe_path",
            "临时目录包含符号链接，未删除",
        ));
    }
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        if fs::symlink_metadata(&path)?.file_type().is_symlink() {
            return Err(BackendError::new(
                "unsafe_path",
                "临时目录包含符号链接，未删除",
            ));
        }
        if path.is_dir() {
            remove_tree(root, &path, depth + 1)?;
        } else {
            fs::remove_file(path)?;
        }
    }
    Ok(fs::remove_dir(directory)?)
}

pub fn cleanup<R: Runtime>(
    app: &AppHandle<R>,
    service: &MaintenanceService,
) -> Result<CleanupResult> {
    let result = (|| {
        let _ = health(service)?;
        // 65#1：先物理回收过期 agent 产物（删行+删文件）再扫描——过期行
        // 不再进 scratch 期望集，文件删除失败或超额的残留由扫描收走
        let (purged_files, purged_bytes) = purge_expired_artifacts(service)?;
        let orphans = scan_orphans(service)?;
        let mut deleted_files = purged_files as u64;
        let mut deleted_temp_items = 0;
        let mut freed_bytes = purged_bytes;
        // 14#20：failures 只承载“删除失败”；扫描期的提示性跳过（清单无效、
        // 符号链接等）保留在 issues，不再混入失败列表同图标警示
        let mut failures = Vec::new();
        let root = service.root();
        let mut batch = purged_files;
        // 34#18：宽限跳过项不计入 remaining——原先「还有 N 项未处理」把 24h
        // 内清不掉的项也算进去，用户按提示重跑得到同样数字，提示空转
        let mut deferred = 0usize;
        for item in orphans.originals.iter().chain(&orphans.scratch) {
            if batch >= CLEANUP_BATCH {
                break;
            }
            // 31#32：删除前复查宽限——扫描与删除之间存在「导入已把 .upload-* 临时件
            // rename 为正式原件、事务尚未提交」的毫秒级窗口，rename 保留临时件刚
            // 写入的 mtime，命中宽限即跳过，下一轮扫描再收
            if modified_recently(&root.join(&item.path)) {
                deferred += 1;
                continue;
            }
            match remove_file(&root, item) {
                Ok(bytes) => {
                    batch += 1;
                    deleted_files += 1;
                    freed_bytes += bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        // 65#3：sync 类别在扫描期已完成宽限（临时件）与保留数（json）判定，
        // 不适用 31#32 的 mtime 复查——超量 json 是完成态写入，mtime 恒新；
        // 扫描后并发任务已转正的临时件按已消失处理
        for item in &orphans.sync {
            if batch >= CLEANUP_BATCH {
                break;
            }
            if !root.join(&item.path).exists() {
                // 70#6：扫描后已被并发 sync 自剪枝删除的项按「已处理」入账——
                // 原先既不进 batch 也不进 failures，remaining 把它们计成
                // 「未处理」，运维面板虚高一轮并诱导空跑；不计 deleted_files
                // （非本轮删除）
                batch += 1;
                continue;
            }
            match remove_file(&root, item) {
                Ok(bytes) => {
                    batch += 1;
                    deleted_files += 1;
                    freed_bytes += bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        for item in &orphans.plugins {
            if batch >= CLEANUP_BATCH {
                break;
            }
            // 14#19：插件孤儿按目录整体收——真实清单与“空目录占位项”都指向
            // 插件目录，只删 manifest.json 会留下空目录被反复扫描报出；
            // 直接位于 plugins 根下的杂项文件退回单文件删除
            let directory = Path::new(&item.path)
                .parent()
                .filter(|parent| parent.components().count() >= 2)
                .map(|parent| root.join(parent));
            let outcome = match directory {
                Some(directory) if directory.is_dir() => {
                    remove_tree(&root, &directory, 0).map(|()| item.bytes)
                }
                _ if !root.join(&item.path).exists() => {
                    // 同批更早项已连带删除本项所在目录
                    Ok(0)
                }
                _ => remove_file(&root, item),
            };
            match outcome {
                Ok(bytes) => {
                    batch += 1;
                    deleted_files += 1;
                    freed_bytes += bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        // B-12：tempdirs 循环此前无批次计数，孤儿量大时同样长占库锁
        for item in &orphans.tempdirs {
            if batch >= CLEANUP_BATCH {
                break;
            }
            let path = root.join(&item.path);
            let outcome = if path.is_dir() {
                remove_tree(&root, &path, 0)
            } else {
                remove_file(&root, item).map(|_| ())
            };
            match outcome {
                Ok(()) => {
                    batch += 1;
                    deleted_temp_items += 1;
                    freed_bytes += item.bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        // B-12：截断对用户可见——报告未轮到的剩余项数。purge 的计数不在
        // 本轮孤儿总数内（扫描前已移除），从已处理量中扣除
        let remaining = (orphans.originals.len()
            + orphans.scratch.len()
            + orphans.sync.len()
            + orphans.plugins.len()
            + orphans.tempdirs.len())
        .saturating_sub(batch - purged_files + failures.len() + deferred)
            as u64;
        Ok(CleanupResult {
            deleted_files,
            deleted_temp_items,
            freed_bytes,
            failures,
            remaining,
        })
    })();
    let store = service.store()?;
    emit_result(app, &store, "cleanup", result)
}

pub fn vacuum<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    service: &MaintenanceService,
) -> Result<VacuumResult> {
    let result = (|| {
        let _ = health(service)?;
        let before_bytes = fs::metadata(service.learning.workspace_path())?.len();
        // B-16：事件表只承担短窗口的通知投递职责（UI 刷新触发/插件投递
        // 记录），流式 delta 逐行写入使其随使用无限增长——整理时按
        // 「90 天且最近 20000 条以外」裁剪；状态恢复以全量快照为准，
        // 裁剪过期事件不影响一致性
        let cutoff = (time::OffsetDateTime::now_utc() - time::Duration::days(90))
            .format(&time::format_description::well_known::Rfc3339)?;
        store.conn.execute(
            "DELETE FROM events WHERE created_at < ?1 AND id <= (SELECT COALESCE(MAX(id), 0) - 20000 FROM events)",
            params![cutoff],
        )?;
        // 73#12：路由审计行随每次模型请求单调增长且此前无任何裁剪——
        // 整理时保留最近 1000 条 run（界面日志视图只读最近 100 条）；
        // attempts 经 FK ON DELETE CASCADE 连带清理
        // 75#5：事件 90 天裁剪同理——plugin_deliveries.event_id 的 FK CASCADE
        // 使投递审计随事件裁剪连带消失，此为既定语义（非泄漏）
        store.conn.execute(
            "DELETE FROM provider_route_runs WHERE request_id NOT IN (
                SELECT request_id FROM provider_route_runs ORDER BY created_at DESC, rowid DESC LIMIT 1000
            )",
            [],
        )?;
        store.conn.execute_batch("VACUUM")?;
        // 14#20：VACUUM 本体已完成；检查点被并发读挡住（wal_busy=1）只
        // 意味着 WAL 稍后再截断，不再把整条“整理数据库”报为失败误导重试
        let (wal_busy, wal_log, wal_checkpointed): (i64, i64, i64) = store
            .conn
            .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?))
            })
            .unwrap_or((1, 0, 0));
        Ok(VacuumResult {
            before_bytes,
            after_bytes: fs::metadata(service.learning.workspace_path())?.len(),
            wal_busy,
            wal_log,
            wal_checkpointed,
            journal_mode: store
                .conn
                .query_row("PRAGMA journal_mode", [], |row| row.get::<_, String>(0))?
                .to_lowercase(),
        })
    })();
    emit_result(app, store, "vacuum", result)
}
