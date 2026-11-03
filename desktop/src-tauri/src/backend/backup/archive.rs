use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::learning::LearningState;
use super::super::store::Store;
use super::super::{materials, plugins};
use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use zip::ZipArchive;

pub(crate) const MAX_BACKUP_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_DATABASE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_FILE_BYTES: u64 = 50 * 1024 * 1024;
const MAX_MANIFEST_BYTES: usize = 1024 * 1024;
static TEMP_ID: AtomicU64 = AtomicU64::new(0);

pub(crate) const TABLES: &[&str] = &[
    "kv",
    "meta",
    "revisions",
    "events",
    "materials",
    "material_text",
    "material_jobs",
    "provider_profiles",
    "provider_meta",
    "provider_requests",
    "provider_route_runs",
    "provider_route_attempts",
    "provider_circuits",
    "usage_rates",
    "learning_plans",
    "learning_plan_steps",
    "learning_plan_materials",
    "learning_plan_adoptions",
    "learning_plan_schedule_tasks",
    "learning_plan_versions",
    "learning_node_evidence",
    "learning_reading_progress",
    "learning_node_tasks",
    "learning_lectures",
    "learning_lecture_units",
    "learning_lecture_versions",
    "learning_annotations",
    "practice_sets",
    "practice_questions",
    "practice_answers",
    "practice_mistakes",
    "practice_bookmarks",
    "review_cards",
    "review_history",
    "knowledge_sessions",
    "knowledge_session_materials",
    "agent_sessions",
    "agent_runs",
    "agent_messages",
    "agent_tool_calls",
    "agent_artifacts",
    "agent_approvals",
    "agent_memory",
    "community_posts",
    "community_replies",
    "community_reactions",
    "community_groups",
    "community_group_members",
    "community_rooms",
    "community_reservations",
    "community_reminders",
    "shared_plans",
    "plugins",
    "plugin_subscriptions",
    "plugin_deliveries",
    "plugin_reloads",
    "sync_config",
    "sync_jobs",
    "local_users",
    "auth_sessions",
    "access_tokens",
    "security_quotas",
    "share_index",
    "focus_sessions",
    "study_checkins",
    "notifications",
];

pub(crate) const EXCLUDED_AUTH_TABLES: &[&str] = &["auth_sessions", "access_tokens", "share_index"];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct FileEntry {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Manifest {
    pub format: String,
    pub version: u32,
    pub created_at: String,
    pub tables: BTreeMap<String, u64>,
    pub database: FileEntry,
    pub files: Vec<FileEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackupResult {
    pub bytes: u64,
    pub sha256: String,
    pub files: usize,
    pub records: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackupPreview {
    pub format: String,
    pub version: u32,
    pub created_at: String,
    pub table_counts: BTreeMap<String, u64>,
    pub collection_counts: BTreeMap<String, u64>,
    pub files: usize,
    pub file_bytes: u64,
    pub database_bytes: u64,
    pub append_only: bool,
}

pub(crate) struct Workspace {
    path: PathBuf,
}

impl Workspace {
    fn new(database_path: &Path) -> Result<Self> {
        let root = database_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("backup-work");
        fs::create_dir_all(&root)?;
        for _ in 0..16 {
            let path = root.join(format!(
                "{}-{}",
                std::process::id(),
                TEMP_ID.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::create_dir(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(BackendError::new(
            "backup_workspace_failed",
            "无法创建独立备份工作区",
        ))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        if let Err(error) = fs::remove_dir_all(&self.path) {
            eprintln!("backup workspace cleanup failed: {error}");
        }
    }
}

fn relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if path.as_os_str().is_empty()
        || path.components().count() > 12
        || !path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
    {
        return Err(BackendError::invalid(
            "invalid_backup_path",
            "备份包含不安全的相对路径",
        ));
    }
    Ok(path.to_path_buf())
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn hash_file(path: &Path) -> Result<(String, u64)> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = [0_u8; 65536];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        total += read as u64;
        if total > MAX_FILE_BYTES {
            return Err(BackendError::new(
                "backup_file_too_large",
                "单个备份文件超过 50 MB 上限",
            ));
        }
        hash.update(&buffer[..read]);
    }
    Ok((format!("{:x}", hash.finalize()), total))
}

fn file_entry(path: String, source: &Path) -> Result<FileEntry> {
    relative_path(&path)?;
    let (sha256, bytes) = hash_file(source)?;
    Ok(FileEntry {
        path,
        sha256,
        bytes,
    })
}

fn collect_files(state: &LearningState) -> Result<Vec<FileEntry>> {
    let root = state
        .workspace_path()
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let store = state.store()?;
    let mut entries = Vec::new();
    let mut statement = store
        .conn
        .prepare("SELECT sha256, kind FROM materials WHERE sha256 IS NOT NULL ORDER BY id")?;
    let materials = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (sha256, kind) in materials {
        if !valid_hash(&sha256)
            || kind.is_empty()
            || kind.len() > 12
            || !kind.bytes().all(|byte| byte.is_ascii_alphanumeric())
        {
            return Err(BackendError::new(
                "invalid_material_path",
                "资料原件路径不符合应用约定",
            ));
        }
        let kind = kind.to_ascii_lowercase();
        let relative = format!("materials/originals/{sha256}.{kind}");
        entries.push(file_entry(
            relative,
            &root
                .join("materials/originals")
                .join(format!("{sha256}.{kind}")),
        )?);
    }
    let mut statement = store.conn.prepare("SELECT id FROM plugins ORDER BY id")?;
    let ids = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for id in ids {
        let relative = format!("plugins/{id}/manifest.json");
        let content = fs::read_to_string(root.join(&relative))?;
        plugins::validate_manifest_content(&content)?;
        let manifest: Value = serde_json::from_str(&content)?;
        if manifest.get("id").and_then(Value::as_str) != Some(&id) {
            return Err(BackendError::new(
                "plugin_manifest_mismatch",
                "插件清单标识与记录不一致",
            ));
        }
        entries.push(file_entry(
            relative,
            &root.join(format!("plugins/{id}/manifest.json")),
        )?);
    }
    let mut statement = store
        .conn
        .prepare("SELECT session_id, path FROM agent_artifacts ORDER BY id")?;
    let artifacts = statement
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (session, path) in artifacts {
        let relative = relative_path(&path)?;
        if !matches!(
            relative.extension().and_then(|value| value.to_str()),
            Some("json" | "md" | "txt")
        ) {
            return Err(BackendError::invalid(
                "invalid_artifact_path",
                "备份产物类型不受支持",
            ));
        }
        let directory = format!("agent-workspaces/{session}/scratch");
        entries.push(file_entry(
            format!("{directory}/{path}"),
            &root.join(directory).join(relative),
        )?);
    }
    let unique = entries
        .iter()
        .map(|entry| entry.path.clone())
        .collect::<BTreeSet<_>>();
    if unique.len() != entries.len() {
        return Err(BackendError::new(
            "duplicate_backup_path",
            "备份存在重复文件路径",
        ));
    }
    Ok(entries)
}

fn write_archive_file(
    writer: &mut zip::ZipWriter<impl std::io::Write + std::io::Seek>,
    path: &str,
    bytes: &[u8],
) -> Result<()> {
    writer.start_file(
        path,
        zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated),
    )?;
    writer.write_all(bytes)?;
    Ok(())
}

pub fn export<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    state: &LearningState,
    destination: &Path,
) -> Result<BackupResult> {
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("dtbackup"))
    {
        return Err(BackendError::invalid(
            "invalid_backup_destination",
            "全量备份必须使用 .dtbackup 扩展名",
        ));
    }
    let workspace = Workspace::new(&state.workspace_path())?;
    let snapshot = workspace.path.join("state.db");
    let path = snapshot.to_string_lossy().to_string();
    store.conn.execute("VACUUM INTO ?1", params![path])?;
    {
        let snapshot_connection = Connection::open(&snapshot)?;
        if let Some(user_id) = super::super::security::principal_id() {
            let accounts = Connection::open(state.database_path())?;
            let (name, role, active, created_at, updated_at, last_login_at): (String, String, bool, String, String, Option<String>) = accounts.query_row(
                "SELECT display_name, role, active, created_at, updated_at, last_login_at FROM local_users WHERE id = ?1 AND deleted_at IS NULL",
                params![user_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?)),
            )?;
            snapshot_connection.execute(
                "INSERT OR REPLACE INTO local_users (id, display_name, role, active, created_at, updated_at, last_login_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![user_id, name, role, active, created_at, updated_at, last_login_at],
            )?;
            let mut statement = accounts
                .prepare("SELECT resource, item_limit FROM security_quotas WHERE user_id = ?1")?;
            let quotas = statement
                .query_map(params![user_id], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
                })?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            drop(statement);
            for (resource, limit) in quotas {
                snapshot_connection.execute("INSERT OR REPLACE INTO security_quotas (user_id, resource, item_limit) VALUES (?1, ?2, ?3)", params![user_id, resource, limit])?;
            }
        }
        snapshot_connection.execute_batch(
            "PRAGMA secure_delete = ON;
             DELETE FROM auth_sessions;
             DELETE FROM access_tokens;
             DELETE FROM share_index;
             VACUUM;",
        )?;
    }
    let database_bytes = fs::read(&snapshot)?;
    if database_bytes.len() as u64 > MAX_DATABASE_BYTES {
        return Err(BackendError::new(
            "backup_database_too_large",
            "数据库快照超过 512 MB 上限",
        ));
    }
    let database = FileEntry {
        path: "state.db".into(),
        sha256: format!("{:x}", Sha256::digest(&database_bytes)),
        bytes: database_bytes.len() as u64,
    };
    let files = collect_files(state)?;
    let snapshot = Connection::open_with_flags(&snapshot, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut tables = BTreeMap::new();
    for table in TABLES {
        let count = snapshot.query_row(&format!("SELECT COUNT(*) FROM \"{table}\""), [], |row| {
            row.get::<_, i64>(0)
        })? as u64;
        tables.insert((*table).into(), count);
    }
    let manifest = Manifest {
        format: "deep-tutor-backup".into(),
        version: 3,
        created_at: OffsetDateTime::now_utc().format(&Rfc3339)?,
        tables,
        database,
        files,
    };
    let mut bytes = Vec::new();
    {
        let mut writer = zip::ZipWriter::new(std::io::Cursor::new(&mut bytes));
        write_archive_file(
            &mut writer,
            "manifest.json",
            serde_json::to_string(&manifest)?.as_bytes(),
        )?;
        write_archive_file(&mut writer, "state.db", &database_bytes)?;
        for file in &manifest.files {
            let source = state
                .workspace_path()
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .join(&file.path);
            let bytes = fs::read(&source)?;
            if bytes.len() as u64 != file.bytes
                || format!("{:x}", Sha256::digest(&bytes)) != file.sha256
            {
                return Err(BackendError::new(
                    "backup_file_changed",
                    "备份期间原件发生变化，未生成不完整备份",
                ));
            }
            write_archive_file(&mut writer, &format!("files/{}", file.path), &bytes)?;
        }
        writer.finish()?;
    }
    if bytes.len() as u64 > MAX_BACKUP_BYTES {
        return Err(BackendError::new("backup_too_large", "备份超过 2 GB 上限"));
    }
    materials::atomic_export(destination, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let event = Store::event(
        &store.conn,
        "backup.exported",
        json!({"format": "deep-tutor-backup", "version": 3, "bytes": bytes.len(), "files": manifest.files.len(), "sha256": sha256}),
    )?;
    emit(app, &event);
    Ok(BackupResult {
        bytes: bytes.len() as u64,
        sha256,
        files: manifest.files.len(),
        records: manifest.tables.values().sum(),
    })
}

pub(crate) struct OpenBackup {
    pub manifest: Manifest,
    pub database: PathBuf,
    pub workspace: Workspace,
    archive: ZipArchive<fs::File>,
}

fn read_bounded(reader: &mut impl Read, limit: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    let mut limited = reader.take(limit + 1);
    limited.read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(BackendError::new(
            "backup_entry_too_large",
            "备份条目超过允许大小",
        ));
    }
    Ok(bytes)
}

pub(crate) fn open(path: &Path, state: &LearningState) -> Result<OpenBackup> {
    if !path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("dtbackup"))
    {
        return Err(BackendError::invalid(
            "invalid_backup_file",
            "请选择 .dtbackup 全量备份",
        ));
    }
    let size = fs::metadata(path)?.len();
    if size > MAX_BACKUP_BYTES {
        return Err(BackendError::new("backup_too_large", "备份超过 2 GB 上限"));
    }
    let mut archive = ZipArchive::new(fs::File::open(path)?)?;
    if archive.len() < 2 || archive.len() > 20_002 {
        return Err(BackendError::invalid(
            "invalid_backup",
            "备份归档条目数量无效",
        ));
    }
    let manifest: Manifest = serde_json::from_slice(&read_bounded(
        &mut archive.by_name("manifest.json")?,
        MAX_MANIFEST_BYTES as u64,
    )?)
    .map_err(|_| BackendError::invalid("invalid_backup_manifest", "备份清单格式无效"))?;
    if manifest.format != "deep-tutor-backup" || manifest.version != 3 {
        return Err(BackendError::invalid(
            "unsupported_backup",
            "仅支持第三版 Deep Tutor 全量备份",
        ));
    }
    if manifest.database.path != "state.db"
        || manifest.database.bytes > MAX_DATABASE_BYTES
        || !valid_hash(&manifest.database.sha256)
        || manifest
            .tables
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
            != TABLES.iter().copied().collect()
    {
        return Err(BackendError::invalid(
            "invalid_backup_manifest",
            "备份数据库或表清单无效",
        ));
    }
    let workspace = Workspace::new(&state.workspace_path())?;
    let bytes = read_bounded(&mut archive.by_name("state.db")?, MAX_DATABASE_BYTES)?;
    if bytes.len() as u64 != manifest.database.bytes
        || format!("{:x}", Sha256::digest(&bytes)) != manifest.database.sha256
    {
        return Err(BackendError::new(
            "backup_checksum_mismatch",
            "备份数据库校验失败",
        ));
    }
    fs::write(workspace.path.join("state.db"), bytes)?;
    let connection = Connection::open_with_flags(
        workspace.path.join("state.db"),
        OpenFlags::SQLITE_OPEN_READ_ONLY,
    )?;
    connection.execute("PRAGMA query_only = ON", [])?;
    let integrity: String = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    if integrity != "ok" {
        return Err(BackendError::new(
            "invalid_backup_database",
            "备份数据库完整性检查失败",
        ));
    }
    let foreign_errors =
        connection.query_row("SELECT COUNT(*) FROM pragma_foreign_key_check", [], |row| {
            row.get::<_, i64>(0)
        })?;
    if foreign_errors != 0 {
        return Err(BackendError::new(
            "invalid_backup_references",
            "备份包含失效的外键引用",
        ));
    }
    let mut statement = connection.prepare(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )?;
    let names = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if names.iter().any(|name| !TABLES.contains(&name.as_str())) {
        return Err(BackendError::invalid(
            "unsupported_backup_schema",
            "备份包含不支持的数据表",
        ));
    }
    for table in TABLES {
        let count =
            connection.query_row(&format!("SELECT COUNT(*) FROM \"{table}\""), [], |row| {
                row.get::<_, i64>(0)
            })? as u64;
        if manifest.tables.get(*table) != Some(&count)
            && !(manifest.tables.get(*table).is_none()
                && [
                    "local_users",
                    "auth_sessions",
                    "access_tokens",
                    "security_quotas",
                    "share_index",
                    "focus_sessions",
                    "study_checkins",
                    "notifications",
                ]
                .contains(table))
        {
            return Err(BackendError::new(
                "backup_manifest_mismatch",
                "备份表计数与数据库不一致",
            ));
        }
    }
    let mut paths = BTreeSet::new();
    for file in &manifest.files {
        relative_path(&file.path)?;
        if !valid_hash(&file.sha256) || file.bytes > MAX_FILE_BYTES || !paths.insert(&file.path) {
            return Err(BackendError::invalid(
                "invalid_backup_file_entry",
                "备份文件路径、大小或哈希无效",
            ));
        }
        let bytes = read_bounded(
            &mut archive.by_name(&format!("files/{}", file.path))?,
            MAX_FILE_BYTES,
        )?;
        if bytes.len() as u64 != file.bytes
            || format!("{:x}", Sha256::digest(&bytes)) != file.sha256
        {
            return Err(BackendError::new(
                "backup_checksum_mismatch",
                "备份原件内容校验失败",
            ));
        }
    }
    Ok(OpenBackup {
        manifest,
        database: workspace.path.join("state.db"),
        workspace,
        archive,
    })
}

impl OpenBackup {
    pub fn preview(&self) -> Result<BackupPreview> {
        let connection =
            Connection::open_with_flags(&self.database, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let mut statement =
            connection.prepare("SELECT namespace, COUNT(*) FROM kv GROUP BY namespace")?;
        let collections = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)? as u64))
            })?
            .collect::<std::result::Result<BTreeMap<_, _>, _>>()?;
        Ok(BackupPreview {
            format: self.manifest.format.clone(),
            version: self.manifest.version,
            created_at: self.manifest.created_at.clone(),
            table_counts: self.manifest.tables.clone(),
            collection_counts: collections,
            files: self.manifest.files.len(),
            file_bytes: self.manifest.files.iter().map(|file| file.bytes).sum(),
            database_bytes: self.manifest.database.bytes,
            append_only: true,
        })
    }

    pub fn read_file(&mut self, entry: &FileEntry) -> Result<Vec<u8>> {
        let bytes = read_bounded(
            &mut self.archive.by_name(&format!("files/{}", entry.path))?,
            MAX_FILE_BYTES,
        )?;
        if bytes.len() as u64 != entry.bytes
            || format!("{:x}", Sha256::digest(&bytes)) != entry.sha256
        {
            return Err(BackendError::new(
                "backup_checksum_mismatch",
                "备份文件内容校验失败",
            ));
        }
        Ok(bytes)
    }
}
