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
                "材料原件路径不符合应用约定",
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
            originals.push(item);
        }
    }
    let mut expected_scratch = BTreeSet::new();
    let mut statement = store
        .conn
        .prepare("SELECT session_id, path FROM agent_artifacts")?;
    let artifacts = statement
        .query_map([], |row| {
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
            scratch.push(item);
        }
    }
    let mut plugins = Vec::new();
    let plugin_root = root.join("plugins");
    if plugin_root.exists() {
        for entry in fs::read_dir(&plugin_root)? {
            let entry = entry?;
            let id = entry.file_name().to_string_lossy().into_owned();
            let path = entry.path().join("manifest.json");
            let installed: bool = store.conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM plugins WHERE id = ?1)",
                params![id],
                |row| row.get(0),
            )?;
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
            if entry.file_type()?.is_dir()
                && name.split('-').count() == 2
                && name.split('-').all(|part| part.parse::<u64>().is_ok())
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
            if entry.file_type()?.is_file()
                && name.starts_with("anki-")
                && name.ends_with(".db")
                && name[5..name.len() - 3]
                    .split('-')
                    .all(|part| part.parse::<u64>().is_ok())
            {
                tempdirs.push(file_item(&root, &entry.path())?);
            }
        }
    }
    Ok(OrphanReport {
        originals,
        scratch,
        plugins,
        tempdirs,
        missing_originals,
        issues,
    })
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
        let orphans = scan_orphans(service)?;
        let mut deleted_files = 0;
        let mut deleted_temp_items = 0;
        let mut freed_bytes = 0;
        let mut failures = orphans.issues.clone();
        let root = service.root();
        for item in orphans
            .originals
            .iter()
            .chain(&orphans.scratch)
            .chain(&orphans.plugins)
        {
            match remove_file(&root, item) {
                Ok(bytes) => {
                    deleted_files += 1;
                    freed_bytes += bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        for item in &orphans.tempdirs {
            let path = root.join(&item.path);
            let outcome = if path.is_dir() {
                remove_tree(&root, &path, 0)
            } else {
                remove_file(&root, item).map(|_| ())
            };
            match outcome {
                Ok(()) => {
                    deleted_temp_items += 1;
                    freed_bytes += item.bytes;
                }
                Err(error) => failures.push(error),
            }
        }
        Ok(CleanupResult {
            deleted_files,
            deleted_temp_items,
            freed_bytes,
            failures,
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
        store.conn.execute_batch("VACUUM")?;
        let (wal_busy, wal_log, wal_checkpointed): (i64, i64, i64) =
            store
                .conn
                .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| {
                    Ok((row.get(0)?, row.get(1)?, row.get(2)?))
                })?;
        if wal_busy != 0 {
            return Err(BackendError::new(
                "database_busy",
                "数据库检查点未完成，请稍后重试",
            ));
        }
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
