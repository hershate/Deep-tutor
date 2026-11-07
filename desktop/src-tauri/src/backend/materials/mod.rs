pub mod archive;
pub mod commands;
pub mod media;
pub mod parse;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use parse::{parse, MAX_FILE_BYTES};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const SUPPORTED: [&str; 19] = [
    "pdf", "docx", "pptx", "epub", "txt", "md", "mp4", "mov", "m4v", "webm", "mp3", "wav", "ogg",
    "m4a", "png", "jpg", "jpeg", "gif", "webp",
];
static TEMP_ID: AtomicU64 = AtomicU64::new(0);

const MAX_PARSE_WORKERS: usize = 1;

pub struct MaterialState {
    database_path: PathBuf,
    active: Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>,
    accepting: Arc<AtomicBool>,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
    #[cfg(test)]
    pause: Arc<AtomicBool>,
    #[cfg(test)]
    defer: Arc<AtomicBool>,
}

impl MaterialState {
    pub fn new(database_path: &Path) -> Result<Self> {
        let state = Self {
            database_path: database_path.to_path_buf(),
            active: Arc::new(Mutex::new(HashMap::new())),
            accepting: Arc::new(AtomicBool::new(true)),
            #[cfg(test)]
            _guard: None,
            #[cfg(test)]
            pause: Arc::new(AtomicBool::new(false)),
            #[cfg(test)]
            defer: Arc::new(AtomicBool::new(false)),
        };
        fs::create_dir_all(state.root())?;
        Ok(state)
    }

    fn root(&self) -> PathBuf {
        super::security::workspace_path(&self.database_path)
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("materials")
    }

    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            active: Arc::clone(&self.active),
            accepting: Arc::clone(&self.accepting),
            #[cfg(test)]
            _guard: None,
            #[cfg(test)]
            pause: Arc::clone(&self.pause),
            #[cfg(test)]
            defer: Arc::clone(&self.defer),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        let mut state = Self::new(&guard.path().join("app.db"))?;
        state._guard = Some(guard);
        Ok(state)
    }

    #[cfg(test)]
    pub fn test_database_path(&self) -> PathBuf {
        self.database_path.clone()
    }

    fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }

    fn is_active(&self, id: i64) -> bool {
        self.active
            .lock()
            .map(|jobs| jobs.contains_key(&id))
            .unwrap_or(false)
    }

    fn stop_all(&self) -> Result<()> {
        self.accepting.store(false, Ordering::Release);
        let jobs = self
            .active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "材料任务状态暂不可用"))?;
        for flag in jobs.values() {
            flag.store(true, Ordering::Release);
        }
        drop(jobs);
        wait_idle(&self.active)
    }
}

pub(crate) fn stop(state: &MaterialState) -> Result<()> {
    state.stop_all()
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Material {
    pub id: i64,
    pub title: String,
    pub kind: String,
    pub size: u64,
    pub sha256: String,
    pub status: String,
    pub progress: i64,
    pub text_length: i64,
    pub parse_mode: String,
    pub warning: Option<String>,
    pub error: Option<BackendError>,
    pub original_available: bool,
    pub source_url: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IngestResult {
    pub material: Material,
    pub deduplicated: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportOutcome {
    pub file_name: String,
    pub material: Option<Material>,
    pub deduplicated: bool,
    pub error: Option<BackendError>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaterialDetail {
    #[serde(flatten)]
    pub material: Material,
    pub text: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobAccepted {
    pub job_id: i64,
    pub material: Material,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

/// `legacy_import`：是否执行 kv→materials 的一次性旧数据导入。
/// 根库（账号库）传 false，避免一次性复制后在根库再生成一份死数据（08#8）。
pub fn migrate(conn: &Connection, legacy_import: bool) -> Result<()> {
    migrate_schema(conn)?;
    if !legacy_import {
        return Ok(());
    }
    // kv→materials 的一次性导入只在真正的旧版用户库上执行；meta 标记防止重复执行
    let imported_marker: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'materials_kv_imported'",
            [],
            |row| row.get(0),
        )
        .optional()?
        .filter(|value| value == "1");
    if imported_marker.is_some() {
        return Ok(());
    }
    let has_materials: bool =
        conn.query_row("SELECT EXISTS(SELECT 1 FROM materials)", [], |row| {
            row.get(0)
        })?;
    if has_materials {
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('materials_kv_imported', '1')
             ON CONFLICT(key) DO UPDATE SET value = '1'",
            [],
        )?;
        return Ok(());
    }
    let mut statement = conn.prepare("SELECT data FROM kv WHERE namespace = 'resources'")?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    let mut migrated = false;
    for row in rows {
        let value: Value = serde_json::from_str(&row?)?;
        let id = value.get("id").and_then(Value::as_i64).unwrap_or(0);
        if id > 0 {
            import_metadata(conn, &value, id)?;
            migrated = true;
        }
    }
    if migrated {
        sync_resources(conn)?;
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('materials_kv_imported', '1')
             ON CONFLICT(key) DO UPDATE SET value = '1'",
            [],
        )?;
    }
    Ok(())
}

fn migrate_schema(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT UNIQUE,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            parse_mode TEXT NOT NULL DEFAULT '',
            text_length INTEGER NOT NULL DEFAULT 0,
            warning TEXT,
            error_code TEXT,
            error_message TEXT,
            source_meta TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS material_text (
            material_id INTEGER PRIMARY KEY REFERENCES materials(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            mode TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS material_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(material_id, kind, attempt)
        );
        CREATE INDEX IF NOT EXISTS material_jobs_active ON material_jobs(material_id, status);",
    )?;
    Ok(())
}

pub fn import_metadata(conn: &Connection, value: &Value, id: i64) -> Result<i64> {
    let title = value
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("导入资料");
    let kind = if value.get("type").and_then(Value::as_str) == Some("PDF") {
        "pdf"
    } else {
        "file"
    };
    let meta = value.get("meta").and_then(Value::as_str).unwrap_or("");
    let timestamp = now()?;
    conn.execute(
        "INSERT INTO materials (id, title, kind, size, sha256, status, progress, warning, source_meta, created_at, updated_at)
         VALUES (?1, ?2, ?3, 0, NULL, 'metadata_only', 0, ?4, ?5, ?6, ?6)",
        params![id, title, kind, "原件未随元数据备份提供", meta, timestamp],
    )?;
    Ok(id)
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM material_jobs", [])?;
    conn.execute("DELETE FROM material_text", [])?;
    conn.execute("DELETE FROM materials", [])?;
    Ok(())
}

fn status_label(status: &str) -> &str {
    match status {
        "staged" => "已暂存",
        "queued" => "等待解析",
        "parsing" => "解析中",
        "parsed" => "已解析",
        "degraded" => "部分解析",
        "failed" => "解析失败",
        "cancelling" => "正在取消",
        "cancelled" => "已取消",
        "metadata_only" => "仅元数据",
        _ => "状态未知",
    }
}

pub(crate) fn sync_resources(conn: &Connection) -> Result<()> {
    let mut statement = conn.prepare(
        "SELECT id, title, kind, size, status, progress, text_length, source_meta FROM materials ORDER BY id",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, i64>(3)?,
            row.get::<_, String>(4)?,
            row.get::<_, i64>(5)?,
            row.get::<_, i64>(6)?,
            row.get::<_, String>(7)?,
        ))
    })?;
    let mut resources = Vec::new();
    for row in rows {
        let (id, title, kind, size, status, progress, text_length, source_meta) = row?;
        let size_kb = (size.max(0) as u64 + 1023) / 1024;
        let meta = if status == "metadata_only" {
            format!("{source_meta} · 原件未随备份提供")
        } else if text_length > 0 {
            format!(
                "{size_kb} KB · {} · {text_length} 字",
                status_label(&status)
            )
        } else {
            format!("{size_kb} KB · {}", status_label(&status))
        };
        let webpage = source_meta.starts_with("https://") || source_meta.starts_with("http://");
        let meta = if webpage && status != "metadata_only" {
            format!("{source_meta} · {meta}")
        } else {
            meta
        };
        resources.push(json!({
            "id": id,
            "title": title,
            "type": if kind == "pdf" { "PDF" } else if webpage { "网页" } else { media::category(&kind).unwrap_or("文件") },
            "meta": meta,
            "progress": progress
        }));
    }
    conn.execute("DELETE FROM kv WHERE namespace = 'resources'", [])?;
    for resource in resources {
        let id = resource["id"].as_i64().unwrap_or_default().to_string();
        Store::insert_json(conn, "resources", &id, &resource)?;
    }
    Store::next_revision(conn, "resources")?;
    Ok(())
}

fn original_path(root: &Path, sha256: &str, kind: &str) -> PathBuf {
    root.join("originals").join(format!("{sha256}.{kind}"))
}

fn material(row: &Row<'_>, root: &Path) -> rusqlite::Result<Material> {
    let sha256: Option<String> = row.get(4)?;
    let kind: String = row.get(2)?;
    let error_code: Option<String> = row.get(9)?;
    let error_message: Option<String> = row.get(10)?;
    Ok(Material {
        id: row.get(0)?,
        title: row.get(1)?,
        kind: kind.to_uppercase(),
        size: row.get::<_, i64>(3)?.max(0) as u64,
        original_available: sha256
            .as_deref()
            .is_some_and(|sha| original_path(root, sha, &kind).is_file()),
        sha256: sha256.unwrap_or_default(),
        status: row.get(5)?,
        progress: row.get(6)?,
        text_length: row.get(7)?,
        parse_mode: row.get(8)?,
        warning: row.get(11)?,
        error: error_code
            .map(|code| BackendError::new(&code, error_message.as_deref().unwrap_or("解析失败"))),
        source_url: row
            .get::<_, Option<String>>(12)
            .unwrap_or(None)
            .filter(|value| !value.is_empty()),
    })
}

fn get(conn: &Connection, root: &Path, id: i64) -> Result<Material> {
    if id <= 0 {
        return Err(BackendError::invalid("invalid_id", "资料标识必须是正整数"));
    }
    conn.query_row(
        "SELECT id, title, kind, size, sha256, status, progress, text_length, parse_mode, error_code, error_message, warning, source_meta
         FROM materials WHERE id = ?1",
        params![id],
        |row| material(row, root),
    )
    .optional()?
    .ok_or_else(|| BackendError::new("not_found", "资料不存在"))
}

pub fn detail(state: &MaterialState, id: i64) -> Result<MaterialDetail> {
    let store = state.store()?;
    let material = get(&store.conn, &state.root(), id)?;
    let text = store
        .conn
        .query_row(
            "SELECT content FROM material_text WHERE material_id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?;
    Ok(MaterialDetail { material, text })
}

pub fn list(state: &MaterialState) -> Result<Vec<Material>> {
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id, title, kind, size, sha256, status, progress, text_length, parse_mode, error_code, error_message, warning, source_meta FROM materials ORDER BY id",
    )?;
    let rows = statement.query_map([], |row| material(row, &state.root()))?;
    let mut materials = Vec::new();
    for row in rows {
        materials.push(row?);
    }
    Ok(materials)
}

fn validate_source(path: &Path) -> Result<(String, String, Vec<u8>, String)> {
    let path = fs::canonicalize(path)
        .map_err(|_| BackendError::new("not_found", "选择的文件不存在或不可读"))?;
    let metadata = fs::metadata(&path)?;
    if !metadata.is_file() {
        return Err(BackendError::new("invalid_file", "只能选择普通文件"));
    }
    if metadata.len() == 0 {
        return Err(BackendError::new("empty_content", "不能导入空文件"));
    }
    if metadata.len() > MAX_FILE_BYTES {
        return Err(BackendError::new(
            "file_too_large",
            "单份资料不能超过 50 MB",
        ));
    }
    let title = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .filter(|name| !name.trim().is_empty() && name.len() <= 500)
        .ok_or_else(|| {
            BackendError::invalid("invalid_file_name", "文件名不能为空且不能超过 500 字节")
        })?;
    let kind = path
        .extension()
        .map(|extension| extension.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_default();
    if !SUPPORTED.contains(&kind.as_str()) {
        return Err(BackendError::new(
            "unsupported_format",
            "当前支持 PDF、DOCX、PPTX、EPUB、TXT 和 Markdown；其他格式尚未接入原生解析",
        ));
    }
    let bytes = fs::read(path)?;
    if metadata.len() != bytes.len() as u64 {
        return Err(BackendError::new(
            "file_changed",
            "文件在读取过程中发生变化，请重试",
        ));
    }
    validate_content(&kind, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    Ok((title, kind, bytes, sha256))
}

fn validate_content(kind: &str, bytes: &[u8]) -> Result<()> {
    if !SUPPORTED.contains(&kind) {
        return Err(BackendError::new(
            "unsupported_format",
            "支持 PDF、DOCX、PPTX、EPUB、TXT、Markdown，以及常见音视频和图片原件",
        ));
    }
    match kind {
        "pdf" if !bytes.starts_with(b"%PDF-") => {
            return Err(BackendError::new("invalid_format", "文件内容不是有效 PDF"))
        }
        "docx" | "pptx" | "epub" if !bytes.starts_with(b"PK\x03\x04") => {
            return Err(BackendError::new(
                "invalid_format",
                "文件内容不是有效的 Office 或 EPUB 压缩文档",
            ))
        }
        "txt" | "md" if bytes.contains(&0) || std::str::from_utf8(bytes).is_err() => {
            return Err(BackendError::new(
                "invalid_format",
                "文本文件必须是有效 UTF-8 且不含 NUL",
            ))
        }
        _ if media::mime(kind).is_some() => media::validate(kind, bytes)?,
        _ => {}
    }
    Ok(())
}

fn stage_validated<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    title: String,
    kind: String,
    bytes: Vec<u8>,
    sha256: String,
    source_url: Option<&str>,
    initial_warning: Option<&str>,
) -> Result<IngestResult> {
    let store = state.store()?;
    if let Some(existing) = store
        .conn
        .query_row(
            "SELECT id FROM materials WHERE sha256 = ?1",
            params![sha256],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
    {
        return Ok(IngestResult {
            material: get(&store.conn, &state.root(), existing)?,
            deduplicated: true,
        });
    }
    store.ensure_quota("materials", 1)?;
    let destination = original_path(&state.root(), &sha256, &kind);
    fs::create_dir_all(destination.parent().unwrap_or(&state.root()))?;
    let temporary = destination.with_extension(format!(
        "{kind}.upload-{}-{}",
        std::process::id(),
        TEMP_ID.fetch_add(1, Ordering::Relaxed)
    ));
    let mut reused = destination.is_file();
    if reused && fs::read(&destination).map(|old| format!("{:x}", Sha256::digest(old)))? != sha256 {
        fs::remove_file(&destination)?;
        reused = false;
    }
    if !reused {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        std::io::Write::write_all(&mut file, &bytes)?;
        file.sync_all()?;
        drop(file);
        if let Err(error) = fs::rename(&temporary, &destination) {
            let _ = fs::remove_file(&temporary);
            return Err(error.into());
        }
    } else {
        let _ = fs::remove_file(&temporary);
    }
    let timestamp = now()?;
    let result = (|| {
        let tx = store.conn.unchecked_transaction()?;
        tx.execute(
            "INSERT INTO materials (title, kind, size, sha256, status, progress, warning, source_meta, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, 'staged', 0, ?7, ?5, ?6, ?6)",
            params![title, kind, bytes.len() as i64, sha256, source_url.unwrap_or(""), timestamp, initial_warning],
        )?;
        let id = tx.last_insert_rowid();
        sync_resources(&tx)?;
        let event = Store::event(
            &tx,
            "material.staged",
            json!({"materialId": id, "status": "staged", "progress": 0}),
        )?;
        tx.commit()?;
        Ok((id, event))
    })();
    let (id, event) = match result {
        Ok(value) => value,
        Err(error) => {
            if !reused {
                let _ = fs::remove_file(&destination);
            }
            return Err(error);
        }
    };
    emit(app, &event);
    Ok(IngestResult {
        material: get(&store.conn, &state.root(), id)?,
        deduplicated: false,
    })
}

fn stage_bytes<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    title: String,
    kind: String,
    bytes: Vec<u8>,
) -> Result<IngestResult> {
    if bytes.is_empty() || bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(BackendError::new(
            "invalid_archive_entry",
            "资料包条目必须包含 1 到 50 MB 内容",
        ));
    }
    validate_content(&kind, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    stage_validated(app, state, title, kind, bytes, sha256, None, None)
}

fn stage_one<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    source: &Path,
) -> Result<IngestResult> {
    let (title, kind, bytes, sha256) = validate_source(source)?;
    stage_validated(app, state, title, kind, bytes, sha256, None, None)
}

pub fn import_text<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    title: String,
    content: String,
) -> Result<IngestResult> {
    import_text_with_source(app, state, title, content, None, None)
}

pub(crate) fn import_text_with_source<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    title: String,
    content: String,
    source_url: Option<&str>,
    initial_warning: Option<&str>,
) -> Result<IngestResult> {
    let title = title.trim();
    if title.is_empty() || title.len() > 200 {
        return Err(BackendError::invalid(
            "invalid_title",
            "文本资料标题不能为空且不能超过 200 字节",
        ));
    }
    if content.trim().is_empty() || content.len() > 500_000 {
        return Err(BackendError::invalid(
            "invalid_text",
            "文本资料须包含 1 到 500000 字节的内容",
        ));
    }
    if content.contains('\0') {
        return Err(BackendError::invalid(
            "invalid_text",
            "文本资料不得包含 NUL 字符",
        ));
    }
    let bytes = content.into_bytes();
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    stage_validated(
        app,
        state,
        title.to_string(),
        "txt".into(),
        bytes,
        sha256,
        source_url,
        None,
    )
}

pub fn import_files<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    paths: Vec<String>,
) -> Vec<ImportOutcome> {
    paths
        .into_iter()
        .map(|path| {
            let file_name = Path::new(&path)
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.clone());
            match stage_one(app, state, Path::new(&path)) {
                Ok(result) => ImportOutcome {
                    file_name,
                    material: Some(result.material),
                    deduplicated: result.deduplicated,
                    error: None,
                },
                Err(error) => ImportOutcome {
                    file_name,
                    material: None,
                    deduplicated: false,
                    error: Some(error),
                },
            }
        })
        .collect()
}

fn transition<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    root: &Path,
    id: i64,
    job_id: i64,
    status: &str,
    progress: i64,
    error: Option<&BackendError>,
) -> Result<Material> {
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE materials SET status = ?2, progress = ?3, error_code = ?4, error_message = ?5, updated_at = ?6 WHERE id = ?1",
        params![id, status, progress, error.map(|error| &error.code), error.map(|error| &error.message), now()?],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "material_deleted",
            "资料已删除，停止本次解析",
        ));
    }
    tx.execute(
        "UPDATE material_jobs SET status = ?2, error_code = ?3, error_message = ?4, updated_at = ?5 WHERE id = ?1",
        params![job_id, status, error.map(|error| &error.code), error.map(|error| &error.message), now()?],
    )?;
    sync_resources(&tx)?;
    let event = Store::event(
        &tx,
        &format!("material.{status}"),
        json!({"materialId": id, "jobId": job_id, "status": status, "progress": progress, "error": error}),
    )?;
    tx.commit()?;
    emit(app, &event);
    get(&store.conn, root, id)
}

fn run_job<R: Runtime>(
    app: AppHandle<R>,
    database_path: PathBuf,
    root: PathBuf,
    id: i64,
    job_id: i64,
    cancel: Arc<AtomicBool>,
) -> Result<()> {
    if cancel.load(Ordering::Acquire) {
        return Err(BackendError::new(
            "cancelled",
            "解析已取消，未写入新的解析文本",
        ));
    }
    let store = Store::new(database_path)?;
    transition(&app, &store, &root, id, job_id, "parsing", 35, None)?;
    let (sha256, kind): (Option<String>, String) = store.conn.query_row(
        "SELECT sha256, kind FROM materials WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let sha256 = sha256
        .ok_or_else(|| BackendError::new("missing_original", "该资料只有元数据，没有可解析原件"))?;
    let path = original_path(&root, &sha256, &kind);
    let bytes = fs::read(&path)
        .map_err(|_| BackendError::new("missing_original", "原件不存在或已被移动"))?;
    if format!("{:x}", Sha256::digest(&bytes)) != sha256 {
        return Err(BackendError::new(
            "original_changed",
            "原件校验失败，已停止解析",
        ));
    }
    let parsed = parse(&bytes, &kind, cancel.clone())?;
    if cancel.load(Ordering::Acquire) {
        return Err(BackendError::new(
            "cancelled",
            "解析已取消，未写入新的解析文本",
        ));
    }
    transition(&app, &store, &root, id, job_id, "parsing", 85, None)?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        // warning 取解析阶段与入库阶段先到者：网页导入的截断警示不被
        // 纯文本解析的“无警告”覆盖（10#25）
        "UPDATE materials SET status = ?2, progress = 100, text_length = ?3, parse_mode = ?4, warning = COALESCE(?5, warning), error_code = NULL, error_message = NULL, updated_at = ?6 WHERE id = ?1",
        params![id, if parsed.warning.is_some() { "degraded" } else { "parsed" }, parsed.text.chars().count() as i64, parsed.mode, parsed.warning, now()?],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "material_deleted",
            "资料已删除，未提交解析结果",
        ));
    }
    tx.execute(
        "INSERT INTO material_text (material_id, content, mode, updated_at) VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(material_id) DO UPDATE SET content = excluded.content, mode = excluded.mode, updated_at = excluded.updated_at",
        params![id, parsed.text, "native", now()?],
    )?;
    tx.execute(
        "UPDATE material_jobs SET status = 'done', updated_at = ?1 WHERE id = ?2",
        params![now()?, job_id],
    )?;
    sync_resources(&tx)?;
    let event = Store::event(
        &tx,
        "material.parsed",
        json!({"materialId": id, "jobId": job_id, "status": "parsed", "progress": 100}),
    )?;
    tx.commit()?;
    emit(&app, &event);
    Ok(())
}

fn spawn_job<R: Runtime>(
    app: AppHandle<R>,
    state: &MaterialState,
    id: i64,
    job_id: i64,
    from_worker: bool,
) -> Result<()> {
    #[cfg(test)]
    if state.defer.load(Ordering::Acquire) {
        return Ok(());
    }
    if !state.accepting.load(Ordering::Acquire) {
        return Ok(());
    }
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut active = state
            .active
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "材料任务状态暂不可用"))?;
        if active.contains_key(&id) {
            return Err(BackendError::new("job_active", "该资料已在解析队列中"));
        }
        if (!from_worker && active.len() >= MAX_PARSE_WORKERS)
            || !state.accepting.load(Ordering::Acquire)
        {
            return Ok(());
        }
        active.insert(id, cancel.clone());
    }
    let next_state = state.shared();
    let root = state.root();
    let database_path = state.database_path.clone();
    let job_cancel = cancel.clone();
    let thread_app = app.clone();
    let state_database_path = database_path.clone();
    let state_root = root.clone();
    #[cfg(test)]
    let pause = state.pause.clone();
    let result = super::security::thread_builder()
        .name(format!("material-parse-{id}"))
        .spawn(move || {
            #[cfg(test)]
            while pause.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(2));
            }
            let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run_job(
                    thread_app.clone(),
                    database_path,
                    root,
                    id,
                    job_id,
                    job_cancel.clone(),
                )
            }));
            match Store::new(state_database_path) {
                Ok(store) => match outcome {
                    Ok(Ok(())) => {}
                    Ok(Err(error)) => {
                        let status = if error.code == "cancelled" {
                            "cancelled"
                        } else {
                            "failed"
                        };
                        if let Err(terminal_error) = transition(
                            &thread_app,
                            &store,
                            &state_root,
                            id,
                            job_id,
                            status,
                            0,
                            Some(&error),
                        ) {
                            eprintln!(
                                "material {id} terminal update failed: {}",
                                terminal_error.code
                            );
                        }
                    }
                    Err(_) => {
                        let error = BackendError::new(
                            "internal_error",
                            "解析任务异常终止，未提交新的解析文本",
                        );
                        if let Err(terminal_error) = transition(
                            &thread_app,
                            &store,
                            &state_root,
                            id,
                            job_id,
                            "failed",
                            0,
                            Some(&error),
                        ) {
                            eprintln!(
                                "material {id} terminal update failed: {}",
                                terminal_error.code
                            );
                        }
                    }
                },
                Err(error) => {
                    eprintln!("material {id} initialization failed: {}", error.code);
                }
            }
            if let Err(error) = schedule_next(&thread_app, &next_state, id) {
                eprintln!("material queue continuation failed: {}", error.code);
            }
        });
    if result.is_err() {
        if let Ok(mut active) = state.active.lock() {
            active.remove(&id);
        }
        let error = BackendError::new("job_start_failed", "无法启动原生解析任务");
        let store = state.store()?;
        transition(
            &app,
            &store,
            &state.root(),
            id,
            job_id,
            "failed",
            0,
            Some(&error),
        )?;
        return Err(error);
    }
    Ok(())
}

pub fn enqueue<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    id: i64,
) -> Result<JobAccepted> {
    if state.is_active(id) {
        return Err(BackendError::new(
            "job_active",
            "该资料已在解析队列中，不会重复执行",
        ));
    }
    let store = state.store()?;
    let material = get(&store.conn, &state.root(), id)?;
    if media::mime(&material.kind).is_some() {
        return Err(BackendError::new(
            "media_analysis_unsupported",
            "音视频与图片仅支持原件预览，不提供文字识别或内容分析",
        ));
    }
    if material.status == "metadata_only" {
        return Err(BackendError::new(
            "missing_original",
            "该资料只有元数据，没有可解析原件",
        ));
    }
    let active_job: Option<i64> = store.conn.query_row(
        "SELECT id FROM material_jobs WHERE material_id = ?1 AND status IN ('queued', 'parsing', 'cancelling')",
        params![id],
        |row| row.get(0),
    ).optional()?;
    if active_job.is_some() {
        return Err(BackendError::new(
            "job_active",
            "该资料已有未完成任务，不会重复执行",
        ));
    }
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    let attempt: i64 = tx.query_row(
        "SELECT COALESCE(MAX(attempt), 0) + 1 FROM material_jobs WHERE material_id = ?1 AND kind = 'parse'",
        params![id],
        |row| row.get(0),
    )?;
    tx.execute(
        "INSERT INTO material_jobs (material_id, kind, attempt, status, created_at, updated_at) VALUES (?1, 'parse', ?2, 'queued', ?3, ?3)",
        params![id, attempt, timestamp],
    )?;
    let job_id = tx.last_insert_rowid();
    tx.execute(
        "UPDATE materials SET status = 'queued', progress = 10, error_code = NULL, error_message = NULL, updated_at = ?1 WHERE id = ?2",
        params![timestamp, id],
    )?;
    sync_resources(&tx)?;
    let event = Store::event(
        &tx,
        "material.queued",
        json!({"materialId": id, "jobId": job_id, "status": "queued", "progress": 10}),
    )?;
    tx.commit()?;
    emit(app, &event);
    spawn_job(app.clone(), state, id, job_id, false)?;
    Ok(JobAccepted {
        job_id,
        material: get(&store.conn, &state.root(), id)?,
    })
}

pub fn cancel<R: Runtime>(app: &AppHandle<R>, state: &MaterialState, id: i64) -> Result<Material> {
    let flag = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "材料任务状态暂不可用"))?
        .get(&id)
        .cloned();
    if let Some(flag) = &flag {
        flag.store(true, Ordering::Release);
    }
    let store = state.store()?;
    let job: Option<(i64, String)> = store
        .conn
        .query_row(
            "SELECT id, status FROM material_jobs WHERE material_id = ?1 AND status IN ('queued', 'parsing', 'cancelling') ORDER BY id DESC LIMIT 1",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let Some((job_id, status)) = job else {
        return Err(BackendError::new(
            "no_active_job",
            "该资料没有正在执行的解析任务",
        ));
    };
    if flag.is_none() && status != "queued" {
        return Err(BackendError::new(
            "no_active_job",
            "该资料没有正在执行的解析任务",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("UPDATE material_jobs SET cancel_requested = 1, status = 'cancelling', updated_at = ?1 WHERE id = ?2", params![now()?, job_id])?;
    tx.execute(
        "UPDATE materials SET status = 'cancelling', updated_at = ?1 WHERE id = ?2",
        params![now()?, id],
    )?;
    sync_resources(&tx)?;
    let event = Store::event(
        &tx,
        "material.cancel_requested",
        json!({"materialId": id, "jobId": job_id, "status": "cancelling"}),
    )?;
    tx.commit()?;
    emit(app, &event);
    if flag.is_none() {
        let error = BackendError::new("cancelled", "等待中的解析任务已取消，未读取原件");
        transition(
            app,
            &store,
            &state.root(),
            id,
            job_id,
            "cancelled",
            0,
            Some(&error),
        )?;
    }
    get(&store.conn, &state.root(), id)
}

fn schedule_next<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    finished_id: i64,
) -> Result<()> {
    let result = (|| -> Result<()> {
        if !state.accepting.load(Ordering::Acquire) {
            return Ok(());
        }
        let store = state.store()?;
        let mut statement = store.conn.prepare(
            "SELECT material_id, id FROM material_jobs WHERE status = 'queued' ORDER BY id",
        )?;
        let jobs = statement
            .query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(statement);
        drop(store);
        for (id, job_id) in jobs {
            if id == finished_id || state.is_active(id) {
                continue;
            }
            spawn_job(app.clone(), state, id, job_id, true)?;
            break;
        }
        Ok(())
    })();
    if let Ok(mut active) = state.active.lock() {
        active.remove(&finished_id);
    }
    result
}

pub fn recover<R: Runtime>(app: &AppHandle<R>, state: &MaterialState) -> Result<()> {
    state.accepting.store(true, Ordering::Release);
    let store = state.store()?;
    let mut statement = store.conn.prepare("SELECT id, material_id, cancel_requested FROM material_jobs WHERE status IN ('queued', 'parsing', 'cancelling')")?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, bool>(2)?,
        ))
    })?;
    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(row?);
    }
    drop(statement);
    for (job_id, id, cancel_requested) in jobs {
        if cancel_requested {
            let error = BackendError::new("cancelled", "应用重启后，已取消的解析任务未重新执行");
            transition(
                app,
                &store,
                &state.root(),
                id,
                job_id,
                "cancelled",
                0,
                Some(&error),
            )?;
        } else {
            transition(app, &store, &state.root(), id, job_id, "queued", 10, None)?;
            spawn_job(app.clone(), state, id, job_id, false)?;
        }
    }
    Ok(())
}

pub fn delete<R: Runtime>(app: &AppHandle<R>, state: &MaterialState, id: i64) -> Result<()> {
    if state.is_active(id) {
        return Err(BackendError::new(
            "job_active",
            "请先取消并等待解析结束，再删除资料",
        ));
    }
    let store = state.store()?;
    let material = get(&store.conn, &state.root(), id)?;
    let in_use: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM learning_plan_materials WHERE material_id = ?1) OR EXISTS(SELECT 1 FROM knowledge_session_materials WHERE material_id = ?1)",
        params![id],
        |row| row.get(0),
    )?;
    if in_use {
        return Err(BackendError::new(
            "material_in_use",
            "该资料已被学习方案或 Wiki 问答引用，不能删除其原文证据",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("DELETE FROM materials WHERE id = ?1", params![id])?;
    if changed == 0 {
        return Err(BackendError::new("not_found", "资料不存在"));
    }
    sync_resources(&tx)?;
    let event = Store::event(&tx, "material.deleted", json!({"materialId": id}))?;
    tx.commit()?;
    emit(app, &event);
    if material.original_available {
        fs::remove_file(original_path(
            &state.root(),
            &material.sha256,
            &material.kind.to_ascii_lowercase(),
        ))
        .map_err(|_| {
            BackendError::new(
                "cleanup_failed",
                "资料记录已删除，但原件清理失败，请检查应用数据目录",
            )
        })?;
    }
    Ok(())
}

pub fn export_original(state: &MaterialState, id: i64, destination: &Path) -> Result<Value> {
    let store = state.store()?;
    let material = get(&store.conn, &state.root(), id)?;
    if !material.original_available {
        return Err(BackendError::new(
            "missing_original",
            "原件不存在，无法导出",
        ));
    }
    let source = original_path(
        &state.root(),
        &material.sha256,
        &material.kind.to_ascii_lowercase(),
    );
    let bytes = fs::read(source)?;
    if format!("{:x}", Sha256::digest(&bytes)) != material.sha256 {
        return Err(BackendError::new(
            "original_changed",
            "原件校验失败，未导出",
        ));
    }
    atomic_export(destination, &bytes)?;
    Ok(json!({"bytes": bytes.len(), "sha256": material.sha256}))
}

pub(crate) fn atomic_export(destination: &Path, bytes: &[u8]) -> Result<()> {
    if destination.file_name().is_none() {
        return Err(BackendError::invalid(
            "invalid_destination",
            "导出目标必须是文件",
        ));
    }
    let parent = destination
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    let unique = TEMP_ID.fetch_add(1, Ordering::Relaxed);
    let temporary = destination.with_extension(format!("download-{unique}"));
    let previous = destination.with_extension(format!("previous-{unique}"));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    std::io::Write::write_all(&mut file, bytes)?;
    file.sync_all()?;
    drop(file);
    let replaced = destination.exists();
    if replaced {
        fs::rename(destination, &previous)?;
    }
    match fs::rename(&temporary, destination) {
        Ok(()) => {
            if replaced {
                fs::remove_file(previous).map_err(|_| {
                    BackendError::new("cleanup_failed", "导出已写入，旧文件备份清理失败")
                })?;
            }
            Ok(())
        }
        Err(error) => {
            if replaced {
                fs::rename(previous, destination)?;
            }
            let _ = fs::remove_file(temporary);
            Err(error.into())
        }
    }
}

pub fn export_text(state: &MaterialState, id: i64, destination: &Path) -> Result<Value> {
    let text = detail(state, id)?
        .text
        .ok_or_else(|| BackendError::new("empty_content", "该资料尚无已保存的解析文本"))?;
    if text.is_empty() {
        return Err(BackendError::new("empty_content", "解析文本为空，未导出"));
    }
    let sha256 = format!("{:x}", Sha256::digest(text.as_bytes()));
    atomic_export(destination, text.as_bytes())?;
    Ok(json!({"bytes": text.len(), "sha256": sha256}))
}

pub fn prepare_reset(state: &MaterialState) -> Result<Option<PathBuf>> {
    state.stop_all()?;
    let root = state.root();
    if !root.exists() {
        return Ok(None);
    }
    let destination =
        root.with_extension(format!("reset-{}", TEMP_ID.fetch_add(1, Ordering::Relaxed)));
    fs::rename(root, &destination)?;
    Ok(Some(destination))
}

pub fn finish_reset(state: &MaterialState, backup: Option<PathBuf>, committed: bool) -> Result<()> {
    let Some(backup) = backup else { return Ok(()) };
    if committed {
        fs::remove_dir_all(backup)
            .map_err(|_| BackendError::new("cleanup_failed", "数据已清空，但旧原件目录清理失败"))?;
    } else {
        fs::rename(backup, &state.root())?;
    }
    Ok(())
}

fn wait_idle(active: &Arc<Mutex<HashMap<i64, Arc<AtomicBool>>>>) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if active.lock().map(|jobs| jobs.is_empty()).unwrap_or(false) {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Err(BackendError::new(
        "job_active",
        "解析任务尚未停止，请稍后重试",
    ))
}

#[cfg(test)]
pub fn wait_for_idle(state: &MaterialState) -> Result<()> {
    wait_idle(&state.active)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::{Listener, Manager};

    fn setup() -> (
        tempfile::TempDir,
        tauri::App<tauri::test::MockRuntime>,
        Arc<Mutex<Vec<Value>>>,
    ) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("app.db");
        let store = Store::new(&path).unwrap();
        let state = MaterialState::new(&path).unwrap();
        let app = tauri::test::mock_builder()
            .manage(state)
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        let events = Arc::new(Mutex::new(Vec::new()));
        let output = Arc::clone(&events);
        app.listen("backend://event", move |event| {
            output
                .lock()
                .unwrap()
                .push(serde_json::from_str(event.payload()).unwrap());
        });
        drop(store);
        (directory, app, events)
    }

    fn text_file(directory: &Path, name: &str, content: &str) -> PathBuf {
        let path = directory.join(name);
        fs::write(&path, content).unwrap();
        path
    }

    #[test]
    fn text_import_uses_real_content_deduplication_and_parsing() {
        let (_directory, app, _) = setup();
        let state = app.state::<MaterialState>();
        let first =
            import_text(app.handle(), &state, "课堂笔记".into(), "先验与后验".into()).unwrap();
        assert!(!first.deduplicated);
        assert!(first.material.original_available);
        let second =
            import_text(app.handle(), &state, "另一标题".into(), "先验与后验".into()).unwrap();
        assert!(second.deduplicated);
        assert_eq!(second.material.id, first.material.id);
        assert_eq!(second.material.title, "课堂笔记");
        assert_eq!(
            import_text(app.handle(), &state, "空白".into(), "  ".into())
                .unwrap_err()
                .code,
            "invalid_text"
        );
        assert_eq!(
            import_text(app.handle(), &state, "空字符".into(), "a\0b".into())
                .unwrap_err()
                .code,
            "invalid_text"
        );
        enqueue(app.handle(), &state, first.material.id).unwrap();
        wait_for_idle(&state).unwrap();
        let detail = detail(&state, first.material.id).unwrap();
        assert_eq!(detail.material.status, "parsed");
        assert_eq!(detail.text.as_deref(), Some("先验与后验"));
        assert_eq!(detail.material.parse_mode, "text-utf8");
    }

    #[test]
    fn parsing_is_bounded_and_queue_drains_after_worker_finishes() {
        let (directory, app, _) = setup();
        let state = app.state::<MaterialState>();
        state.pause.store(true, Ordering::Release);
        let outcomes = import_files(
            app.handle(),
            &state,
            vec![
                text_file(directory.path(), "first.txt", "第一份正文")
                    .to_string_lossy()
                    .into_owned(),
                text_file(directory.path(), "second.txt", "第二份正文")
                    .to_string_lossy()
                    .into_owned(),
                text_file(directory.path(), "third.txt", "第三份正文")
                    .to_string_lossy()
                    .into_owned(),
            ],
        );
        let ids = outcomes
            .iter()
            .map(|outcome| outcome.material.as_ref().unwrap().id)
            .collect::<Vec<_>>();
        for id in &ids {
            enqueue(app.handle(), &state, *id).unwrap();
        }
        assert_eq!(state.active.lock().unwrap().len(), 1);
        assert_eq!(
            cancel(app.handle(), &state, ids[1]).unwrap().status,
            "cancelled"
        );
        state.pause.store(false, Ordering::Release);
        wait_for_idle(&state).unwrap();
        assert_eq!(
            detail(&state, ids[0]).unwrap().text.as_deref(),
            Some("第一份正文")
        );
        assert_eq!(
            detail(&state, ids[2]).unwrap().text.as_deref(),
            Some("第三份正文")
        );
        let cancelled = detail(&state, ids[1]).unwrap();
        assert_eq!(cancelled.material.status, "cancelled");
        assert_eq!(cancelled.text, None);
    }

    #[test]
    fn stopping_a_queue_does_not_start_the_next_job_until_recovery() {
        let (directory, app, _) = setup();
        let state = app.state::<MaterialState>();
        state.pause.store(true, Ordering::Release);
        let outcomes = import_files(
            app.handle(),
            &state,
            vec![
                text_file(directory.path(), "stop-first.txt", "停止第一项")
                    .to_string_lossy()
                    .into_owned(),
                text_file(directory.path(), "stop-second.txt", "等待恢复")
                    .to_string_lossy()
                    .into_owned(),
            ],
        );
        let ids = outcomes
            .iter()
            .map(|outcome| outcome.material.as_ref().unwrap().id)
            .collect::<Vec<_>>();
        for id in &ids {
            enqueue(app.handle(), &state, *id).unwrap();
        }
        let stopping = state.shared();
        let worker = std::thread::spawn(move || stop(&stopping));
        for _ in 0..100 {
            if !state.accepting.load(Ordering::Acquire) {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(!state.accepting.load(Ordering::Acquire));
        state.pause.store(false, Ordering::Release);
        worker.join().unwrap().unwrap();
        assert_eq!(detail(&state, ids[0]).unwrap().material.status, "cancelled");
        assert_eq!(detail(&state, ids[1]).unwrap().material.status, "queued");
        assert_eq!(detail(&state, ids[1]).unwrap().text, None);
        recover(app.handle(), &state).unwrap();
        wait_for_idle(&state).unwrap();
        assert_eq!(
            detail(&state, ids[1]).unwrap().text.as_deref(),
            Some("等待恢复")
        );
    }

    #[test]
    fn ingest_deduplicates_and_parse_survives_reopen() {
        let (directory, app, events) = setup();
        let state = app.state::<MaterialState>();
        let file = text_file(directory.path(), "study.md", "# 先验\n后验概率");
        let outcomes = import_files(
            app.handle(),
            &state,
            vec![file.to_string_lossy().into_owned()],
        );
        assert!(outcomes[0].error.is_none(), "{:?}", outcomes[0].error);
        let id = outcomes[0].material.as_ref().unwrap().id;
        assert!(
            import_files(
                app.handle(),
                &state,
                vec![file.to_string_lossy().into_owned()]
            )[0]
            .deduplicated
        );
        assert_eq!(list(&state).unwrap().len(), 1);
        state.pause.store(true, Ordering::Release);
        enqueue(app.handle(), &state, id).unwrap();
        assert_eq!(
            enqueue(app.handle(), &state, id).unwrap_err().code,
            "job_active"
        );
        state.pause.store(false, Ordering::Release);
        wait_for_idle(&state).unwrap();
        let detail = detail(&state, id).unwrap();
        assert_eq!(
            detail.material.status, "parsed",
            "{:?}",
            detail.material.error
        );
        assert_eq!(detail.text.unwrap(), "# 先验\n后验概率");
        assert_eq!(detail.material.status, "parsed");
        assert_eq!(detail.material.progress, 100);
        assert_eq!(detail.material.text_length, 9);
        drop(state);
        let store = Store::new(directory.path().join("app.db")).unwrap();
        assert_eq!(
            store.collection("resources").unwrap().value[0]["progress"],
            100
        );
        let kinds = events
            .lock()
            .unwrap()
            .iter()
            .map(|event| event["kind"].as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        for expected in [
            "material.staged",
            "material.queued",
            "material.parsing",
            "material.parsed",
        ] {
            assert!(kinds.contains(&expected.to_string()), "{kinds:?}");
        }
        assert!(kinds
            .windows(2)
            .any(|pair| pair == ["material.parsing", "material.parsed"]));
    }

    #[test]
    fn bad_input_does_not_leave_a_completed_record() {
        let (directory, app, events) = setup();
        let state = app.state::<MaterialState>();
        let path = text_file(directory.path(), "bad.pdf", "not a pdf");
        let outcomes = import_files(
            app.handle(),
            &state,
            vec![path.to_string_lossy().into_owned()],
        );
        assert_eq!(outcomes[0].error.as_ref().unwrap().code, "invalid_format");
        assert_eq!(list(&state).unwrap().len(), 0);
        assert!(events.lock().unwrap().is_empty());
    }

    #[test]
    fn cancellation_and_metadata_import_do_not_claim_originals() {
        let (directory, app, _) = setup();
        let state = app.state::<MaterialState>();
        let file = text_file(directory.path(), "study.txt", "正文");
        let result = import_files(
            app.handle(),
            &state,
            vec![file.to_string_lossy().into_owned()],
        );
        let id = result[0].material.as_ref().unwrap().id;
        state.pause.store(true, Ordering::Release);
        enqueue(app.handle(), &state, id).unwrap();
        cancel(app.handle(), &state, id).unwrap();
        state.pause.store(false, Ordering::Release);
        wait_for_idle(&state).unwrap();
        let cancelled = detail(&state, id).unwrap();
        assert_eq!(
            cancelled.material.status, "cancelled",
            "{:?}",
            cancelled.material.error
        );
        let store = state.store().unwrap();
        import_metadata(
            &store.conn,
            &json!({"id": 99, "title": "历史元数据", "type": "文件", "meta": "没有原件"}),
            99,
        )
        .unwrap();
        sync_resources(&store.conn).unwrap();
        let metadata = detail(&state, 99).unwrap();
        assert!(!metadata.material.original_available);
        assert_eq!(
            enqueue(app.handle(), &state, 99).unwrap_err().code,
            "missing_original"
        );
        delete(app.handle(), &state, id).unwrap();
        assert!(detail(&state, id).is_err());
    }

    #[test]
    fn recovery_requeues_only_unfinished_jobs() {
        let (directory, app, events) = setup();
        let state = app.state::<MaterialState>();
        state.defer.store(true, Ordering::Release);
        let resume = import_files(
            app.handle(),
            &state,
            vec![text_file(directory.path(), "resume.txt", "恢复正文")
                .to_string_lossy()
                .into_owned()],
        );
        let cancelled = import_files(
            app.handle(),
            &state,
            vec![text_file(directory.path(), "cancelled.txt", "取消正文")
                .to_string_lossy()
                .into_owned()],
        );
        let resume_id = resume[0].material.as_ref().unwrap().id;
        let cancelled_id = cancelled[0].material.as_ref().unwrap().id;
        enqueue(app.handle(), &state, resume_id).unwrap();
        enqueue(app.handle(), &state, cancelled_id).unwrap();
        let store = state.store().unwrap();
        store
            .conn
            .execute(
                "UPDATE material_jobs SET cancel_requested = 1, status = 'cancelling' WHERE material_id = ?1",
                params![cancelled_id],
            )
            .unwrap();
        drop(store);

        let restarted = MaterialState::new(&directory.path().join("app.db")).unwrap();
        recover(app.handle(), &restarted).unwrap();
        wait_for_idle(&restarted).unwrap();
        assert_eq!(
            detail(&restarted, resume_id).unwrap().text.as_deref(),
            Some("恢复正文")
        );
        let cancelled_detail = detail(&restarted, cancelled_id).unwrap();
        assert_eq!(cancelled_detail.material.status, "cancelled");
        assert_eq!(cancelled_detail.text, None);
        let events = events.lock().unwrap();
        assert_eq!(
            events
                .iter()
                .filter(|event| {
                    event["kind"] == "material.parsed"
                        && event["payload"]["materialId"] == resume_id
                })
                .count(),
            1
        );
        assert!(events.iter().any(|event| {
            event["kind"] == "material.cancelled" && event["payload"]["materialId"] == cancelled_id
        }));
        assert!(!events.iter().any(|event| {
            event["kind"] == "material.parsed" && event["payload"]["materialId"] == cancelled_id
        }));
    }

    #[test]
    fn export_delete_and_reset_cover_original_files() {
        let (directory, app, _) = setup();
        let state = app.state::<MaterialState>();
        let first = import_files(
            app.handle(),
            &state,
            vec![text_file(directory.path(), "first.txt", "第一份正文")
                .to_string_lossy()
                .into_owned()],
        );
        let second = import_files(
            app.handle(),
            &state,
            vec![text_file(directory.path(), "second.txt", "第二份正文")
                .to_string_lossy()
                .into_owned()],
        );
        let first_id = first[0].material.as_ref().unwrap().id;
        let second_id = second[0].material.as_ref().unwrap().id;
        state.pause.store(true, Ordering::Release);
        enqueue(app.handle(), &state, first_id).unwrap();
        enqueue(app.handle(), &state, second_id).unwrap();
        state.pause.store(false, Ordering::Release);
        wait_for_idle(&state).unwrap();
        let output = directory.path().join("exports");
        let original = output.join("original.txt");
        export_original(&state, first_id, &original).unwrap();
        assert_eq!(fs::read_to_string(&original).unwrap(), "第一份正文");
        let text = output.join("parsed.txt");
        export_text(&state, first_id, &text).unwrap();
        fs::write(&text, "旧导出").unwrap();
        export_text(&state, first_id, &text).unwrap();
        assert_eq!(fs::read_to_string(&text).unwrap(), "第一份正文");
        let first_material = detail(&state, first_id).unwrap().material;
        let original = original_path(&state.root(), &first_material.sha256, "txt");
        assert!(original.is_file());
        delete(app.handle(), &state, first_id).unwrap();
        assert!(!original.exists());

        let reset_app = tauri::test::mock_builder()
            .manage(crate::backend::BackendState {
                store: Mutex::new(state.store().unwrap()),
                materials: MaterialState::new(&directory.path().join("app.db")).unwrap(),
                providers: crate::backend::providers::ProviderState::for_tests().unwrap(),
                learning: crate::backend::learning::LearningState::for_tests().unwrap(),
                agent: crate::backend::agent::AgentState::for_tests().unwrap(),
                practice: crate::backend::practice::PracticeState::for_tests().unwrap(),
                review: crate::backend::review::ReviewState::for_tests().unwrap(),
                knowledge: crate::backend::knowledge::KnowledgeState::for_tests().unwrap(),
                community: crate::backend::community::CommunityState::for_tests().unwrap(),
                plugins: crate::backend::plugins::PluginState::for_tests().unwrap(),
                sharing: crate::backend::sharing::SharingState::for_tests().unwrap(),
            })
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        let state_json = crate::backend::commands::reset_application(
            reset_app.handle().clone(),
            reset_app.state(),
        )
        .unwrap();
        assert_eq!(state_json["resources"], json!([]));
        assert!(
            list(&reset_app.state::<crate::backend::BackendState>().materials)
                .unwrap()
                .is_empty()
        );
        assert!(!reset_app
            .state::<crate::backend::BackendState>()
            .materials
            .root()
            .exists());
        let second_material = detail(&state, second_id).unwrap_err();
        assert_eq!(second_material.code, "not_found");
    }

    #[test]
    fn concurrent_store_initialization_does_not_fail() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("app.db");
        let barrier = Arc::new(std::sync::Barrier::new(9));
        std::thread::scope(|scope| {
            let mut workers = Vec::new();
            for _ in 0..8 {
                let path = path.clone();
                let barrier = Arc::clone(&barrier);
                workers.push(scope.spawn(move || {
                    barrier.wait();
                    let store = Store::new(path)?;
                    store.app_state()?;
                    Ok::<(), BackendError>(())
                }));
            }
            barrier.wait();
            for worker in workers {
                worker.join().unwrap().unwrap();
            }
        });
    }
}
