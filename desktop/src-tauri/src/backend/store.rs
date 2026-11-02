use super::error::{BackendError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::fs;
#[cfg(test)]
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const COLLECTIONS: [&str; 4] = ["tasks", "notes", "resources", "projects"];
const MAX_RECORD_BYTES: usize = 1024 * 1024;
const MAX_BACKUP_BYTES: usize = 256 * 1024 * 1024;
static STORE_OPEN_LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug, Clone, Serialize)]
pub struct BackendEvent {
    pub id: i64,
    pub kind: String,
    pub payload: Value,
    #[serde(rename = "createdAt")]
    pub created_at: String,
}

#[derive(Debug, Serialize)]
pub struct Mutation {
    pub state: Value,
    pub event: Option<BackendEvent>,
}

#[derive(Debug, Serialize)]
pub struct Collection {
    pub value: Value,
    pub revision: i64,
}

#[derive(Debug, Serialize)]
pub struct EventPage {
    pub events: Vec<BackendEvent>,
    #[serde(rename = "latestId")]
    pub latest_id: i64,
    #[serde(rename = "hasMore")]
    pub has_more: bool,
}

pub struct Store {
    pub(crate) conn: Connection,
}

impl Store {
    pub fn new(path: impl Into<PathBuf>) -> Result<Self> {
        let path = path.into();
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        let _open = STORE_OPEN_LOCK
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "数据库初始化锁不可用"))?;
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "busy_timeout", 10000)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS kv (
                namespace TEXT NOT NULL,
                id TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, id)
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions (
                namespace TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );",
        )?;
        super::materials::migrate(&conn)?;
        Ok(Self { conn })
    }

    #[cfg(test)]
    pub fn in_memory() -> Result<Self> {
        Self::new(":memory:")
    }

    #[cfg(test)]
    pub fn at(path: &Path) -> Result<Self> {
        Self::new(path)
    }

    fn now() -> Result<String> {
        Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
    }

    fn namespace_allowed(namespace: &str) -> bool {
        COLLECTIONS.contains(&namespace) || namespace == "preferences"
    }

    fn defaults() -> Value {
        json!({
            "proactive": true,
            "hintsOnly": true,
            "notifications": true,
            "weeklyReport": true,
            "highContrast": false,
            "reducedMotion": false,
            "largeText": false,
            "theme": "light"
        })
    }

    pub fn bootstrap(&self) -> Result<Value> {
        let seeded: Option<String> = self
            .conn
            .query_row("SELECT value FROM meta WHERE key = 'seeded'", [], |row| {
                row.get(0)
            })
            .optional()?;
        if seeded.is_none() {
            let tx = self.conn.unchecked_transaction()?;
            Self::insert_json(&tx, "preferences", "current", &Self::defaults())?;
            tx.execute("INSERT INTO meta (key, value) VALUES ('seeded', '1')", [])?;
            tx.commit()?;
        }
        self.app_state()
    }

    fn revision(conn: &Connection, namespace: &str) -> Result<i64> {
        Ok(conn
            .query_row(
                "SELECT value FROM revisions WHERE namespace = ?1",
                params![namespace],
                |row| row.get(0),
            )
            .optional()?
            .unwrap_or(0))
    }

    pub(crate) fn next_revision(conn: &Connection, namespace: &str) -> Result<i64> {
        let revision = Self::revision(conn, namespace)? + 1;
        conn.execute(
            "INSERT INTO revisions (namespace, value) VALUES (?1, ?2)
             ON CONFLICT(namespace) DO UPDATE SET value = excluded.value",
            params![namespace, revision],
        )?;
        Ok(revision)
    }

    pub(crate) fn insert_json(
        conn: &Connection,
        namespace: &str,
        id: &str,
        value: &Value,
    ) -> Result<()> {
        let data = serde_json::to_string(value)?;
        conn.execute(
            "INSERT INTO kv (namespace, id, data, updated_at) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(namespace, id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            params![namespace, id, data, Self::now()?],
        )?;
        Ok(())
    }

    pub fn collection(&self, namespace: &str) -> Result<Collection> {
        if namespace == "drafts" {
            return Err(BackendError::invalid(
                "invalid_collection",
                "草稿须按标识读取，不能作为普通集合读取",
            ));
        }
        if !Self::namespace_allowed(namespace) {
            return Err(BackendError::invalid(
                "unknown_collection",
                "不支持的数据集合",
            ));
        }
        let mut stmt = self
            .conn
            .prepare("SELECT data FROM kv WHERE namespace = ?1 ORDER BY rowid")?;
        let rows = stmt.query_map(params![namespace], |row| row.get::<_, String>(0))?;
        let mut items = Vec::new();
        for row in rows {
            items.push(serde_json::from_str::<Value>(&row?)?);
        }
        let value = if namespace == "preferences" {
            items.into_iter().next().unwrap_or_else(Self::defaults)
        } else {
            Value::Array(items)
        };
        Ok(Collection {
            value,
            revision: Self::revision(&self.conn, namespace)?,
        })
    }

    pub fn app_state(&self) -> Result<Value> {
        let mut state = Map::new();
        let mut versions = Map::new();
        for namespace in COLLECTIONS {
            let collection = self.collection(namespace)?;
            state.insert(namespace.to_string(), collection.value);
            versions.insert(namespace.to_string(), Value::from(collection.revision));
        }
        for namespace in ["preferences", "drafts"] {
            versions.insert(
                namespace.to_string(),
                Value::from(Self::revision(&self.conn, namespace)?),
            );
        }
        state.insert(
            "preferences".to_string(),
            self.collection("preferences")?.value,
        );
        state.insert("versions".to_string(), Value::Object(versions));
        state.insert(
            "lastEventId".to_string(),
            Value::from(self.latest_event_id()?),
        );
        Ok(Value::Object(state))
    }

    pub(crate) fn event(conn: &Connection, kind: &str, payload: Value) -> Result<BackendEvent> {
        conn.execute(
            "INSERT INTO events (kind, payload, created_at) VALUES (?1, ?2, ?3)",
            params![kind, serde_json::to_string(&payload)?, Self::now()?],
        )?;
        Ok(BackendEvent {
            id: conn.last_insert_rowid(),
            kind: kind.to_string(),
            payload,
            created_at: Self::now()?,
        })
    }

    fn finish_write(
        &self,
        tx: rusqlite::Transaction<'_>,
        event: Option<BackendEvent>,
    ) -> Result<Mutation> {
        tx.commit()?;
        Ok(Mutation {
            state: self.app_state()?,
            event,
        })
    }

    pub fn replace_collection(
        &self,
        namespace: &str,
        expected_revision: i64,
        value: &Value,
    ) -> Result<Mutation> {
        if namespace == "resources" {
            return Err(BackendError::new(
                "use_material_commands",
                "资料原件、解析和删除必须使用原生材料命令",
            ));
        }
        if !Self::namespace_allowed(namespace) {
            return Err(BackendError::invalid(
                "unknown_collection",
                "不支持的数据集合",
            ));
        }
        if namespace == "preferences" {
            validate_preferences(value)?;
        } else {
            validate_collection(namespace, value)?;
        }
        let tx = self.conn.unchecked_transaction()?;
        let revision = Self::next_revision(&tx, namespace)?;
        if revision - 1 != expected_revision {
            return Err(BackendError::new(
                "revision_conflict",
                "数据已在其他操作中更新，请刷新后重试",
            ));
        }
        tx.execute("DELETE FROM kv WHERE namespace = ?1", params![namespace])?;
        if namespace == "preferences" {
            Self::insert_json(&tx, namespace, "current", value)?;
        } else {
            for item in value.as_array().ok_or_else(|| {
                BackendError::invalid("collection_must_be_array", "集合必须是数组")
            })? {
                Self::insert_json(&tx, namespace, &record_id(item)?, item)?;
            }
        }
        let event = Self::event(
            &tx,
            "state.changed",
            json!({"namespace": namespace, "revision": revision}),
        )?;
        self.finish_write(tx, Some(event))
    }

    fn validate_draft_id(id: &str) -> Result<()> {
        let suffix = id
            .strip_prefix("deep-tutor-draft-")
            .ok_or_else(|| BackendError::invalid("invalid_draft_id", "草稿标识不符合应用约定"))?;
        if (suffix == "new" || suffix.parse::<u64>().is_ok_and(|id| id > 0)) && id.len() <= 128 {
            Ok(())
        } else {
            Err(BackendError::invalid(
                "invalid_draft_id",
                "草稿标识不符合应用约定",
            ))
        }
    }

    pub fn draft(&self, id: &str) -> Result<Collection> {
        Self::validate_draft_id(id)?;
        let data: Option<String> = self
            .conn
            .query_row(
                "SELECT data FROM kv WHERE namespace = 'drafts' AND id = ?1",
                params![id],
                |row| row.get(0),
            )
            .optional()?;
        Ok(Collection {
            value: match data {
                Some(data) => serde_json::from_str(&data)?,
                None => Value::Null,
            },
            revision: Self::revision(&self.conn, "drafts")?,
        })
    }

    pub fn put_draft(&self, id: &str, value: &Value) -> Result<Mutation> {
        Self::validate_draft_id(id)?;
        validate_draft(value)?;
        let tx = self.conn.unchecked_transaction()?;
        let revision = Self::next_revision(&tx, "drafts")?;
        Self::insert_json(&tx, "drafts", id, value)?;
        let event = Self::event(&tx, "draft.saved", json!({"id": id, "revision": revision}))?;
        self.finish_write(tx, Some(event))
    }

    pub fn delete_draft(&self, id: &str) -> Result<Mutation> {
        Self::validate_draft_id(id)?;
        let tx = self.conn.unchecked_transaction()?;
        let deleted = tx.execute(
            "DELETE FROM kv WHERE namespace = 'drafts' AND id = ?1",
            params![id],
        )?;
        let event = if deleted > 0 {
            let revision = Self::next_revision(&tx, "drafts")?;
            Some(Self::event(
                &tx,
                "draft.deleted",
                json!({"id": id, "revision": revision}),
            )?)
        } else {
            None
        };
        self.finish_write(tx, event)
    }

    pub fn reset(&self) -> Result<Mutation> {
        let tx = self.conn.unchecked_transaction()?;
        super::materials::clear_all(&tx)?;
        tx.execute(
            "DELETE FROM kv WHERE namespace IN ('tasks', 'notes', 'resources', 'projects', 'preferences', 'drafts')",
            [],
        )?;
        Self::insert_json(&tx, "preferences", "current", &Self::defaults())?;
        let mut revisions = Map::new();
        for namespace in [
            "tasks",
            "notes",
            "resources",
            "projects",
            "preferences",
            "drafts",
        ] {
            revisions.insert(
                namespace.to_string(),
                Value::from(Self::next_revision(&tx, namespace)?),
            );
        }
        let event = Self::event(
            &tx,
            "application.reset",
            json!({"versions": Value::Object(revisions)}),
        )?;
        self.finish_write(tx, Some(event))
    }

    pub fn export_backup(&self) -> Result<Value> {
        let mut backup = self.app_state()?;
        let object = backup
            .as_object_mut()
            .ok_or_else(|| BackendError::new("storage_failed", "无法读取本地状态"))?;
        object.remove("lastEventId");
        object.remove("versions");
        let drafts = self.drafts()?;
        object.insert("drafts".to_string(), Value::Array(drafts));
        object.insert(
            "format".to_string(),
            Value::String("deep-tutor-backup".into()),
        );
        object.insert("version".to_string(), Value::from(2));
        object.insert("exportedAt".to_string(), Value::String(Self::now()?));
        Ok(backup)
    }

    fn drafts(&self) -> Result<Vec<Value>> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, data FROM kv WHERE namespace = 'drafts' ORDER BY id")?;
        let rows = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        let mut drafts = Vec::new();
        for row in rows {
            let (id, data) = row?;
            let mut draft = serde_json::from_str::<Value>(&data)?;
            draft["id"] = Value::String(id);
            drafts.push(draft);
        }
        Ok(drafts)
    }

    fn validate_backup(backup: &Value) -> Result<&Map<String, Value>> {
        if serde_json::to_vec(backup)?.len() > MAX_BACKUP_BYTES {
            return Err(BackendError::new(
                "backup_too_large",
                "备份超过 256 MB 上限",
            ));
        }
        let object = backup
            .as_object()
            .ok_or_else(|| BackendError::invalid("invalid_backup", "备份必须是 JSON 对象"))?;
        if object.get("format").and_then(Value::as_str) != Some("deep-tutor-backup")
            || object.get("version").and_then(Value::as_i64) != Some(2)
        {
            return Err(BackendError::invalid(
                "unsupported_backup",
                "仅支持第二版 Deep-tutor 完整备份",
            ));
        }
        for key in object.keys() {
            if !["format", "version", "exportedAt", "preferences", "drafts"]
                .into_iter()
                .chain(COLLECTIONS)
                .any(|allowed| allowed == key)
            {
                return Err(BackendError::invalid(
                    "invalid_backup_field",
                    "备份包含未知字段",
                ));
            }
        }
        for namespace in COLLECTIONS {
            validate_collection(
                namespace,
                object
                    .get(namespace)
                    .ok_or_else(|| BackendError::invalid("incomplete_backup", "备份缺少集合"))?,
            )?;
        }
        validate_preferences(
            object
                .get("preferences")
                .ok_or_else(|| BackendError::invalid("incomplete_backup", "备份缺少偏好"))?,
        )?;
        let drafts = object
            .get("drafts")
            .and_then(Value::as_array)
            .ok_or_else(|| BackendError::invalid("incomplete_backup", "备份缺少草稿集合"))?;
        if drafts.len() > 10_000 {
            return Err(BackendError::new("quota_exceeded", "备份草稿数量超过上限"));
        }
        let mut ids = BTreeSet::new();
        for draft in drafts {
            validate_draft(draft)?;
            let id = draft
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| BackendError::invalid("invalid_draft_id", "草稿缺少标识"))?;
            Self::validate_draft_id(id)?;
            if !ids.insert(id.to_string()) {
                return Err(BackendError::invalid("duplicate_id", "备份草稿标识重复"));
            }
        }
        Ok(object)
    }

    pub fn preview_backup(&self, backup: &Value) -> Result<Value> {
        let object = Self::validate_backup(backup)?;
        let mut counts = Map::new();
        for key in COLLECTIONS.into_iter().chain(["drafts"]) {
            counts.insert(
                key.to_string(),
                Value::from(object[key].as_array().map_or(0, Vec::len)),
            );
        }
        Ok(json!({
            "counts": counts,
            "appendOnly": true,
            "format": "deep-tutor-backup",
            "version": 2
        }))
    }

    pub fn import_backup(&self, backup: &Value) -> Result<Mutation> {
        let object = Self::validate_backup(backup)?;
        let tx = self.conn.unchecked_transaction()?;
        let mut imported = Map::new();
        for namespace in COLLECTIONS {
            let items = object[namespace].as_array().ok_or_else(|| {
                BackendError::invalid("collection_must_be_array", "集合必须是数组")
            })?;
            for item in items {
                let original_id = record_id(item)?;
                let mut id = original_id.clone();
                let mut imported_item = item.clone();
                if Self::record_exists(&tx, namespace, &id)? {
                    id = Self::next_free_id(&tx, namespace)?;
                    let numeric_id = id
                        .parse::<i64>()
                        .map_err(|_| BackendError::new("storage_failed", "无法分配记录标识"))?;
                    imported_item["id"] = Value::from(numeric_id);
                }
                if namespace == "resources" {
                    let imported_id = imported_item["id"].as_i64().ok_or_else(|| {
                        BackendError::invalid("invalid_id", "资料标识必须是正整数")
                    })?;
                    super::materials::import_metadata(&tx, &imported_item, imported_id)?;
                } else {
                    Self::insert_json(&tx, namespace, &id, &imported_item)?;
                }
                Self::next_revision(&tx, namespace)?;
            }
            imported.insert(namespace.to_string(), Value::from(items.len()));
        }
        super::materials::sync_resources(&tx)?;
        let drafts = object["drafts"].as_array().ok_or_else(|| {
            BackendError::invalid("collection_must_be_array", "草稿集合必须是数组")
        })?;
        for draft in drafts {
            let original_id = draft
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| BackendError::invalid("invalid_draft_id", "草稿缺少标识"))?;
            let mut id = original_id.to_string();
            let mut imported_draft = draft.clone();
            if Self::record_exists(&tx, "drafts", &id)? {
                id = format!("deep-tutor-draft-{}", Self::next_free_id(&tx, "drafts")?);
                imported_draft["id"] = Value::String(id.clone());
            }
            Self::insert_json(&tx, "drafts", &id, &imported_draft)?;
            Self::next_revision(&tx, "drafts")?;
        }
        imported.insert("drafts".to_string(), Value::from(drafts.len()));
        let event = Self::event(&tx, "backup.imported", json!({"counts": imported}))?;
        self.finish_write(tx, Some(event))
    }

    fn record_exists(conn: &Connection, namespace: &str, id: &str) -> Result<bool> {
        Ok(conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM kv WHERE namespace = ?1 AND id = ?2)",
            params![namespace, id],
            |row| row.get(0),
        )?)
    }

    fn next_free_id(conn: &Connection, namespace: &str) -> Result<String> {
        let id: i64 = conn.query_row(
            "SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0) + 1 FROM kv WHERE namespace = ?1",
            params![namespace],
            |row| row.get(0),
        )?;
        Ok(id.to_string())
    }

    pub fn latest_event_id(&self) -> Result<i64> {
        Ok(self
            .conn
            .query_row("SELECT COALESCE(MAX(id), 0) FROM events", [], |row| {
                row.get(0)
            })?)
    }

    pub fn events_since(&self, last_id: i64) -> Result<EventPage> {
        if last_id < 0 {
            return Err(BackendError::invalid(
                "invalid_event_cursor",
                "事件游标不能为负数",
            ));
        }
        let mut stmt = self.conn.prepare(
            "SELECT id, kind, payload, created_at FROM events WHERE id > ?1 ORDER BY id LIMIT 201",
        )?;
        let rows = stmt.query_map(params![last_id], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })?;
        let mut events = Vec::new();
        for row in rows {
            let (id, kind, payload, created_at) = row?;
            events.push(BackendEvent {
                id,
                kind,
                payload: serde_json::from_str(&payload)?,
                created_at,
            });
        }
        let has_more = events.len() > 200;
        events.truncate(200);
        Ok(EventPage {
            events,
            latest_id: self.latest_event_id()?,
            has_more,
        })
    }
}

fn field<'a>(object: &'a Map<String, Value>, key: &str) -> Result<&'a Value> {
    object
        .get(key)
        .ok_or_else(|| BackendError::invalid("missing_field", "记录缺少必需字段"))
}

fn text(object: &Map<String, Value>, key: &str, max: usize, blank: bool) -> Result<()> {
    let value = field(object, key)?
        .as_str()
        .ok_or_else(|| BackendError::invalid("invalid_field", "文本字段类型不正确"))?;
    if value.len() > max || (!blank && value.trim().is_empty()) {
        return Err(BackendError::invalid(
            "invalid_field",
            "文本字段为空或超过长度上限",
        ));
    }
    Ok(())
}

fn boolean(object: &Map<String, Value>, key: &str) -> Result<()> {
    if field(object, key)?.is_boolean() {
        Ok(())
    } else {
        Err(BackendError::invalid("invalid_field", "布尔字段类型不正确"))
    }
}

fn progress(object: &Map<String, Value>, key: &str) -> Result<()> {
    let value = field(object, key)?
        .as_f64()
        .ok_or_else(|| BackendError::invalid("invalid_field", "进度必须是数字"))?;
    if value.is_finite() && (0.0..=100.0).contains(&value) {
        Ok(())
    } else {
        Err(BackendError::invalid(
            "invalid_field",
            "进度必须位于 0 到 100",
        ))
    }
}

fn record_id(record: &Value) -> Result<String> {
    let object = record
        .as_object()
        .ok_or_else(|| BackendError::invalid("invalid_record", "记录必须是对象"))?;
    let id = object
        .get("id")
        .and_then(Value::as_i64)
        .ok_or_else(|| BackendError::invalid("invalid_id", "记录标识必须是正整数"))?;
    if id <= 0 {
        return Err(BackendError::invalid("invalid_id", "记录标识必须是正整数"));
    }
    Ok(id.to_string())
}

fn allowed_fields(object: &Map<String, Value>, allowed: &[&str]) -> Result<()> {
    if object.keys().all(|key| allowed.contains(&key.as_str())) {
        Ok(())
    } else {
        Err(BackendError::invalid(
            "unknown_field",
            "记录包含不支持的字段",
        ))
    }
}

fn validate_collection(namespace: &str, value: &Value) -> Result<()> {
    let items = value
        .as_array()
        .ok_or_else(|| BackendError::invalid("collection_must_be_array", "集合必须是数组"))?;
    if items.len() > 10_000 {
        return Err(BackendError::new("quota_exceeded", "集合记录数量超过上限"));
    }
    let mut ids = BTreeSet::new();
    for item in items {
        if serde_json::to_vec(item)?.len() > MAX_RECORD_BYTES {
            return Err(BackendError::new(
                "record_too_large",
                "单条记录超过 1 MB 上限",
            ));
        }
        let object = item
            .as_object()
            .ok_or_else(|| BackendError::invalid("invalid_record", "记录必须是对象"))?;
        if !ids.insert(record_id(item)?) {
            return Err(BackendError::invalid("duplicate_id", "记录标识不能重复"));
        }
        text(object, "title", 500, false)?;
        match namespace {
            "tasks" => {
                allowed_fields(
                    object,
                    &["id", "title", "subject", "duration", "done", "kind"],
                )?;
                text(object, "subject", 100, false)?;
                boolean(object, "done")?;
                let duration = field(object, "duration")?
                    .as_u64()
                    .ok_or_else(|| BackendError::invalid("invalid_duration", "时长必须是正整数"))?;
                if !(1..=10_080).contains(&duration) {
                    return Err(BackendError::invalid(
                        "invalid_duration",
                        "时长必须位于 1 到 10080 分钟",
                    ));
                }
                if !["学习", "练习", "复习", "项目"]
                    .contains(&field(object, "kind")?.as_str().unwrap_or_default())
                {
                    return Err(BackendError::invalid("invalid_kind", "任务类型不受支持"));
                }
            }
            "notes" => {
                allowed_fields(
                    object,
                    &["id", "title", "excerpt", "tag", "updated", "content"],
                )?;
                text(object, "excerpt", 2_000, true)?;
                text(object, "tag", 100, false)?;
                text(object, "updated", 100, true)?;
                text(object, "content", MAX_RECORD_BYTES, false)?;
            }
            "resources" => {
                allowed_fields(object, &["id", "title", "type", "meta", "progress"])?;
                if !["PDF", "视频", "网页", "音频", "文件"]
                    .contains(&field(object, "type")?.as_str().unwrap_or_default())
                {
                    return Err(BackendError::invalid("invalid_kind", "资料类型不受支持"));
                }
                text(object, "meta", 1_000, true)?;
                progress(object, "progress")?;
            }
            "projects" => {
                allowed_fields(
                    object,
                    &[
                        "id",
                        "title",
                        "description",
                        "due",
                        "progress",
                        "tag",
                        "milestones",
                    ],
                )?;
                text(object, "description", 10_000, true)?;
                text(object, "due", 100, true)?;
                text(object, "tag", 100, false)?;
                progress(object, "progress")?;
                let milestones = field(object, "milestones")?.as_array().ok_or_else(|| {
                    BackendError::invalid("invalid_milestones", "里程碑必须是数组")
                })?;
                if milestones.len() > 200 {
                    return Err(BackendError::new("quota_exceeded", "里程碑数量超过上限"));
                }
                let mut milestone_ids = BTreeSet::new();
                for milestone in milestones {
                    let object = milestone.as_object().ok_or_else(|| {
                        BackendError::invalid("invalid_milestone", "里程碑必须是对象")
                    })?;
                    allowed_fields(object, &["id", "title", "done"])?;
                    if !milestone_ids.insert(record_id(milestone)?) {
                        return Err(BackendError::invalid("duplicate_id", "里程碑标识不能重复"));
                    }
                    text(object, "title", 500, false)?;
                    boolean(object, "done")?;
                }
            }
            _ => {
                return Err(BackendError::invalid(
                    "unknown_collection",
                    "不支持的数据集合",
                ))
            }
        }
    }
    Ok(())
}

fn validate_preferences(value: &Value) -> Result<()> {
    let object = value
        .as_object()
        .ok_or_else(|| BackendError::invalid("invalid_preferences", "偏好必须是对象"))?;
    allowed_fields(
        object,
        &[
            "proactive",
            "hintsOnly",
            "notifications",
            "weeklyReport",
            "highContrast",
            "reducedMotion",
            "largeText",
            "theme",
        ],
    )?;
    for key in [
        "proactive",
        "hintsOnly",
        "notifications",
        "weeklyReport",
        "highContrast",
        "reducedMotion",
        "largeText",
    ] {
        boolean(object, key)?;
    }
    if !["light", "dark"].contains(&field(object, "theme")?.as_str().unwrap_or_default()) {
        return Err(BackendError::invalid("invalid_theme", "主题不受支持"));
    }
    Ok(())
}

fn validate_draft(value: &Value) -> Result<()> {
    let object = value
        .as_object()
        .ok_or_else(|| BackendError::invalid("invalid_draft", "草稿必须是对象"))?;
    allowed_fields(object, &["id", "title", "content", "tag", "savedAt"])?;
    if serde_json::to_vec(value)?.len() > MAX_RECORD_BYTES {
        return Err(BackendError::new("record_too_large", "草稿超过 1 MB 上限"));
    }
    text(object, "title", 500, true)?;
    text(object, "content", MAX_RECORD_BYTES, true)?;
    text(object, "tag", 100, false)?;
    if object.contains_key("savedAt") && !field(object, "savedAt")?.is_number() {
        return Err(BackendError::invalid(
            "invalid_field",
            "草稿保存时间必须是数字",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn note(id: i64, title: &str) -> Value {
        json!({"id": id, "title": title, "excerpt": "摘要", "tag": "复盘", "updated": "now", "content": "正文"})
    }

    fn task(id: i64) -> Value {
        json!({"id": id, "title": "学习", "subject": "数学", "duration": 20, "done": false, "kind": "学习"})
    }

    #[test]
    fn bootstrap_is_empty_and_persistent_writes_survive_restart() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("app.db");
        let store = Store::at(&path).unwrap();
        let initial = store.bootstrap().unwrap();
        assert_eq!(initial["tasks"], json!([]));
        assert_eq!(initial["notes"], json!([]));
        assert_eq!(initial["preferences"]["theme"], "light");
        assert_eq!(initial["lastEventId"], 0);
        assert_eq!(store.bootstrap().unwrap(), initial);
        let mutation = store
            .replace_collection("tasks", 0, &json!([task(1)]))
            .unwrap();
        assert_eq!(mutation.state["versions"]["tasks"], 1);
        assert_eq!(mutation.event.unwrap().id, 1);
        drop(store);
        let reopened = Store::at(&path).unwrap();
        assert_eq!(reopened.app_state().unwrap()["tasks"][0]["title"], "学习");
        assert_eq!(reopened.bootstrap().unwrap()["lastEventId"], 1);
    }

    #[test]
    fn writes_validate_and_do_not_create_terminal_events() {
        let store = Store::in_memory().unwrap();
        store.bootstrap().unwrap();
        let error = store
            .replace_collection("tasks", 0, &json!([{"title": "无标识"}]))
            .unwrap_err();
        assert_eq!(error.code, "invalid_id");
        assert_eq!(store.collection("tasks").unwrap().value, json!([]));
        assert_eq!(store.latest_event_id().unwrap(), 0);
        assert_eq!(
            store
                .replace_collection("other", 0, &json!([]))
                .unwrap_err()
                .code,
            "unknown_collection"
        );
        store
            .replace_collection("tasks", 0, &json!([task(1)]))
            .unwrap();
        assert_eq!(
            store
                .replace_collection("tasks", 0, &json!([task(2)]))
                .unwrap_err()
                .code,
            "revision_conflict"
        );
        assert_eq!(store.collection("tasks").unwrap().value, json!([task(1)]));
        assert_eq!(store.latest_event_id().unwrap(), 1);
    }

    #[test]
    fn drafts_round_trip_individually_and_missing_is_null() {
        let store = Store::in_memory().unwrap();
        store.bootstrap().unwrap();
        assert_eq!(
            store.draft("deep-tutor-draft-new").unwrap().value,
            Value::Null
        );
        store
            .put_draft(
                "deep-tutor-draft-new",
                &json!({"title": "一", "content": "甲", "tag": "复盘", "savedAt": 1}),
            )
            .unwrap();
        store
            .put_draft(
                "deep-tutor-draft-1",
                &json!({"title": "二", "content": "乙", "tag": "复盘", "savedAt": 2}),
            )
            .unwrap();
        assert_eq!(
            store.draft("deep-tutor-draft-new").unwrap().value["content"],
            "甲"
        );
        assert_eq!(
            store.draft("deep-tutor-draft-1").unwrap().value["content"],
            "乙"
        );
        assert!(store.drafts().unwrap().len() == 2);
        store.delete_draft("deep-tutor-draft-new").unwrap();
        assert_eq!(
            store.draft("deep-tutor-draft-new").unwrap().value,
            Value::Null
        );
        assert!(store
            .put_draft("bad", &json!({"title": "", "content": "", "tag": "复盘"}))
            .is_err());
    }

    #[test]
    fn outbox_is_durable_ordered_and_paginated_without_duplicate_execution() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("app.db");
        let store = Store::at(&path).unwrap();
        store.bootstrap().unwrap();
        for index in 0..201 {
            store
                .put_draft(
                    "deep-tutor-draft-new",
                    &json!({"title": index.to_string(), "content": "draft", "tag": "复盘"}),
                )
                .unwrap();
        }
        let first = store.events_since(0).unwrap();
        assert_eq!(first.events.len(), 200);
        assert!(first.has_more);
        assert_eq!(first.events.first().unwrap().id, 1);
        assert_eq!(first.events.last().unwrap().id, 200);
        drop(store);
        let reopened = Store::at(&path).unwrap();
        let second = reopened.events_since(200).unwrap();
        assert_eq!(second.events.len(), 1);
        assert_eq!(second.events[0].id, 201);
        assert!(!second.has_more);
        assert_eq!(reopened.events_since(201).unwrap().events.len(), 0);
        assert_eq!(
            reopened.draft("deep-tutor-draft-new").unwrap().value["title"],
            "200"
        );
        assert_eq!(
            reopened.events_since(-1).unwrap_err().code,
            "invalid_event_cursor"
        );
    }

    #[test]
    fn backup_is_strict_atomic_and_append_only() {
        let store = Store::in_memory().unwrap();
        store.bootstrap().unwrap();
        store
            .replace_collection("notes", 0, &json!([note(1, "原笔记")]))
            .unwrap();
        store
            .put_draft(
                "deep-tutor-draft-new",
                &json!({"title": "草稿", "content": "正文", "tag": "复盘"}),
            )
            .unwrap();
        let backup = store.export_backup().unwrap();
        assert_eq!(backup["version"], 2);
        assert_eq!(backup["drafts"].as_array().unwrap().len(), 1);
        assert_eq!(
            store.preview_backup(&json!({})).unwrap_err().code,
            "unsupported_backup"
        );
        let mut invalid = backup.clone();
        invalid["notes"] = json!([note(2, "不应导入"), {"id": 3}]);
        assert!(store.import_backup(&invalid).is_err());
        assert_eq!(
            store.collection("notes").unwrap().value,
            json!([note(1, "原笔记")])
        );
        let event_count = store.latest_event_id().unwrap();
        store.import_backup(&backup).unwrap();
        let notes = store.collection("notes").unwrap().value;
        assert_eq!(notes.as_array().unwrap().len(), 2);
        assert_eq!(notes[0]["id"], 1);
        assert_eq!(notes[1]["id"], 2);
        assert_eq!(notes[1]["title"], "原笔记");
        assert_eq!(store.drafts().unwrap().len(), 2);
        assert!(store.latest_event_id().unwrap() > event_count);
        store.reset().unwrap();
        assert_eq!(store.app_state().unwrap()["notes"], json!([]));
        assert!(store.latest_event_id().unwrap() > event_count + 1);
    }
}
