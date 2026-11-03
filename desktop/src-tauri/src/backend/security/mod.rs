pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
#[cfg(test)]
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
#[cfg(test)]
use std::sync::Mutex;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime};
use zeroize::Zeroizing;

const SERVICE: &str = "deep-tutor-security";
const SESSION_SECONDS: i64 = 7 * 24 * 60 * 60;
const RESOURCES: [(&str, u64); 4] = [
    ("sessions", 200),
    ("notes", 500),
    ("materials", 200),
    ("threads", 500),
];

trait CredentialBackend: Send + Sync {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>>;
    fn set(&self, id: &str, value: &str) -> Result<()>;
    fn delete(&self, id: &str) -> Result<()>;
}

struct SystemCredentials;

impl CredentialBackend for SystemCredentials {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>> {
        let entry = keyring::Entry::new(SERVICE, id)
            .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?;
        match entry.get_password() {
            Ok(value) => Ok(Some(Zeroizing::new(value))),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(BackendError::new("credential_error", "无法读取系统密钥库")),
        }
    }

    fn set(&self, id: &str, value: &str) -> Result<()> {
        keyring::Entry::new(SERVICE, id)
            .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?
            .set_password(value)
            .map_err(|_| BackendError::new("credential_error", "无法写入系统密钥库"))
    }

    fn delete(&self, id: &str) -> Result<()> {
        match keyring::Entry::new(SERVICE, id)
            .map_err(|_| BackendError::new("credential_error", "无法访问系统密钥库"))?
            .delete_credential()
        {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(BackendError::new(
                "credential_cleanup_failed",
                "无法删除系统密钥库中的会话",
            )),
        }
    }
}

#[cfg(test)]
#[derive(Default)]
struct MemoryCredentials(Mutex<HashMap<String, Zeroizing<String>>>);

#[cfg(test)]
impl CredentialBackend for MemoryCredentials {
    fn get(&self, id: &str) -> Result<Option<Zeroizing<String>>> {
        Ok(self
            .0
            .lock()
            .map_err(|_| BackendError::new("credential_error", "测试密钥库不可用"))?
            .get(id)
            .cloned())
    }
    fn set(&self, id: &str, value: &str) -> Result<()> {
        self.0
            .lock()
            .map_err(|_| BackendError::new("credential_error", "测试密钥库不可用"))?
            .insert(id.to_string(), Zeroizing::new(value.to_string()));
        Ok(())
    }
    fn delete(&self, id: &str) -> Result<()> {
        self.0
            .lock()
            .map_err(|_| BackendError::new("credential_error", "测试密钥库不可用"))?
            .remove(id);
        Ok(())
    }
}

pub struct SecurityState {
    database_path: PathBuf,
    credentials: Arc<dyn CredentialBackend>,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
}

impl SecurityState {
    pub fn new(path: &Path) -> Self {
        Self {
            database_path: path.to_path_buf(),
            credentials: Arc::new(SystemCredentials),
            #[cfg(test)]
            _guard: None,
        }
    }

    #[cfg(test)]
    pub fn shared(&self) -> Self {
        Self {
            database_path: self.database_path.clone(),
            credentials: Arc::clone(&self.credentials),
            #[cfg(test)]
            _guard: None,
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        Ok(Self {
            database_path: guard.path().join("app.db"),
            credentials: Arc::new(MemoryCredentials::default()),
            _guard: Some(guard),
        })
    }

    fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalUser {
    pub id: i64,
    pub name: String,
    pub role: String,
    pub active: bool,
    pub current: bool,
    pub created_at: String,
    pub last_login_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionView {
    pub id: String,
    pub created_at: String,
    pub expires_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenView {
    pub id: i64,
    pub name: String,
    pub mask: String,
    pub created_at: String,
    pub last_used_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreatedToken {
    pub token: String,
    pub view: TokenView,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuotaView {
    pub resource: String,
    pub used: u64,
    pub limit: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthMode {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    pub detail: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SecurityOverview {
    pub users: Vec<LocalUser>,
    pub current_user: Option<LocalUser>,
    pub session: Option<SessionView>,
    pub requires_login: bool,
    pub tokens: Vec<TokenView>,
    pub quotas: Vec<QuotaView>,
    pub modes: Vec<AuthMode>,
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS local_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('管理员', '成员')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS auth_sessions_latest ON auth_sessions(revoked_at, created_at);
        CREATE TABLE IF NOT EXISTS access_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            prefix TEXT NOT NULL,
            suffix TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS security_quotas (
            user_id INTEGER NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
            resource TEXT NOT NULL,
            item_limit INTEGER NOT NULL CHECK(item_limit > 0),
            PRIMARY KEY(user_id, resource)
        );",
    )?;
    Ok(())
}

fn now() -> Result<OffsetDateTime> {
    Ok(OffsetDateTime::now_utc())
}

fn timestamp(value: OffsetDateTime) -> Result<String> {
    Ok(value.format(&Rfc3339)?)
}

fn hash(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn user(conn: &Connection, id: i64, current_id: i64) -> Result<LocalUser> {
    conn.query_row(
        "SELECT id, display_name, role, active, created_at, last_login_at FROM local_users WHERE id = ?1 AND deleted_at IS NULL",
        params![id],
        |row| Ok(LocalUser { id: row.get(0)?, name: row.get(1)?, role: row.get(2)?, active: row.get(3)?, current: id == current_id, created_at: row.get(4)?, last_login_at: row.get(5)? }),
    ).optional()?.ok_or_else(|| BackendError::new("not_found", "本地用户不存在"))
}

fn active_user(conn: &Connection, id: i64) -> Result<LocalUser> {
    let user = user(conn, id, id)?;
    if !user.active {
        return Err(BackendError::new("account_disabled", "该本地用户已停用"));
    }
    Ok(user)
}

fn require_admin(conn: &Connection, id: i64) -> Result<()> {
    let user = active_user(conn, id)?;
    if user.role != "管理员" {
        return Err(BackendError::new(
            "permission_denied",
            "只有管理员可以管理用户和配额",
        ));
    }
    Ok(())
}

fn decode(value: &str) -> Result<Vec<u8>> {
    if value.is_empty() || value.len() > 4096 {
        return Err(BackendError::new("invalid_session", "会话格式无效"));
    }
    let mut accumulator = 0_u32;
    let mut bits = 0_u8;
    let mut bytes = Vec::new();
    for byte in value.bytes() {
        let digit = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'-' => 62,
            b'_' => 63,
            _ => return Err(BackendError::new("invalid_session", "会话格式无效")),
        };
        accumulator = (accumulator << 6) | u32::from(digit);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            bytes.push((accumulator >> bits) as u8);
            accumulator &= (1_u32 << bits).saturating_sub(1);
        }
    }
    if accumulator != 0 || super::random::encode(&bytes) != value {
        return Err(BackendError::new("invalid_session", "会话格式无效"));
    }
    Ok(bytes)
}

fn signature(secret: &str, value: &str) -> Vec<u8> {
    let mut outer = [0x5c_u8; 64];
    let mut inner = [0x36_u8; 64];
    let mut key_block = [0_u8; 64];
    if secret.len() > 64 {
        key_block[..32].copy_from_slice(&Sha256::digest(secret.as_bytes()));
    } else {
        key_block[..secret.len()].copy_from_slice(secret.as_bytes());
    }
    for (index, byte) in key_block.iter().enumerate() {
        outer[index] ^= byte;
        inner[index] ^= byte;
    }
    let mut hasher = Sha256::new();
    hasher.update(inner);
    hasher.update(value.as_bytes());
    let inner_hash = hasher.finalize();
    let mut hasher = Sha256::new();
    hasher.update(outer);
    hasher.update(inner_hash);
    hasher.finalize().to_vec()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtHeader {
    alg: String,
    typ: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtPayload {
    sub: String,
    sid: String,
    iat: i64,
    exp: i64,
}

fn jwt(
    secret: &str,
    session_id: &str,
    user_id: i64,
    issued_at: i64,
    expires_at: i64,
) -> Result<Zeroizing<String>> {
    let header = super::random::encode(&serde_json::to_vec(&json!({"alg":"HS256","typ":"JWT"}))?);
    let payload = super::random::encode(&serde_json::to_vec(
        &json!({"sub":user_id.to_string(),"sid":session_id,"iat":issued_at,"exp":expires_at}),
    )?);
    let content = format!("{header}.{payload}");
    let signature = super::random::encode(&signature(secret, &content));
    Ok(Zeroizing::new(format!("{content}.{signature}")))
}

fn secret(state: &SecurityState) -> Result<Zeroizing<String>> {
    if let Some(secret) = state.credentials.get("jwt-secret")? {
        return Ok(secret);
    }
    let secret = Zeroizing::new(super::random::token("")?);
    state.credentials.set("jwt-secret", &secret)?;
    Ok(secret)
}

fn current(conn: &Connection, state: &SecurityState) -> Result<(LocalUser, SessionView)> {
    let (id, created_at, expires_at): (String, String, String) = conn.query_row(
        "SELECT id, created_at, expires_at FROM auth_sessions WHERE revoked_at IS NULL ORDER BY created_at DESC, rowid DESC LIMIT 1",
        [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    ).optional()?.ok_or_else(|| BackendError::new("login_required", "请选择本地用户登录"))?;
    let token = state
        .credentials
        .get(&format!("session-{id}"))?
        .ok_or_else(|| BackendError::new("login_required", "本地会话凭据不可用，请重新选择用户"))?;
    let user = verify(conn, state, &token)?;
    Ok((
        user,
        SessionView {
            id,
            created_at,
            expires_at,
        },
    ))
}

fn verify(conn: &Connection, state: &SecurityState, token: &str) -> Result<LocalUser> {
    let segments = token.split('.').collect::<Vec<_>>();
    if segments.len() != 3 || token.len() > 4096 {
        return Err(BackendError::new("invalid_session", "会话格式无效"));
    }
    let header: JwtHeader = serde_json::from_slice(&decode(segments[0])?)
        .map_err(|_| BackendError::new("invalid_session", "会话头部无效"))?;
    if header.alg != "HS256" || header.typ != "JWT" {
        return Err(BackendError::new("invalid_session", "会话签名算法不受支持"));
    }
    let payload: JwtPayload = serde_json::from_slice(&decode(segments[1])?)
        .map_err(|_| BackendError::new("invalid_session", "会话载荷无效"))?;
    let expected = signature(&secret(state)?, &format!("{}.{}", segments[0], segments[1]));
    let provided = decode(segments[2])?;
    if expected.len() != provided.len()
        || !expected
            .iter()
            .zip(provided.iter())
            .all(|(left, right)| *left == *right)
    {
        return Err(BackendError::new("invalid_session", "会话签名无效"));
    }
    if payload.sid.is_empty()
        || payload.sid.len() > 64
        || payload.exp <= now()?.unix_timestamp()
        || payload.exp > payload.iat + SESSION_SECONDS + 60
        || payload.iat > now()?.unix_timestamp() + 30
    {
        return Err(BackendError::new(
            "session_expired",
            "会话已过期或有效期无效，请重新登录",
        ));
    }
    let user_id = payload
        .sub
        .parse::<i64>()
        .map_err(|_| BackendError::new("invalid_session", "会话用户标识无效"))?;
    let expires = timestamp(
        OffsetDateTime::from_unix_timestamp(payload.exp)
            .map_err(|_| BackendError::new("invalid_session", "会话到期时间无效"))?,
    )?;
    let valid: bool = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM auth_sessions WHERE id = ?1 AND user_id = ?2 AND expires_at = ?3 AND revoked_at IS NULL)",
        params![payload.sid, user_id, expires],
        |row| row.get(0),
    )?;
    if !valid {
        return Err(BackendError::new("session_revoked", "会话已撤销或不存在"));
    }
    active_user(conn, user_id)
}

fn issue<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    user_id: i64,
) -> Result<SessionView> {
    let store = state.store()?;
    active_user(&store.conn, user_id)?;
    let issued = OffsetDateTime::from_unix_timestamp(now()?.unix_timestamp())
        .map_err(|_| BackendError::new("invalid_time", "无法建立会话时间"))?;
    let expires = issued + Duration::seconds(SESSION_SECONDS);
    let id = super::random::token("")?;
    let token = jwt(
        &secret(state)?,
        &id,
        user_id,
        issued.unix_timestamp(),
        expires.unix_timestamp(),
    )?;
    let view = SessionView {
        id: id.clone(),
        created_at: timestamp(issued)?,
        expires_at: timestamp(expires)?,
    };
    state.credentials.set(&format!("session-{id}"), &token)?;
    let result = (|| {
        let tx = store.conn.unchecked_transaction()?;
        tx.execute("INSERT INTO auth_sessions (id, user_id, created_at, expires_at) VALUES (?1, ?2, ?3, ?4)", params![id, user_id, view.created_at, view.expires_at])?;
        tx.execute(
            "UPDATE local_users SET last_login_at = ?1 WHERE id = ?2",
            params![view.created_at, user_id],
        )?;
        let event = Store::event(
            &tx,
            "security.session_started",
            json!({"userId":user_id,"sessionId":id,"expiresAt":view.expires_at}),
        )?;
        tx.commit()?;
        emit(app, &event);
        Ok(view)
    })();
    if result.is_err() {
        let _ = state.credentials.delete(&format!("session-{id}"));
    }
    result
}

fn ensure_accounts(state: &SecurityState) -> Result<()> {
    let store = state.store()?;
    let count: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM local_users", [], |row| row.get(0))?;
    if count > 0 {
        return Ok(());
    }
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO local_users (display_name, role, created_at, updated_at) VALUES ('本地管理员', '管理员', ?1, ?1)", params![timestamp])?;
    let user_id = tx.last_insert_rowid();
    for (resource, limit) in RESOURCES {
        tx.execute(
            "INSERT INTO security_quotas (user_id, resource, item_limit) VALUES (?1, ?2, ?3)",
            params![user_id, resource, limit as i64],
        )?;
    }
    tx.commit()?;
    Ok(())
}

fn initialize<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
) -> Result<(LocalUser, SessionView)> {
    ensure_accounts(state)?;
    let store = state.store()?;
    match current(&store.conn, state) {
        Ok(current) => Ok(current),
        Err(error) if error.code == "login_required" => {
            let id: i64 = store.conn.query_row("SELECT id FROM local_users WHERE role = '管理员' AND active = 1 AND deleted_at IS NULL ORDER BY id LIMIT 1", [], |row| row.get(0))?;
            let session = issue(app, state, id)?;
            Ok((user(&store.conn, id, id)?, session))
        }
        Err(error) => Err(error),
    }
}

fn overview_from(state: &SecurityState) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current_user, session) = current(&store.conn, state)?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM local_users WHERE deleted_at IS NULL ORDER BY id")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let users = ids
        .into_iter()
        .map(|id| user(&store.conn, id, current_user.id))
        .collect::<Result<Vec<_>>>()?;
    let mut statement = store.conn.prepare("SELECT id, name, prefix, suffix, created_at, last_used_at FROM access_tokens WHERE user_id = ?1 AND revoked_at IS NULL ORDER BY id DESC")?;
    let tokens = statement
        .query_map(params![current_user.id], |row| {
            let prefix: String = row.get(2)?;
            let suffix: String = row.get(3)?;
            Ok(TokenView {
                id: row.get(0)?,
                name: row.get(1)?,
                mask: format!("{prefix}••••••••{suffix}"),
                created_at: row.get(4)?,
                last_used_at: row.get(5)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut quotas = Vec::new();
    for (resource, _) in RESOURCES {
        let limit: i64 = store.conn.query_row(
            "SELECT item_limit FROM security_quotas WHERE user_id = ?1 AND resource = ?2",
            params![current_user.id, resource],
            |row| row.get(0),
        )?;
        let used: i64 = match resource {
            "sessions" => {
                store
                    .conn
                    .query_row("SELECT COUNT(*) FROM agent_sessions", [], |row| row.get(0))?
            }
            "notes" => store.conn.query_row(
                "SELECT COUNT(*) FROM kv WHERE namespace = 'notes'",
                [],
                |row| row.get(0),
            )?,
            "materials" => store
                .conn
                .query_row("SELECT COUNT(*) FROM materials", [], |row| row.get(0))?,
            "threads" => {
                store
                    .conn
                    .query_row("SELECT COUNT(*) FROM community_posts", [], |row| row.get(0))?
            }
            _ => return Err(BackendError::new("invalid_quota", "未知的资源配额")),
        };
        quotas.push(QuotaView {
            resource: resource.to_string(),
            used: used as u64,
            limit: limit as u64,
        });
    }
    Ok(SecurityOverview {
        users,
        current_user: Some(current_user),
        session: Some(session),
        requires_login: false,
        tokens,
        quotas,
        modes: vec![
            AuthMode {
                id: "jwt".into(),
                label: "本地 JWT 会话 · 7 天".into(),
                enabled: true,
                detail: "HS256 签名、到期与撤销由原生后端验证，明文只存系统密钥库".into(),
            },
            AuthMode {
                id: "pat".into(),
                label: "个人访问令牌 PAT".into(),
                enabled: true,
                detail: "系统随机令牌；数据库仅保存 SHA-256 摘要，明文只返回一次".into(),
            },
            AuthMode {
                id: "env".into(),
                label: ".env token".into(),
                enabled: false,
                detail: "已废止，不读取环境变量充当应用凭据".into(),
            },
            AuthMode {
                id: "open".into(),
                label: "开放本机模式".into(),
                enabled: false,
                detail: "不开放匿名管理操作；本地身份选择不代替 Windows 账户认证".into(),
            },
        ],
    })
}

fn logged_out_overview(state: &SecurityState) -> Result<SecurityOverview> {
    let store = state.store()?;
    let mut statement = store
        .conn
        .prepare("SELECT id FROM local_users WHERE deleted_at IS NULL ORDER BY id")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let users = ids
        .into_iter()
        .map(|id| user(&store.conn, id, 0))
        .collect::<Result<Vec<_>>>()?;
    Ok(SecurityOverview {
        users,
        current_user: None,
        session: None,
        requires_login: true,
        tokens: vec![],
        quotas: vec![],
        modes: vec![
            AuthMode {
                id: "jwt".into(),
                label: "本地 JWT 会话 · 7 天".into(),
                enabled: true,
                detail: "HS256 签名、到期与撤销由原生后端验证，明文只存系统密钥库".into(),
            },
            AuthMode {
                id: "pat".into(),
                label: "个人访问令牌 PAT".into(),
                enabled: true,
                detail: "系统随机令牌；数据库仅保存 SHA-256 摘要，明文只返回一次".into(),
            },
            AuthMode {
                id: "env".into(),
                label: ".env token".into(),
                enabled: false,
                detail: "已废止，不读取环境变量充当应用凭据".into(),
            },
            AuthMode {
                id: "open".into(),
                label: "开放本机模式".into(),
                enabled: false,
                detail: "不开放匿名管理操作；本地身份选择不代替 Windows 账户认证".into(),
            },
        ],
    })
}

pub fn overview<R: Runtime>(app: &AppHandle<R>, state: &SecurityState) -> Result<SecurityOverview> {
    match initialize(app, state) {
        Ok(_) => overview_from(state),
        Err(error)
            if [
                "invalid_session",
                "session_expired",
                "session_revoked",
                "account_disabled",
            ]
            .contains(&error.code.as_str()) =>
        {
            logged_out_overview(state)
        }
        Err(error) => Err(error),
    }
}

pub fn login<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    user_id: i64,
) -> Result<SecurityOverview> {
    ensure_accounts(state)?;
    issue(app, state, user_id)?;
    overview_from(state)
}

pub fn logout<R: Runtime>(app: &AppHandle<R>, state: &SecurityState) -> Result<()> {
    let store = state.store()?;
    let (user, session) = current(&store.conn, state)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE auth_sessions SET revoked_at = ?1 WHERE id = ?2",
        params![timestamp(now()?)?, session.id],
    )?;
    let event = Store::event(
        &tx,
        "security.session_revoked",
        json!({"sessionId":session.id,"userId":user.id}),
    )?;
    tx.commit()?;
    state
        .credentials
        .delete(&format!("session-{}", session.id))?;
    emit(app, &event);
    Ok(())
}

pub fn create_user<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    name: String,
    role: String,
) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    require_admin(&store.conn, current.id)?;
    let name = name.trim().to_string();
    if name.is_empty() || name.len() > 80 || !["管理员", "成员"].contains(&role.as_str()) {
        return Err(BackendError::invalid(
            "invalid_user",
            "用户名称须为 1 到 80 字节，角色须为管理员或成员",
        ));
    }
    let exists: bool = store.conn.query_row("SELECT EXISTS(SELECT 1 FROM local_users WHERE display_name = ?1 COLLATE NOCASE AND deleted_at IS NULL)", params![name], |row| row.get(0))?;
    if exists {
        return Err(BackendError::new("user_exists", "同名本地用户已存在"));
    }
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO local_users (display_name, role, created_at, updated_at) VALUES (?1, ?2, ?3, ?3)", params![name, role, timestamp])?;
    let id = tx.last_insert_rowid();
    for (resource, limit) in RESOURCES {
        tx.execute(
            "INSERT INTO security_quotas (user_id, resource, item_limit) VALUES (?1, ?2, ?3)",
            params![id, resource, limit as i64],
        )?;
    }
    let event = Store::event(
        &tx,
        "security.user_created",
        json!({"userId":id,"role":role}),
    )?;
    tx.commit()?;
    emit(app, &event);
    overview_from(state)
}

fn revoke_user_access(conn: &Connection, id: i64, timestamp: &str) -> Result<Vec<String>> {
    let mut statement =
        conn.prepare("SELECT id FROM auth_sessions WHERE user_id = ?1 AND revoked_at IS NULL")?;
    let sessions = statement
        .query_map(params![id], |row| row.get::<_, String>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ?1 WHERE user_id = ?2 AND revoked_at IS NULL",
        params![timestamp, id],
    )?;
    conn.execute(
        "UPDATE access_tokens SET revoked_at = ?1 WHERE user_id = ?2 AND revoked_at IS NULL",
        params![timestamp, id],
    )?;
    Ok(sessions)
}

fn check_user_change(conn: &Connection, current: &LocalUser, id: i64, active: bool) -> Result<()> {
    require_admin(conn, current.id)?;
    if id == current.id && !active {
        return Err(BackendError::new(
            "cannot_disable_current",
            "不能停用当前登录用户",
        ));
    }
    let target = user(conn, id, current.id)?;
    if target.role == "管理员" && !active {
        let administrators: i64 = conn.query_row("SELECT COUNT(*) FROM local_users WHERE role = '管理员' AND active = 1 AND deleted_at IS NULL", [], |row| row.get(0))?;
        if administrators <= 1 {
            return Err(BackendError::new(
                "last_admin",
                "至少需要保留一名可用管理员",
            ));
        }
    }
    Ok(())
}

pub fn set_active<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    user_id: i64,
    active: bool,
) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    check_user_change(&store.conn, &current, user_id, active)?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE local_users SET active = ?1, updated_at = ?2 WHERE id = ?3",
        params![active, timestamp, user_id],
    )?;
    let sessions = if active {
        vec![]
    } else {
        revoke_user_access(&tx, user_id, &timestamp)?
    };
    let event = Store::event(
        &tx,
        "security.user_updated",
        json!({"userId":user_id,"active":active}),
    )?;
    tx.commit()?;
    for session in sessions {
        state.credentials.delete(&format!("session-{session}"))?;
    }
    emit(app, &event);
    overview_from(state)
}

pub fn remove_user<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    user_id: i64,
) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    check_user_change(&store.conn, &current, user_id, false)?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE local_users SET active = 0, deleted_at = ?1, updated_at = ?1 WHERE id = ?2",
        params![timestamp, user_id],
    )?;
    let sessions = revoke_user_access(&tx, user_id, &timestamp)?;
    let event = Store::event(
        &tx,
        "security.user_removed",
        json!({"userId":user_id,"dataPreserved":true}),
    )?;
    tx.commit()?;
    for session in sessions {
        state.credentials.delete(&format!("session-{session}"))?;
    }
    emit(app, &event);
    overview_from(state)
}

fn token_view(conn: &Connection, id: i64) -> Result<TokenView> {
    conn.query_row("SELECT id, name, prefix, suffix, created_at, last_used_at FROM access_tokens WHERE id = ?1", params![id], |row| {
        let prefix: String = row.get(2)?;
        let suffix: String = row.get(3)?;
        Ok(TokenView { id: row.get(0)?, name: row.get(1)?, mask: format!("{prefix}••••••••{suffix}"), created_at: row.get(4)?, last_used_at: row.get(5)? })
    }).optional()?.ok_or_else(|| BackendError::new("not_found", "访问令牌不存在"))
}

pub fn create_token<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    name: String,
) -> Result<CreatedToken> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    let name = name.trim().to_string();
    if name.is_empty() || name.len() > 60 {
        return Err(BackendError::invalid(
            "invalid_token_name",
            "令牌名称须为 1 到 60 字节",
        ));
    }
    let token = super::random::token("dtp_")?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO access_tokens (user_id, name, token_hash, prefix, suffix, created_at) VALUES (?1, ?2, ?3, 'dtp_', ?4, ?5)", params![current.id, name, hash(&token), &token[token.len()-4..], timestamp(now()?)?])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "security.token_created",
        json!({"tokenId":id,"userId":current.id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(CreatedToken {
        token,
        view: token_view(&store.conn, id)?,
    })
}

pub fn revoke_token<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    token_id: i64,
) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    let owner: i64 = store
        .conn
        .query_row(
            "SELECT user_id FROM access_tokens WHERE id = ?1 AND revoked_at IS NULL",
            params![token_id],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "访问令牌不存在或已吊销"))?;
    if owner != current.id {
        return Err(BackendError::new("not_found", "访问令牌不存在或已吊销"));
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE access_tokens SET revoked_at = ?1 WHERE id = ?2",
        params![timestamp(now()?)?, token_id],
    )?;
    let event = Store::event(
        &tx,
        "security.token_revoked",
        json!({"tokenId":token_id,"userId":current.id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    overview_from(state)
}

pub fn authenticate_token<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    token: String,
) -> Result<LocalUser> {
    let suffix = token.strip_prefix("dtp_").unwrap_or_default();
    if suffix.len() != 43
        || !suffix
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(BackendError::new("invalid_token", "访问令牌格式无效"));
    }
    let store = state.store()?;
    let (id, user_id): (i64, i64) = store.conn.query_row(
        "SELECT t.id, t.user_id FROM access_tokens t JOIN local_users u ON u.id = t.user_id WHERE t.token_hash = ?1 AND t.revoked_at IS NULL AND u.active = 1 AND u.deleted_at IS NULL",
        params![hash(&token)], |row| Ok((row.get(0)?, row.get(1)?)),
    ).optional()?.ok_or_else(|| BackendError::new("invalid_token", "访问令牌无效、已吊销或所属用户已停用"))?;
    let timestamp = timestamp(now()?)?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE access_tokens SET last_used_at = ?1 WHERE id = ?2",
        params![timestamp, id],
    )?;
    let event = Store::event(
        &tx,
        "security.token_authenticated",
        json!({"tokenId":id,"userId":user_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    user(&store.conn, user_id, user_id)
}

pub fn set_quota<R: Runtime>(
    app: &AppHandle<R>,
    state: &SecurityState,
    user_id: i64,
    resource: String,
    limit: u64,
) -> Result<SecurityOverview> {
    let store = state.store()?;
    let (current, _) = current(&store.conn, state)?;
    require_admin(&store.conn, current.id)?;
    active_user(&store.conn, user_id)?;
    if !RESOURCES.iter().any(|(name, _)| name == &resource) || !(1..=100_000).contains(&limit) {
        return Err(BackendError::invalid(
            "invalid_quota",
            "配额资源无效或上限须为 1 到 100000",
        ));
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE security_quotas SET item_limit = ?1 WHERE user_id = ?2 AND resource = ?3",
        params![limit as i64, user_id, resource],
    )?;
    let event = Store::event(
        &tx,
        "security.quota_updated",
        json!({"userId":user_id,"resource":resource,"limit":limit}),
    )?;
    tx.commit()?;
    emit(app, &event);
    overview_from(state)
}
