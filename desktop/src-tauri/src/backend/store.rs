use super::error::{BackendError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
#[cfg(test)]
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const COLLECTIONS: [&str; 4] = ["tasks", "notes", "resources", "projects"];
pub(crate) const MAX_RECORD_BYTES: usize = 1024 * 1024;
/// 代码侧 schema 版本：任何 migrate() 新增表/列时必须递增，
/// 使老库即使指纹与记录一致也会重新执行迁移（指纹只反映库内现状，不反映代码期望）。
const SCHEMA_VERSION: i64 = 1;
static STORE_OPEN_LOCK: Mutex<()> = Mutex::new(());
// 12#20：迁移计数改为互斥保护——无锁原子计数在并行测试下与幂等断言竞态；
// 互斥后读取/递增串行化，断言不再随机漂移
#[cfg(test)]
static MIGRATION_RUNS: std::sync::Mutex<usize> = std::sync::Mutex::new(0);

#[cfg(test)]
pub fn migration_count() -> usize {
    *MIGRATION_RUNS.lock().unwrap()
}

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
    pub(crate) owner_id: Option<i64>,
    pub(crate) account_path: PathBuf,
}

impl Store {
    pub fn new(path: impl Into<PathBuf>) -> Result<Self> {
        let root = path.into();
        // 22#1：隐式入口不再 fail-open 回退 user1——无 principal 的裸线程
        // 误用会静默写入 user1 工作区（23#7 曾实证踩中）；启动装配
        // （startup::assemble）显式用 at_user(…, 1) 创建引导库，测试环境
        // 维持 root 语义
        #[cfg(not(test))]
        let owner = Some(super::security::principal_id().ok_or_else(|| {
            BackendError::new("store_unavailable", "本地会话身份缺失，拒绝打开数据层")
        })?);
        #[cfg(test)]
        let owner = super::security::principal_id();
        Self::open(root, owner)
    }

    pub fn at_user(path: impl Into<PathBuf>, user_id: i64) -> Result<Self> {
        Self::open(path.into(), Some(user_id))
    }

    pub fn root(path: impl Into<PathBuf>) -> Result<Self> {
        Self::open(path.into(), None)
    }

    /// 读-改-写事务使用 IMMEDIATE：WAL 下 DEFERRED 事务在读快照过期后升级写锁
    /// 会得到 BUSY_SNAPSHOT（busy_timeout 不重试）；IMMEDIATE 起步即持写锁，
    /// 冲突时按 busy_timeout 等待而非立即失败。适合 replace_collection 这类
    /// 与流式 delta 自动提交并发的高频短事务（07#11）。
    pub(crate) fn write_transaction(&self) -> Result<rusqlite::Transaction<'_>> {
        rusqlite::Transaction::new_unchecked(&self.conn, rusqlite::TransactionBehavior::Immediate)
            .map_err(BackendError::from)
    }

    fn schema_fingerprint(conn: &Connection) -> Result<String> {
        let mut statement = conn.prepare(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master
             WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })?;
        let mut digest = Sha256::new();
        for row in rows {
            let (kind, name, table, sql) = row?;
            for value in [kind, name, table, sql] {
                digest.update(value.as_bytes());
                digest.update([0]);
            }
        }
        Ok(format!("{:x}", digest.finalize()))
    }

    fn open(root: PathBuf, owner: Option<i64>) -> Result<Self> {
        let path = match owner {
            Some(user_id) => super::security::user_workspace_path(&root, user_id)?,
            None => root.clone(),
        };
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        let _open = STORE_OPEN_LOCK
            .lock()
            .map_err(|_| BackendError::new("store_unavailable", "数据库初始化锁不可用"))?;
        if owner == Some(1) && root != path && root.exists() && !path.exists() {
            let source = Connection::open(&root)?;
            source.pragma_update(None, "busy_timeout", 10000)?;
            source.execute_batch("PRAGMA wal_checkpoint(FULL)")?;
            drop(source);
            // 先写临时文件再原子改名：复制中途崩溃不会在正式路径留下截断的库
            let staged = path.with_file_name(format!(
                "{}.migration-tmp",
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("store")
            ));
            if let Err(error) = fs::copy(&root, &staged).and_then(|_| fs::rename(&staged, &path)) {
                let _ = fs::remove_file(&staged);
                return Err(error.into());
            }
        }
        let conn = Connection::open(&path)?;
        // B-10：SQLite 内建 lower() 仅覆盖 ASCII——注册 Unicode 版供搜索
        // 等场景使用（西里尔/希腊等字母的大小写变体检索）
        conn.create_scalar_function(
            "uni_lower",
            1,
            rusqlite::functions::FunctionFlags::SQLITE_UTF8
                | rusqlite::functions::FunctionFlags::SQLITE_DETERMINISTIC,
            |ctx| {
                let text: String = ctx.get(0)?;
                Ok(text.to_lowercase())
            },
        )?;
        let journal_mode: String =
            conn.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
        if !journal_mode.eq_ignore_ascii_case("wal") {
            conn.pragma_update(None, "journal_mode", "WAL")?;
        }
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "busy_timeout", 10000)?;
        // 27#7：WAL 常规搭配 synchronous=NORMAL——每提交 fsync 的 FULL 在
        // Windows 上放大流式 delta 的写延迟；NORMAL 下断电最多丢最后一次
        // 提交（WAL 不损坏、checkpoint 仍可靠），事件 outbox 的顺序与重放
        // 语义不受影响（见 note/产品与技术决策.md）
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        let version: i64 = conn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version > 1 {
            return Err(BackendError::new(
                "unsupported_schema",
                "数据库版本高于当前应用，拒绝执行降级修改",
            ));
        }
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
            );
            -- 27#2：流式期间 getProviderChat 按 kind+json_extract(requestId) 重放
            -- delta（前端 8 个页面 150ms 轮询），表达式索引让该查询免全表扫描
            CREATE INDEX IF NOT EXISTS events_kind_request
                ON events(kind, json_extract(payload, '$.requestId'));",
        )?;
        let fingerprint = Self::schema_fingerprint(&conn)?;
        let recorded: Option<String> = conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_fingerprint'",
                [],
                |row| row.get(0),
            )
            .optional()?;
        let recorded_version: i64 = conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_version'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .and_then(|value| value.parse().ok())
            .unwrap_or(0);
        // 指纹不同 = 库被外部改动或首次打开；代码版本前进 = migrate 有新增列/表要对老库补齐。
        // 旧库只记指纹、不记版本：按 0 处理，保证迁移至少补跑一次后写入版本。
        if recorded.as_deref() != Some(fingerprint.as_str()) || recorded_version < SCHEMA_VERSION {
            #[cfg(test)]
            {
                *MIGRATION_RUNS.lock().unwrap() += 1;
            }
            // 根库（owner 为空）跳过 kv→materials 一次性导入，避免复制后再生成死数据
            super::materials::migrate(&conn, owner.is_some())?;
            super::knowledge::migrate(&conn)?;
            super::providers::migrate(&conn)?;
            super::learning::migrate(&conn)?;
            super::projects::migrate(&conn)?;
            super::sharing::migrate(&conn)?;
            super::practice::migrate(&conn)?;
            super::review::migrate(&conn)?;
            super::agent::migrate(&conn)?;
            super::community::migrate(&conn)?;
            super::plugins::migrate(&conn)?;
            super::engagement::migrate(&conn)?;
            super::reports::migrate(&conn)?;
            super::security::migrate(&conn)?;
            super::sync::migrate(&conn)?;
            let verified = Self::schema_fingerprint(&conn)?;
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_fingerprint', ?1)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                params![verified],
            )?;
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?1)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                params![SCHEMA_VERSION.to_string()],
            )?;
        }
        if version < 1 {
            conn.pragma_update(None, "user_version", 1)?;
        }
        Ok(Self {
            conn,
            owner_id: owner,
            account_path: root,
        })
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
            "theme": "light",
            "language": "zh-CN",
            "weeklyHours": 15,
            "planIntensity": "标准",
            "learningStyle": "案例与练习结合",
            "explanationStyle": "先追问，再针对讲解",
            "quietStart": "22:00",
            "quietEnd": "08:00",
            "quietEnabled": true,
            "learningRole": "自学者",
            "learningGoal": "",
            "diagnosticStartedAt": null
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

    pub(crate) fn ensure_quota(&self, resource: &str, additional: u64) -> Result<()> {
        self.ensure_quota_in_tx(&self.conn, resource, additional)
    }

    /// 25#18：配额计数在调用方写事务内执行——原 ensure_quota 在只读连接上
    /// check-then-insert，窗口内并发写可超限在途并发数；把计数放进与插入
    /// 相同的事务（调用方持 IMMEDIATE 写锁）后窗口关闭
    pub(crate) fn ensure_quota_in_tx(
        &self,
        conn: &Connection,
        resource: &str,
        additional: u64,
    ) -> Result<()> {
        let Some(user_id) = self.owner_id else {
            return Ok(());
        };
        let accounts = Connection::open_with_flags(
            &self.account_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        let limit = accounts
            .query_row(
                "SELECT item_limit FROM security_quotas WHERE user_id = ?1 AND resource = ?2",
                params![user_id, resource],
                |row| row.get(0),
            )
            .optional()?;
        let Some(limit) = limit else {
            return Ok(());
        };
        let used: i64 = match resource {
            "notes" => conn.query_row(
                "SELECT COUNT(*) FROM kv WHERE namespace = 'notes'",
                [],
                |row| row.get(0),
            )?,
            "materials" => {
                conn.query_row("SELECT COUNT(*) FROM materials", [], |row| row.get(0))?
            }
            "sessions" => {
                conn.query_row("SELECT COUNT(*) FROM agent_sessions", [], |row| row.get(0))?
            }
            // 03#10：threads 配额按帖子与回复总数计，回复不再无限突破上限
            "threads" => conn.query_row(
                "SELECT (SELECT COUNT(*) FROM community_posts) + (SELECT COUNT(*) FROM community_replies)",
                [],
                |row| row.get(0),
            )?,
            "agent_memory" => {
                conn.query_row("SELECT COUNT(*) FROM agent_memory", [], |row| row.get(0))?
            }
            _ => return Err(BackendError::invalid("invalid_quota", "未知的资源配额")),
        };
        let additional = i64::try_from(additional).unwrap_or(i64::MAX);
        if used.saturating_add(additional) > limit {
            return Err(BackendError::new(
                "quota_exceeded",
                &format!("{resource} 配额已用尽：当前 {used}，上限 {limit}；未写入新内容"),
            ));
        }
        Ok(())
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
            "workspaceId".to_string(),
            Value::from(self.owner_id.unwrap_or(0)),
        );
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
        let event = BackendEvent {
            id: conn.last_insert_rowid(),
            kind: kind.to_string(),
            payload,
            created_at: Self::now()?,
        };
        super::plugins::dispatch(conn, event.id, &event.kind)?;
        Ok(event)
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

    fn normalize_tasks(&self, value: &Value) -> Result<Value> {
        let previous = self.collection("tasks")?.value;
        let mut normalized = value.clone();
        let timestamp = Self::now()?;
        if let Some(items) = normalized.as_array_mut() {
            for item in items {
                let Some(object) = item.as_object_mut() else {
                    continue;
                };
                let id = object.get("id").and_then(Value::as_i64);
                let done = object.get("done").and_then(Value::as_bool).unwrap_or(false);
                let was_done = previous.as_array().into_iter().flatten().any(|task| {
                    task.get("id").and_then(Value::as_i64) == id
                        && task.get("done").and_then(Value::as_bool) == Some(true)
                });
                if done && !was_done {
                    object.insert("completedAt".into(), Value::String(timestamp.clone()));
                } else if !done {
                    object.remove("completedAt");
                }
            }
        }
        Ok(normalized)
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
                "资料原件、解析和删除必须使用原生资料命令",
            ));
        }
        if !Self::namespace_allowed(namespace) {
            return Err(BackendError::invalid(
                "unknown_collection",
                "不支持的数据集合",
            ));
        }
        let normalized = if namespace == "tasks" {
            Some(self.normalize_tasks(value)?)
        } else {
            None
        };
        let value = normalized.as_ref().unwrap_or(value);
        if namespace == "preferences" {
            validate_preferences(value)?;
        } else {
            validate_collection(namespace, value)?;
        }
        // 25#18：notes 配额的增量先算好，计数检查放进下方写事务内执行
        let notes_quota = if namespace == "notes" {
            let next = value.as_array().map_or(0, Vec::len);
            let current = self
                .collection("notes")?
                .value
                .as_array()
                .map_or(0, Vec::len);
            Some(next.saturating_sub(current) as u64)
        } else {
            None
        };
        // IMMEDIATE 事务：与流式 delta 写入并发时不再触发 BUSY_SNAPSHOT
        let tx = self.write_transaction()?;
        if let Some(additional) = notes_quota {
            self.ensure_quota_in_tx(&tx, "notes", additional)?;
        }
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
        if namespace == "notes" {
            tx.execute("DELETE FROM learning_annotations WHERE note_id NOT IN (SELECT CAST(id AS INTEGER) FROM kv WHERE namespace = 'notes')", [])?;
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
        // A-11：草稿保存与流式 delta 自动提交并发频繁，读改写用 IMMEDIATE
        //（07#11 同指引）——DEFERRED 读快照过期后升级写锁会得 BUSY_SNAPSHOT
        let tx = self.write_transaction()?;
        let revision = Self::next_revision(&tx, "drafts")?;
        Self::insert_json(&tx, "drafts", id, value)?;
        let event = Self::event(&tx, "draft.saved", json!({"id": id, "revision": revision}))?;
        self.finish_write(tx, Some(event))
    }

    pub fn delete_draft(&self, id: &str) -> Result<Mutation> {
        Self::validate_draft_id(id)?;
        // A-11：与 put_draft 同理，读改写事务用 IMMEDIATE
        let tx = self.write_transaction()?;
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
        super::plugins::clear_all(&tx)?;
        super::sync::clear_all(&tx)?;
        super::community::clear_all(&tx)?;
        super::agent::clear_all(&tx)?;
        super::practice::clear_all(&tx)?;
        super::review::clear_all(&tx)?;
        super::engagement::clear_all(&tx)?;
        super::reports::clear_all(&tx)?;
        super::knowledge::clear_all(&tx)?;
        super::sharing::clear_all(&tx)?;
        if self.owner_id.is_none() {
            tx.execute("DELETE FROM share_index WHERE user_id = 1", [])?;
        }
        // 多用户时根库 share_index 在主事务提交成功后再清：
        // 提交前清空会在回滚后留下"索引已空而 shared_plans 仍在"的不一致（07#3）
        super::projects::clear_all(&tx)?;
        super::learning::clear_all(&tx)?;
        super::materials::clear_all(&tx)?;
        super::providers::clear_all(&tx)?;
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
        let mutation = self.finish_write(tx, Some(event))?;
        if let Some(owner_id) = self.owner_id {
            // 主事务已提交后再清根库索引；清理失败时重置本身报错，不再产生
            // "索引已空而 shared_plans 仍在"的跨库不一致（备份导入 reindex 亦可自愈）
            super::sharing::clear_index(&self.account_path, owner_id)?;
        }
        Ok(mutation)
    }

    #[cfg(test)]
    fn drafts(&self) -> Result<Vec<Value>> {
        let mut statement = self
            .conn
            .prepare("SELECT id, data FROM kv WHERE namespace = 'drafts' ORDER BY id")?;
        let rows = statement.query_map([], |row| {
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

    pub(crate) fn next_free_id(conn: &Connection, namespace: &str) -> Result<String> {
        // 24#4：前端 localId 以 Date.now 为基（毫秒时间戳量级），后端补学
        // 任务等 MAX+1 紧贴该序列——同毫秒交错时后端刚占用的 id 会被前端
        // 下一个 localId 撞上（kv 主键冲突/React key 重复）。基数从
        // 当前毫秒时间戳起跳，两个空间不再交叠
        let floor = OffsetDateTime::now_utc().unix_timestamp() * 1000;
        let id: i64 = conn.query_row(
            "SELECT MAX(COALESCE(MAX(CAST(id AS INTEGER)), 0) + 1, ?2) FROM kv WHERE namespace = ?1",
            params![namespace, floor],
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

pub(crate) fn validate_collection(namespace: &str, value: &Value) -> Result<()> {
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
                    &[
                        "id",
                        "title",
                        "subject",
                        "duration",
                        "done",
                        "kind",
                        "completedAt",
                    ],
                )?;
                if object.contains_key("completedAt") {
                    text(object, "completedAt", 64, false)?;
                }
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
                // projectVersionId：项目迭代笔记与版本的持久关联（13#28），
                // 可选数字；此前版本号只在标题文本与一次性事件载荷里
                allowed_fields(
                    object,
                    &[
                        "id",
                        "title",
                        "excerpt",
                        "tag",
                        "updated",
                        "content",
                        "projectVersionId",
                    ],
                )?;
                if object.contains_key("projectVersionId")
                    && !object
                        .get("projectVersionId")
                        .unwrap_or(&Value::Null)
                        .is_number()
                {
                    return Err(BackendError::invalid(
                        "invalid_field",
                        "笔记关联的项目版本标识必须是数字",
                    ));
                }
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
                        "planId",
                        "milestones",
                    ],
                )?;
                text(object, "description", 10_000, true)?;
                text(object, "due", 100, true)?;
                text(object, "tag", 100, false)?;
                if object.get("planId").is_some_and(|value| !value.is_null()) {
                    text(object, "planId", 64, false)?;
                }
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

pub(crate) fn validate_preferences(value: &Value) -> Result<()> {
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
            "language",
            "weeklyHours",
            "planIntensity",
            "learningStyle",
            "explanationStyle",
            "quietStart",
            "quietEnd",
            "quietEnabled",
            "profileName",
            "learningRole",
            "learningGoal",
            "diagnosticStartedAt",
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
    if object.contains_key("quietEnabled") {
        boolean(object, "quietEnabled")?;
    }
    if object.contains_key("weeklyHours")
        && !(1..=80).contains(&field(object, "weeklyHours")?.as_u64().unwrap_or_default())
    {
        return Err(BackendError::invalid(
            "invalid_weekly_hours",
            "每周投入须为 1 到 80 小时",
        ));
    }
    if object.contains_key("planIntensity")
        && !["稳妥", "标准", "冲刺"]
            .contains(&field(object, "planIntensity")?.as_str().unwrap_or_default())
    {
        return Err(BackendError::invalid(
            "invalid_intensity",
            "学习强度须为稳妥、标准或冲刺",
        ));
    }
    for (key, maximum) in [
        ("language", 16),
        ("learningStyle", 100),
        ("explanationStyle", 100),
        ("profileName", 80),
        ("learningRole", 100),
        ("learningGoal", 200),
    ] {
        if object.contains_key(key) {
            text(object, key, maximum, key == "learningGoal")?;
        }
    }
    for key in ["quietStart", "quietEnd"] {
        if object.contains_key(key) {
            let value = field(object, key)?.as_str().unwrap_or_default();
            let valid = value.split_once(':').is_some_and(|(hour, minute)| {
                hour.len() == 2
                    && minute.len() == 2
                    && hour.parse::<u32>().is_ok_and(|hour| hour < 24)
                    && minute.parse::<u32>().is_ok_and(|minute| minute < 60)
            });
            if !valid {
                return Err(BackendError::invalid(
                    "invalid_quiet_time",
                    "免打扰时间格式无效",
                ));
            }
        }
    }
    if object
        .get("diagnosticStartedAt")
        .is_some_and(|value| !value.is_null())
    {
        text(object, "diagnosticStartedAt", 64, false)?;
    }
    if !["light", "dark"].contains(&field(object, "theme")?.as_str().unwrap_or_default()) {
        return Err(BackendError::invalid("invalid_theme", "主题不受支持"));
    }
    Ok(())
}

pub(crate) fn validate_draft(value: &Value) -> Result<()> {
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
    fn verified_schema_is_not_migrated_again_but_schema_drift_is_repaired() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("app.db");
        drop(Store::root(&path).unwrap());
        let baseline = migration_count();
        for _ in 0..20 {
            drop(Store::root(&path).unwrap());
        }
        assert_eq!(migration_count(), baseline);
        let connection = Connection::open(&path).unwrap();
        connection.execute("DROP TABLE sync_jobs", []).unwrap();
        drop(connection);
        let store = Store::root(&path).unwrap();
        assert_eq!(migration_count(), baseline + 1);
        let restored: bool = store
            .conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_jobs')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(restored);
    }
}
