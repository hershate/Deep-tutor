use super::error::{BackendError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const COLLECTIONS: [&str; 4] = ["tasks", "notes", "resources", "projects"];
pub(crate) const MAX_RECORD_BYTES: usize = 1024 * 1024;
/// 代码侧 schema 版本：任何 migrate() 新增表/列时必须递增，
/// 使老库即使指纹与记录一致也会重新执行迁移（指纹只反映库内现状，不反映代码期望）。
/// v2（34#1）：provider_requests 增列 agent_session_id（31#1 引入时漏递增，
/// 已在版本 1 记录过指纹的存量库跳过迁移，Agent 域 SQL 报 no such column）
/// v3（99#5）：provider_circuits 增列 last_failure_at（失败计数时间窗）
/// v4（129 波补递增，152#2）：索引三补三删进 migrate()（129#2/#3/#4 的
/// CREATE/DROP INDEX）——当时未递增版本，v3 时代建库的存量库指纹自洽
/// （索引是迁移产物非前提）导致该批索引修复对升级库永不执行
const SCHEMA_VERSION: i64 = 4;
static STORE_OPEN_LOCK: Mutex<()> = Mutex::new(());
// 107#5：每路径一次性的「schema 已就绪」登记——首开（迁移/schema 建立/
// user1 整库迁移段）持全局锁，同路径后续打开跳锁；原实现无条件持锁，
// 稳态下 user1 首迁（GB 级可达数十秒）会让进程内所有 Store::new 排队。
// 登记锁只在查/插瞬间持有，不覆盖任何 IO
static STORE_OPENED: std::sync::LazyLock<Mutex<std::collections::HashSet<PathBuf>>> =
    std::sync::LazyLock::new(|| Mutex::new(std::collections::HashSet::new()));

/// 47#2：time crate 的 now_local() 在多线程进程中因健全性限制恒 Err——
/// Unix 构建上全部调用点静默回退 UTC，东八区 00:00–08:00 之间本地日界
/// 差一天（排期首日/复习到期/预算重置/免打扰全部平移）。
/// 126#10/127#10：偏移缓存加 1 小时 TTL——OnceLock 进程固化的偏移在
/// DST 时区跨季运行时过期 1 小时，本地午夜附近的日界判定错一天；
/// 周期重探测消掉该边界（探测失败保留上次值，不退化回 UTC）
static LOCAL_OFFSET: std::sync::Mutex<(time::UtcOffset, Option<std::time::Instant>)> =
    std::sync::Mutex::new((time::UtcOffset::UTC, None));

pub fn now_local() -> OffsetDateTime {
    let mut cached = LOCAL_OFFSET
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let fresh = cached
        .1
        .is_some_and(|at| at.elapsed() < std::time::Duration::from_secs(3600));
    if !fresh {
        if let Ok(local) = OffsetDateTime::now_local() {
            cached.0 = local.offset();
        }
        cached.1 = Some(std::time::Instant::now());
    }
    let offset = cached.0;
    OffsetDateTime::now_utc().to_offset(offset)
}

/// 46#4：幂等补列（先 PRAGMA table_info 再 ALTER）——原先 practice/variants
/// 与 learning 各持一份内联实现，逐份复制会漂移
pub(crate) fn ensure_column(
    conn: &Connection,
    table: &str,
    name: &str,
    declaration: &str,
) -> Result<()> {
    let mut statement = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    if !columns.iter().any(|column| column == name) {
        conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {declaration}"), [])?;
    }
    Ok(())
}
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
// 92 号：仅 events_since 使用（生产注册面已移除，测试仍用）
#[allow(dead_code)]
pub struct EventPage {
    pub events: Vec<BackendEvent>,
    #[serde(rename = "latestId")]
    pub latest_id: i64,
    #[serde(rename = "hasMore")]
    pub has_more: bool,
}

/// 127#13：坏行跳过等只读路径诊断——eprintln 在 release
///（windows_subsystem=windows）下 stderr 不可见，坏行来源无从追查；
/// 尽力追加到数据根的 diagnostics.log（32KB 截断轮转，任何失败静默，
/// 不得反向影响读路径）
pub(crate) fn append_diagnostic(account_path: &Path, line: &str) {
    use std::io::Write as _;
    let Some(root) = account_path.parent() else {
        return;
    };
    let path = root.join("diagnostics.log");
    if fs::metadata(&path)
        .map(|meta| meta.len() > 32 * 1024)
        .unwrap_or(false)
    {
        let _ = fs::write(&path, []);
    }
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(&path) {
        let stamp = OffsetDateTime::now_utc().format(&Rfc3339).ok();
        let _ = writeln!(file, "{} {}", stamp.as_deref().unwrap_or("?"), line);
    }
}

/// 127#7：security_quotas 只读连接缓存（按路径）——高频写路径的配额
/// 检查不再每次开关连接；连接失效（如换库/迁移）时重建一次
fn with_accounts<T>(account_path: &Path, read: impl Fn(&Connection) -> Result<T>) -> Result<T> {
    use std::collections::HashMap;
    use std::sync::OnceLock;
    // 130#15：缓存无主动逐出，正确性依赖三条外部不变式——①换数据目录下次
    // 启动才生效（缓存随进程消亡）；②重置走 SQL 不动账户库文件；③备份导入
    // 走行插入不替换文件。未来出现「同路径文件替换」类操作时，下方读失败
    // 弃连重建是唯一防线
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, Connection>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut guard = cache
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(connection) = guard.get(account_path) {
        if let Ok(result) = read(connection) {
            return Ok(result);
        }
        // 旧连接失效（文件被替换/锁状态异常）——丢弃后重建
        guard.remove(account_path);
    }
    let connection =
        Connection::open_with_flags(account_path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .map_err(|error| {
                BackendError::new(
                    "account_store_unavailable",
                    &format!("账户库不可读，无法校验配额（{error}）"),
                )
            })?;
    // 137#6：只读连接补 busy_timeout 对齐主连接口径——瞬时写锁窗不再立即
    // SQLITE_BUSY（命中时上方读失败弃连重建仍兜底）
    let _ = connection.busy_timeout(std::time::Duration::from_secs(10));
    let result = read(&connection);
    if result.is_ok() {
        guard.insert(account_path.to_path_buf(), connection);
    }
    result
}

pub struct Store {
    pub(crate) conn: Connection,
    pub(crate) owner_id: Option<i64>,
    pub(crate) account_path: PathBuf,
}

/// 66#3：配额资源 key → 用户可见中文名（与 SecurityPanel quotaNames 对齐），
/// 消息不再直拼英文资源名。119#4：materials 以安全面板的「资料」为准——
/// 原先写「KB 文档」，超限错误与设置项无法关联
fn quota_label(resource: &str) -> &'static str {
    match resource {
        "sessions" => "Agent 会话",
        "notes" => "笔记",
        "materials" => "资料",
        "threads" => "社区讨论",
        "agent_memory" => "Agent 记忆",
        _ => "资源",
    }
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
        // 107#5：稳态打开（同路径已完成首开）不走全局锁——user1 首迁整库
        // 复制（GB 级可达数十秒）原先让进程内所有 Store::new 排队，每命令
        // 新开连接的模式进一步放大阻塞面。首开持锁完成迁移段 + schema 建立
        // （journal_mode/WAL 切换与迁移并发会 database_busy），成功后登记
        // 路径，同路径后续打开跳锁
        let steady = STORE_OPENED
            .lock()
            .map(|opened| opened.contains(&path))
            .unwrap_or(false);
        let _open = if steady {
            None
        } else {
            Some(
                STORE_OPEN_LOCK
                    .lock()
                    .map_err(|_| BackendError::new("store_unavailable", "数据库初始化锁不可用"))?,
            )
        };
        if !steady && owner == Some(1) && root != path && root.exists() && !path.exists() {
            let source = Connection::open(&root)?;
            source.pragma_update(None, "busy_timeout", 10000)?;
            // 103#9：FULL checkpoint 需等齐所有读者，进行中的读事务（报表
            // 聚合/备份/流式大读）会使 busy=1——execute_batch 丢弃结果行
            // 后带着未回置的 WAL 帧复制主文件，会拷出缺失最近提交的库。
            // 读 busy 列，短暂重试，仍忙则拒绝本次迁移（下次打开重试），
            // 绝不带着未回置的 WAL 复制（maintenance 的 14#20 同款先例）
            let mut checkpointed = false;
            for _attempt in 0..20 {
                let (busy, _log, _checkpointed): (i64, i64, i64) =
                    source.query_row("PRAGMA wal_checkpoint(FULL)", [], |row| {
                        Ok((row.get(0)?, row.get(1)?, row.get(2)?))
                    })?;
                if busy == 0 {
                    checkpointed = true;
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            if !checkpointed {
                return Err(BackendError::new(
                    "store_busy",
                    "数据库正被长任务读取，迁移暂时无法完成，请稍后重试",
                ));
            }
            // 96#2：复制期间持源库写锁——staged→rename 原子性只保证目标路径
            // 完整，防不了源文件复制中途被并发写者触发 WAL 自动 checkpoint
            // （默认 1000 页阈值）改写同页，拷出页级新旧混合的撕裂库
            // （SecurityState 惰性创建后长存的账户连接即可触发）；BEGIN
            // IMMEDIATE 阻塞其他写者直至复制完成，drop 后释放
            source.execute_batch("BEGIN IMMEDIATE")?;
            // 先写临时文件再原子改名：复制中途崩溃不会在正式路径留下截断的库。
            // 127#8：主文件连同 -wal/-shm 一起复制——checkpoint 与 BEGIN
            // 之间的空档内并发提交只落 WAL 不落主文件，此前只复制主文件
            // 会把这些提交从迁移产物中静默丢失；写锁在手无新提交，目标侧
            // 打开后 checkpoint(TRUNCATE) 把复制来的 WAL 折叠进主文件
            let staged = path.with_file_name(format!(
                "{}.migration-tmp",
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("store")
            ));
            // 152#1：清扫上次失败/崩溃残留的 staged 临时件——rename 主文件
            // 成功而 -wal 失败的路径会留下 migration-tmp-wal，不清扫则永久滞留
            for stale in ["-wal", ""] {
                let _ = fs::remove_file(staged.with_file_name(format!(
                    "{}{stale}",
                    staged.file_name().and_then(|name| name.to_str()).unwrap_or_default()
                )));
            }
            let copy_result = (|| -> std::io::Result<()> {
                fs::copy(&root, &staged)?;
                let root_name = root
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default();
                let staged_name = staged
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default();
                // -shm 是共享内存索引（SQLite 自行重建）且被活跃连接锁定
                //（Windows 共享冲突），只复制承载未折叠提交的 -wal
                for suffix in ["-wal"] {
                    let side = root.with_file_name(format!("{root_name}{suffix}"));
                    if side.exists() {
                        fs::copy(
                            &side,
                            staged.with_file_name(format!("{staged_name}{suffix}")),
                        )?;
                    }
                }
                // 152#1：-wal 先落位、主文件后落位——主文件一旦先落位，
                // 二次 rename 失败（目标被杀软锁定/IO 错误/两 rename 间崩溃）
                // 会使 path.exists() 为真，下次打开跳过整段迁移并打开缺 WAL
                // 帧的库（127#8 要保的提交静默丢失）；反序时主文件未落位，
                // 失败重试会自然重做迁移
                for suffix in ["-wal", ""] {
                    let from = staged.with_file_name(format!("{staged_name}{suffix}"));
                    if from.exists() {
                        fs::rename(
                            &from,
                            &path.with_file_name(format!(
                                "{}{suffix}",
                                path.file_name()
                                    .and_then(|name| name.to_str())
                                    .unwrap_or("store")
                            )),
                        )?;
                    }
                }
                Ok(())
            })();
            let copied_wal = copy_result.is_ok();
            drop(source);
            if let Err(error) = copy_result {
                let _ = fs::remove_file(&staged);
                return Err(error.into());
            }
            if copied_wal {
                let target = Connection::open(&path)?;
                // 折叠复制来的 WAL；失败不阻断（WAL 保留，下次打开 SQLite
                // 自行回放，内容不丢）
                let _ = target.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
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
        // 46#3：user_version 惯例只写到 1，schema 版本实际记录在 meta 表——
        // 降级门只看 user_version 时，未来版本只递增 SCHEMA_VERSION 的库会被
        // 旧应用静默降级（新列数据被忽略）；取两者最大值（meta 表尚未建时为 0），
        // 仅高于当前应用版本（> SCHEMA_VERSION）才拒绝
        let meta_version: i64 = conn
            .query_row(
                "SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM meta WHERE key = 'schema_version'), 0)",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);
        if version.max(meta_version) > SCHEMA_VERSION {
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
        // 107#5：首开成功（迁移段 + schema 建成）后登记路径，同路径后续
        // 打开跳过全局锁；登记失败不影响正确性（仅退化为继续持锁）
        if !steady {
            if let Ok(mut opened) = STORE_OPENED.lock() {
                opened.insert(path.clone());
            }
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
            // 135#7：OR IGNORE——检查与插入非原子，并发 bootstrap 双见 none 时
            // 后者撞主键（当前调用点互斥串行不可达，防御口径）
            tx.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('seeded', '1')",
                [],
            )?;
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
        let current = Self::revision(conn, namespace)?;
        // 86#8：溢出前显式报错——release 下 wrap 会使乐观锁守卫永久不匹配，
        // 该 namespace 写入全拒且无诊断（理论不可达，完备性收口）
        if current == i64::MAX {
            return Err(BackendError::new(
                "revision_exhausted",
                "集合版本号已达上限，请联系支持处理",
            ));
        }
        let revision = current + 1;
        conn.execute(
            "INSERT INTO revisions (namespace, value) VALUES (?1, ?2)
             ON CONFLICT(namespace) DO UPDATE SET value = excluded.value",
            params![namespace, revision],
        )?;
        Ok(revision)
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
        // 127#7：只读账户连接按路径进程级缓存——原先每条写命令的配额检查
        // 都新开文件+读 header+查询+关闭；打开失败也独立成码（原先泛化
        // storage_failed 引导用户检查数据目录，真实原因是根库不可用）
        let limit = with_accounts(&self.account_path, |accounts| {
            accounts
                .query_row(
                    "SELECT item_limit FROM security_quotas WHERE user_id = ?1 AND resource = ?2",
                    params![user_id, resource],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|error| {
                    BackendError::new(
                        "account_store_unavailable",
                        &format!("账户库不可读，无法校验配额（{error}）"),
                    )
                })
        })?;
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
            _ => return Err(BackendError::new("invalid_quota", "未知的资源配额")),
        };
        let additional = i64::try_from(additional).unwrap_or(i64::MAX);
        if used.saturating_add(additional) > limit {
            return Err(BackendError::new(
                "quota_exceeded",
                &format!(
                    "{}配额已用尽：当前 {used}，上限 {limit}；未写入新内容",
                    quota_label(resource)
                ),
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
            return Err(BackendError::new(
                "invalid_collection",
                "草稿须按标识读取，不能作为普通集合读取",
            ));
        }
        if !Self::namespace_allowed(namespace) {
            return Err(BackendError::new("unknown_collection", "不支持的数据集合"));
        }
        Ok(Collection {
            value: Self::collection_value(&self.conn, namespace, &self.account_path)?,
            revision: Self::revision(&self.conn, namespace)?,
        })
    }

    /// 53#4：在指定连接（如 IMMEDIATE 事务内）读取集合内容——孤儿清理需要
    /// 与 DELETE 同事务地重读任务集合，旧口径「快照读 + 各自 autocommit 的
    /// DELETE」在毫秒窗内会误删并发 adopt/保存刚插入的排期行
    pub(crate) fn collection_value(
        conn: &Connection,
        namespace: &str,
        account_path: &Path,
    ) -> Result<Value> {
        let mut stmt = conn.prepare("SELECT data FROM kv WHERE namespace = ?1 ORDER BY rowid")?;
        let rows = stmt.query_map(params![namespace], |row| row.get::<_, String>(0))?;
        let mut items = Vec::new();
        let mut skipped = 0u64;
        for row in rows {
            // 75#3：单行坏 JSON 只跳过该行——原先直穿 ? 使 app_state/bootstrap
            // 整体失败，一行外部篡改放大为整域不可用
            match serde_json::from_str::<Value>(&row?) {
                Ok(value) => items.push(value),
                Err(_) => skipped += 1,
            }
        }
        if skipped > 0 {
            append_diagnostic(
                account_path,
                &format!("kv namespace {namespace}: {skipped} 行损坏已跳过"),
            );
        }
        Ok(if namespace == "preferences" {
            items.into_iter().next().unwrap_or_else(Self::defaults)
        } else {
            Value::Array(items)
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
        // 31#60：单次取时——原先落库与返回事件各取一次 now()，亚秒漂移不一致
        let created_at = Self::now()?;
        conn.execute(
            "INSERT INTO events (kind, payload, created_at) VALUES (?1, ?2, ?3)",
            params![kind, serde_json::to_string(&payload)?, created_at],
        )?;
        let event = BackendEvent {
            id: conn.last_insert_rowid(),
            kind: kind.to_string(),
            payload,
            created_at,
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

    /// 90#9：写命令轻量回执——replace_collection/save_draft/delete_draft 的
    /// 命令层（light_receipt）只消费 versions/lastEventId/workspaceId 三个
    /// 字段，原先每条写命令 commit 后全量重读四集合 + preferences 反序列化
    /// 再丢弃（notes ≤10000 条 × ≤1MB 时每次保存草稿都附带一次全库读）；
    /// 轻量版只查各 namespace 的 revision 计数与最新事件 id
    fn finish_write_receipt(
        &self,
        tx: rusqlite::Transaction<'_>,
        event: Option<BackendEvent>,
    ) -> Result<Mutation> {
        tx.commit()?;
        let mut versions = Map::new();
        for namespace in COLLECTIONS {
            versions.insert(
                namespace.to_string(),
                Value::from(Self::revision(&self.conn, namespace)?),
            );
        }
        for namespace in ["preferences", "drafts"] {
            versions.insert(
                namespace.to_string(),
                Value::from(Self::revision(&self.conn, namespace)?),
            );
        }
        let state = json!({
            "versions": Value::Object(versions),
            "lastEventId": self.latest_event_id()?,
            "workspaceId": self.owner_id.unwrap_or(0),
        });
        Ok(Mutation { state, event })
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
            return Err(BackendError::new("unknown_collection", "不支持的数据集合"));
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
        // IMMEDIATE 事务：与流式 delta 写入并发时不再触发 BUSY_SNAPSHOT。
        // 25#18：notes 配额的计数检查放进写事务内执行。
        // 127#3/#6：增量基准改用事务内 COUNT——原先事务外把整个 notes
        // 集合全量读出反序列化只为 len()（万行级 IO 放大），且 autocommit
        // 快照与事务内计数在并发删除交叠时使 next-used 被低估、可超限；
        // 事务内读后 IMMEDIATE 串行化，增量恒正确
        let tx = self.write_transaction()?;
        if namespace == "notes" {
            let next = value.as_array().map_or(0, Vec::len) as i64;
            let used: i64 = tx.query_row(
                "SELECT COUNT(*) FROM kv WHERE namespace = 'notes'",
                [],
                |row| row.get(0),
            )?;
            if next > used {
                self.ensure_quota_in_tx(&tx, "notes", (next - used) as u64)?;
            }
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
            for item in value
                .as_array()
                .ok_or_else(|| BackendError::new("collection_must_be_array", "集合必须是数组"))?
            {
                Self::insert_json(&tx, namespace, &record_id(item)?, item)?;
            }
        }
        if namespace == "notes" {
            tx.execute("DELETE FROM learning_annotations WHERE note_id NOT IN (SELECT CAST(id AS INTEGER) FROM kv WHERE namespace = 'notes')", [])?;
        }
        if namespace == "projects" {
            // 98#4：projects 是 kv JSON 集合，project_versions 无 FK 可挂——
            // 前端移除项目（kv 替换）后版本行原先永久残留，workspace 的
            // 200 条上限被死数据侵蚀且 resume 撞「项目不存在」；同 notes
            // 替换 GC learning_annotations 的模式一并清理
            tx.execute("DELETE FROM project_versions WHERE project_id NOT IN (SELECT CAST(id AS INTEGER) FROM kv WHERE namespace = 'projects')", [])?;
        }
        let event = Self::event(
            &tx,
            "state.changed",
            json!({"namespace": namespace, "revision": revision}),
        )?;
        self.finish_write_receipt(tx, Some(event))
    }

    fn validate_draft_id(id: &str) -> Result<()> {
        let suffix = id
            .strip_prefix("deep-tutor-draft-")
            .ok_or_else(|| BackendError::new("invalid_draft_id", "草稿标识不符合应用约定"))?;
        if (suffix == "new" || suffix.parse::<u64>().is_ok_and(|id| id > 0)) && id.len() <= 128 {
            Ok(())
        } else {
            Err(BackendError::new(
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

    pub fn put_draft(
        &self,
        id: &str,
        value: &Value,
        expected_revision: Option<i64>,
    ) -> Result<Mutation> {
        Self::validate_draft_id(id)?;
        validate_draft(value)?;
        // A-11：草稿保存与流式 delta 自动提交并发频繁，读改写用 IMMEDIATE
        //（07#11 同指引）——DEFERRED 读快照过期后升级写锁会得 BUSY_SNAPSHOT
        let tx = self.write_transaction()?;
        // 75#4：可选 revision 守卫——双编辑面并发时后到静默覆盖前值（LWW）；
        // 传入期望版本的调用方与 replace_collection 同口径得到 revision_conflict
        if let Some(expected) = expected_revision {
            let current = Self::revision(&tx, "drafts")?;
            if current != expected {
                return Err(BackendError::new(
                    "revision_conflict",
                    "草稿已在其他窗口更新，请重新读取后再保存",
                ));
            }
        }
        let revision = Self::next_revision(&tx, "drafts")?;
        Self::insert_json(&tx, "drafts", id, value)?;
        let event = Self::event(&tx, "draft.saved", json!({"id": id, "revision": revision}))?;
        self.finish_write_receipt(tx, Some(event))
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
        self.finish_write_receipt(tx, event)
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
        // 当前毫秒时间戳起跳，两个空间不再交叠。
        // 31#58：floor+1——与前端 Date.now() 同毫秒仍可能同值，前移 1ms 消除
        let floor = OffsetDateTime::now_utc().unix_timestamp() * 1000 + 1;
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

    // 92 号：同 EventPage，测试注册面保留
    #[allow(dead_code)]
    pub fn events_since(&self, last_id: i64) -> Result<EventPage> {
        if last_id < 0 {
            return Err(BackendError::new(
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
        // 81#5：has_more 按原始行数——75#3 跳过坏 payload 行后 events.len()
        // 可能 < 201，窗口内恰有坏行时误判「无更多」，按 latest_id 推进游标
        // 会漏页（当前前端零调用，防御未来分页消费方）
        let mut raw_rows = 0usize;
        for row in rows {
            raw_rows += 1;
            let (id, kind, payload, created_at) = row?;
            // 75#3：坏 payload 行跳过——原先直穿使事件轮询分页全失败，
            // 前端从此永远拿不到该页之后的任何事件
            let payload = match serde_json::from_str(&payload) {
                Ok(value) => value,
                Err(_) => {
                    append_diagnostic(
                        &self.account_path,
                        &format!("event #{id} payload 损坏已跳过"),
                    );
                    continue;
                }
            };
            events.push(BackendEvent {
                id,
                kind,
                payload,
                created_at,
            });
        }
        let has_more = raw_rows > 200;
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
        .ok_or_else(|| BackendError::new("missing_field", "记录缺少必需字段"))
}

fn text(object: &Map<String, Value>, key: &str, max: usize, blank: bool) -> Result<()> {
    let value = field(object, key)?
        .as_str()
        .ok_or_else(|| BackendError::new("invalid_field", "文本字段类型不正确"))?;
    if value.len() > max || (!blank && value.trim().is_empty()) {
        return Err(BackendError::new(
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
        Err(BackendError::new("invalid_field", "布尔字段类型不正确"))
    }
}

fn progress(object: &Map<String, Value>, key: &str) -> Result<()> {
    let value = field(object, key)?
        .as_f64()
        .ok_or_else(|| BackendError::new("invalid_field", "进度必须是数字"))?;
    if value.is_finite() && (0.0..=100.0).contains(&value) {
        Ok(())
    } else {
        Err(BackendError::new("invalid_field", "进度必须位于 0 到 100"))
    }
}

fn record_id(record: &Value) -> Result<String> {
    let object = record
        .as_object()
        .ok_or_else(|| BackendError::new("invalid_record", "记录必须是对象"))?;
    let id = object
        .get("id")
        .and_then(Value::as_i64)
        .ok_or_else(|| BackendError::new("invalid_id", "记录标识必须是正整数"))?;
    if id <= 0 {
        return Err(BackendError::new("invalid_id", "记录标识必须是正整数"));
    }
    Ok(id.to_string())
}

fn allowed_fields(object: &Map<String, Value>, allowed: &[&str]) -> Result<()> {
    if object.keys().all(|key| allowed.contains(&key.as_str())) {
        Ok(())
    } else {
        Err(BackendError::new("unknown_field", "记录包含不支持的字段"))
    }
}

pub(crate) fn validate_collection(namespace: &str, value: &Value) -> Result<()> {
    let items = value
        .as_array()
        .ok_or_else(|| BackendError::new("collection_must_be_array", "集合必须是数组"))?;
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
            .ok_or_else(|| BackendError::new("invalid_record", "记录必须是对象"))?;
        if !ids.insert(record_id(item)?) {
            return Err(BackendError::new("duplicate_id", "记录标识不能重复"));
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
                    .ok_or_else(|| BackendError::new("invalid_duration", "时长必须是正整数"))?;
                if !(1..=10_080).contains(&duration) {
                    return Err(BackendError::new(
                        "invalid_duration",
                        "时长必须位于 1 到 10080 分钟",
                    ));
                }
                if !["学习", "练习", "复习", "项目"]
                    .contains(&field(object, "kind")?.as_str().unwrap_or_default())
                {
                    return Err(BackendError::new("invalid_kind", "任务类型不受支持"));
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
                    return Err(BackendError::new(
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
                    return Err(BackendError::new("invalid_kind", "资料类型不受支持"));
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
                let milestones = field(object, "milestones")?
                    .as_array()
                    .ok_or_else(|| BackendError::new("invalid_milestones", "里程碑必须是数组"))?;
                if milestones.len() > 200 {
                    return Err(BackendError::new("quota_exceeded", "里程碑数量超过上限"));
                }
                let mut milestone_ids = BTreeSet::new();
                for milestone in milestones {
                    let object = milestone.as_object().ok_or_else(|| {
                        BackendError::new("invalid_milestone", "里程碑必须是对象")
                    })?;
                    allowed_fields(object, &["id", "title", "done"])?;
                    if !milestone_ids.insert(record_id(milestone)?) {
                        return Err(BackendError::new("duplicate_id", "里程碑标识不能重复"));
                    }
                    text(object, "title", 500, false)?;
                    boolean(object, "done")?;
                }
            }
            _ => return Err(BackendError::new("unknown_collection", "不支持的数据集合")),
        }
    }
    Ok(())
}

pub(crate) fn validate_preferences(value: &Value) -> Result<()> {
    let object = value
        .as_object()
        .ok_or_else(|| BackendError::new("invalid_preferences", "偏好必须是对象"))?;
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
        return Err(BackendError::new(
            "invalid_weekly_hours",
            "每周投入须为 1 到 80 小时",
        ));
    }
    if object.contains_key("planIntensity")
        && !["稳妥", "标准", "冲刺"]
            .contains(&field(object, "planIntensity")?.as_str().unwrap_or_default())
    {
        return Err(BackendError::new(
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
                return Err(BackendError::new(
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
    // 127#14：theme 与其余字段同为可选——原先唯一隐式必填，任何未来的
    // 偏好局部更新路径会被 missing_field 拒绝且错误不指向字段；缺省回落 light
    if object.contains_key("theme")
        && !["light", "dark"].contains(&field(object, "theme")?.as_str().unwrap_or_default())
    {
        return Err(BackendError::new("invalid_theme", "主题不受支持"));
    }
    Ok(())
}

pub(crate) fn validate_draft(value: &Value) -> Result<()> {
    let object = value
        .as_object()
        .ok_or_else(|| BackendError::new("invalid_draft", "草稿必须是对象"))?;
    allowed_fields(object, &["id", "title", "content", "tag", "savedAt"])?;
    if serde_json::to_vec(value)?.len() > MAX_RECORD_BYTES {
        return Err(BackendError::new("record_too_large", "草稿超过 1 MB 上限"));
    }
    text(object, "title", 500, true)?;
    text(object, "content", MAX_RECORD_BYTES, true)?;
    text(object, "tag", 100, false)?;
    if object.contains_key("savedAt") && !field(object, "savedAt")?.is_number() {
        return Err(BackendError::new("invalid_field", "草稿保存时间必须是数字"));
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

    // 64#4：并发写实际并发性只有多连接才真实（生产在途命令与登录换库各持
    // 连接，前端 inflight 串行化不是后端防线）——两连接并发提交，
    // IMMEDIATE+busy_timeout 下无死锁，revision 严格递增、无丢失更新
    #[test]
    fn concurrent_replace_collection_across_connections_serializes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("concurrent.db").to_path_buf();
        let boot = Store::new(&path).unwrap();
        boot.bootstrap().unwrap();
        drop(boot);
        let workers = 2;
        let per_worker = 25;
        let appended = std::thread::scope(|scope| {
            let handles = (0..workers)
                .map(|worker| {
                    let path = path.clone();
                    scope.spawn(move || {
                        let store = Store::new(&path).unwrap();
                        store.bootstrap().unwrap();
                        let mut succeeded = 0usize;
                        for index in 0..per_worker {
                            loop {
                                let current = store.collection("tasks").unwrap();
                                let mut next =
                                    current.value.as_array().cloned().unwrap_or_default();
                                next.push(task(((worker + 1) * 1000 + index) as i64));
                                match store.replace_collection(
                                    "tasks",
                                    current.revision,
                                    &Value::Array(next),
                                ) {
                                    Ok(_) => {
                                        succeeded += 1;
                                        break;
                                    }
                                    Err(error) if error.code == "revision_conflict" => continue,
                                    Err(error) => panic!("并发提交出现意外错误：{error:?}"),
                                }
                            }
                        }
                        succeeded
                    })
                })
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap())
                .sum::<usize>()
        });
        assert_eq!(appended, workers * per_worker);
        let final_store = Store::new(&path).unwrap();
        let tasks = final_store.collection("tasks").unwrap();
        let items = tasks.value.as_array().unwrap();
        assert_eq!(items.len(), workers * per_worker);
        assert_eq!(tasks.revision, (workers * per_worker) as i64);
        let mut ids = items
            .iter()
            .map(|item| item["id"].as_i64().unwrap())
            .collect::<Vec<_>>();
        ids.sort();
        ids.dedup();
        assert_eq!(
            ids.len(),
            workers * per_worker,
            "并发追加不得丢失或重复任务"
        );
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
                None,
            )
            .unwrap();
        store
            .put_draft(
                "deep-tutor-draft-1",
                &json!({"title": "二", "content": "乙", "tag": "复盘", "savedAt": 2}),
                None,
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
            .put_draft(
                "bad",
                &json!({"title": "", "content": "", "tag": "复盘"}),
                None
            )
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
                    None,
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

    #[test]
    fn schema_version_gate_restores_missing_columns_on_reopen() {
        // 34#1：31#1 之前的老库缺 provider_requests.agent_session_id 列，
        // 但其指纹由旧代码在旧 schema 上计算——重开时指纹自洽，只有
        // schema_version 门禁（1 < 2）能触发补跑 migrate 恢复缺列
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("app.db");
        {
            let store = Store::root(&path).unwrap();
            // 53#1 索引引用该列，SQLite 的 DROP COLUMN 拒绝删除被索引引用的
            // 列——老库模拟先摘索引（真实老库本就没有该索引）
            store
                .conn
                .execute("DROP INDEX IF EXISTS provider_requests_session", [])
                .unwrap();
            store
                .conn
                .execute(
                    "ALTER TABLE provider_requests DROP COLUMN agent_session_id",
                    [],
                )
                .unwrap();
            // 重记缺列 schema 的指纹并回退版本号，精确模拟老库：指纹匹配、版本 1
            let stale = Store::schema_fingerprint(&store.conn).unwrap();
            store
                .conn
                .execute(
                    "UPDATE meta SET value = ?1 WHERE key = 'schema_fingerprint'",
                    params![stale],
                )
                .unwrap();
            store
                .conn
                .execute(
                    "UPDATE meta SET value = '1' WHERE key = 'schema_version'",
                    [],
                )
                .unwrap();
        }
        let baseline = migration_count();
        let reopened = Store::root(&path).unwrap();
        assert_eq!(migration_count(), baseline + 1);
        let columns: Vec<String> = reopened
            .conn
            .prepare("SELECT name FROM pragma_table_info('provider_requests')")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        assert!(
            columns.iter().any(|column| column == "agent_session_id"),
            "版本门禁必须补跑 migrate 恢复缺列"
        );
        let version: String = reopened
            .conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_version'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        // 152#2：SCHEMA_VERSION 递增至 4（129 波索引迁移补登记）
        assert_eq!(version, "4");
    }
}
