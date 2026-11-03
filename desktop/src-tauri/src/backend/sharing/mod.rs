pub mod commands;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{
    Graph, LearningState, PlanResult, Schedule, ScheduledStage, ValidationReport,
};
use super::materials;
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

const MAX_PACKAGE_BYTES: usize = 2 * 1024 * 1024;

pub struct SharingState {
    learning: LearningState,
}

impl SharingState {
    pub fn new(learning: &LearningState) -> Self {
        Self {
            learning: learning.shared(),
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        Ok(Self {
            learning: LearningState::for_tests()?,
        })
    }

    pub fn store(&self) -> Result<Store> {
        self.learning.store()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SharedPlanSnapshot {
    pub goal: String,
    pub deadline: String,
    pub daily_minutes: u32,
    pub plan_version: u32,
    pub graph: Graph,
    pub stages: Vec<ScheduledStage>,
    pub schedule: Schedule,
    pub validation: ValidationReport,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShareMetadata {
    pub id: i64,
    pub plan_id: String,
    pub plan_version: u32,
    pub title: String,
    pub status: String,
    pub created_at: String,
    pub expires_at: String,
    pub revoked_at: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreatedShare {
    pub share: ShareMetadata,
    pub token: String,
    pub package: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SharedPlanView {
    pub plan: SharedPlanSnapshot,
    pub expires_at: String,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SharePackage {
    pub format: String,
    pub version: u32,
    pub token: String,
    pub plan: SharedPlanSnapshot,
    pub expires_at: String,
    pub checksum: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PackagePayload<'a> {
    token: &'a str,
    plan: &'a SharedPlanSnapshot,
    expires_at: &'a str,
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS shared_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
            plan_version INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS shared_plans_plan ON shared_plans(plan_id);",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM shared_plans", [])?;
    Ok(())
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

fn expiration(value: &str) -> Result<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| BackendError::new("invalid_share_expiry", "分享有效期不是有效时间"))
}

fn token_hash(token: &str) -> String {
    format!("{:x}", Sha256::digest(token.as_bytes()))
}

fn new_token() -> Result<String> {
    super::random::token("dt_")
}

fn validate_token(token: &str) -> Result<()> {
    let suffix = token.strip_prefix("dt_").unwrap_or_default();
    if suffix.len() == 43
        && suffix
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        Ok(())
    } else {
        Err(BackendError::invalid(
            "invalid_share_token",
            "分享令牌格式无效",
        ))
    }
}

fn snapshot(plan: &super::learning::Plan, result: &PlanResult) -> SharedPlanSnapshot {
    SharedPlanSnapshot {
        goal: result.goal.clone(),
        deadline: result.deadline.clone(),
        daily_minutes: plan.daily_minutes,
        plan_version: plan.version,
        graph: result.graph.clone(),
        stages: result.stages.clone(),
        schedule: result.schedule.clone(),
        validation: result.validation.clone(),
        warnings: result.warnings.clone(),
    }
}

fn metadata(conn: &Connection, id: i64) -> Result<(ShareMetadata, String)> {
    let row = conn.query_row(
        "SELECT plan_id, plan_version, payload_json, expires_at, created_at, revoked_at FROM shared_plans WHERE id = ?1",
        params![id],
        |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)? as u32,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, Option<String>>(5)?,
            ))
        },
    ).optional()?.ok_or_else(|| BackendError::new("share_not_found", "分享不存在"))?;
    let snapshot: SharedPlanSnapshot = serde_json::from_str(&row.2)?;
    let status = if row.5.is_some() {
        "revoked"
    } else if expiration(&row.3)? <= OffsetDateTime::now_utc() {
        "expired"
    } else {
        "active"
    };
    Ok((
        ShareMetadata {
            id,
            plan_id: format!("plan-{}", row.0),
            plan_version: row.1,
            title: snapshot.goal,
            status: status.into(),
            created_at: row.4,
            expires_at: row.3,
            revoked_at: row.5,
        },
        row.2,
    ))
}

pub fn create<R: Runtime>(
    app: &AppHandle<R>,
    state: &SharingState,
    plan_id: &str,
    expires_in_days: Option<u32>,
) -> Result<CreatedShare> {
    let days = expires_in_days.unwrap_or(90);
    if !(1..=365).contains(&days) {
        return Err(BackendError::invalid(
            "invalid_share_expiry",
            "分享有效期须为 1 到 365 天",
        ));
    }
    let plan = super::learning::get(&state.learning, plan_id)?;
    if !["done", "degraded"].contains(&plan.status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "只有已完成的学习方案可以分享",
        ));
    }
    let result = plan
        .result
        .as_ref()
        .ok_or_else(|| BackendError::new("plan_not_ready", "该方案没有可分享的完整快照"))?;
    let snapshot = snapshot(&plan, result);
    let token = new_token()?;
    let created_at = OffsetDateTime::now_utc();
    let expires_at = created_at + Duration::days(i64::from(days));
    let expires_at = expires_at.format(&Rfc3339)?;
    let payload = serde_json::to_string(&snapshot)?;
    if payload.len() > MAX_PACKAGE_BYTES {
        return Err(BackendError::new(
            "share_snapshot_too_large",
            "方案快照超过 2 MB 分享上限",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO shared_plans (plan_id, plan_version, token_hash, payload_json, expires_at, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![super::learning::plan_id(plan_id)?, plan.version, token_hash(&token), payload, expires_at, created_at.format(&Rfc3339)?],
    )?;
    let id = tx.last_insert_rowid();
    let event = Store::event(&tx, "share.created", json_event(id, plan_id, plan.version))?;
    tx.commit()?;
    emit(app, &event);
    let (share, _) = metadata(&store.conn, id)?;
    let package = make_package(token.clone(), snapshot, expires_at)?;
    Ok(CreatedShare {
        share,
        token,
        package,
    })
}

fn json_event(id: i64, plan_id: &str, version: u32) -> serde_json::Value {
    serde_json::json!({"shareId": id, "planId": plan_id, "version": version})
}

fn make_package(token: String, plan: SharedPlanSnapshot, expires_at: String) -> Result<String> {
    let payload = PackagePayload {
        token: &token,
        plan: &plan,
        expires_at: &expires_at,
    };
    let checksum = format!("{:x}", Sha256::digest(serde_json::to_vec(&payload)?));
    serde_json::to_string(&SharePackage {
        format: "deep-tutor-share".into(),
        version: 1,
        token,
        plan,
        expires_at,
        checksum,
    })
    .map_err(Into::into)
}

pub fn read_token(state: &SharingState, token: &str) -> Result<SharedPlanView> {
    validate_token(token)?;
    let store = state.store()?;
    let id = store
        .conn
        .query_row(
            "SELECT id FROM shared_plans WHERE token_hash = ?1",
            params![token_hash(token)],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("share_unavailable", "分享不存在、已撤销或已过期"))?;
    let (share, payload) = metadata(&store.conn, id)?;
    if share.status != "active" {
        return Err(BackendError::new(
            "share_unavailable",
            "分享不存在、已撤销或已过期",
        ));
    }
    Ok(SharedPlanView {
        plan: serde_json::from_str(&payload)?,
        expires_at: share.expires_at,
        source: "local".into(),
    })
}

pub fn list(state: &SharingState, plan_id: Option<&str>) -> Result<Vec<ShareMetadata>> {
    let plan_id = plan_id.map(super::learning::plan_id).transpose()?;
    let store = state.store()?;
    let mut statement = store.conn.prepare(
        "SELECT id FROM shared_plans WHERE (?1 IS NULL OR plan_id = ?1) ORDER BY id DESC LIMIT 100",
    )?;
    let ids = statement
        .query_map(params![plan_id], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    ids.into_iter()
        .map(|id| metadata(&store.conn, id).map(|row| row.0))
        .collect()
}

pub fn revoke<R: Runtime>(
    app: &AppHandle<R>,
    state: &SharingState,
    id: i64,
) -> Result<ShareMetadata> {
    if id <= 0 {
        return Err(BackendError::invalid("invalid_share_id", "分享标识无效"));
    }
    let store = state.store()?;
    let (share, _) = metadata(&store.conn, id)?;
    if share.revoked_at.is_some() {
        return Ok(share);
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "UPDATE shared_plans SET revoked_at = ?1 WHERE id = ?2 AND revoked_at IS NULL",
        params![now()?, id],
    )?;
    let event = Store::event(
        &tx,
        "share.revoked",
        serde_json::json!({"shareId": id, "planId": share.plan_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    metadata(&store.conn, id).map(|row| row.0)
}

pub fn read_package(_state: &SharingState, content: &str) -> Result<SharedPlanView> {
    if content.len() > MAX_PACKAGE_BYTES {
        return Err(BackendError::new(
            "share_package_too_large",
            "分享包超过 2 MB 上限",
        ));
    }
    let package: SharePackage = serde_json::from_str(content)
        .map_err(|_| BackendError::invalid("invalid_share_package", "分享包格式或内容无效"))?;
    if package.format != "deep-tutor-share" || package.version != 1 {
        return Err(BackendError::invalid(
            "unsupported_share_package",
            "仅支持第一版 Deep Tutor 分享包",
        ));
    }
    validate_token(&package.token)?;
    let payload = PackagePayload {
        token: &package.token,
        plan: &package.plan,
        expires_at: &package.expires_at,
    };
    let checksum = format!("{:x}", Sha256::digest(serde_json::to_vec(&payload)?));
    if checksum != package.checksum {
        return Err(BackendError::new(
            "share_checksum_mismatch",
            "分享包已损坏或被修改，无法读取",
        ));
    }
    if expiration(&package.expires_at)? <= OffsetDateTime::now_utc() {
        return Err(BackendError::new("share_expired", "离线分享包已过期"));
    }
    Ok(SharedPlanView {
        plan: package.plan,
        expires_at: package.expires_at,
        source: "offline".into(),
    })
}

fn package_path(path: &Path, error_code: &str) -> Result<()> {
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("dtshare"))
    {
        Ok(())
    } else {
        Err(BackendError::invalid(
            error_code,
            "分享文件必须使用 .dtshare 扩展名",
        ))
    }
}

pub fn export_package(
    state: &SharingState,
    content: &str,
    destination: &Path,
) -> Result<serde_json::Value> {
    read_package(state, content)?;
    package_path(destination, "invalid_share_destination")?;
    materials::atomic_export(destination, content.as_bytes())?;
    Ok(json_result(content.len()))
}

fn json_result(bytes: usize) -> serde_json::Value {
    serde_json::json!({"bytes": bytes})
}

pub fn read_package_file(state: &SharingState, path: &Path) -> Result<SharedPlanView> {
    package_path(path, "invalid_share_file")?;
    let metadata = std::fs::metadata(path)?;
    if !metadata.is_file() {
        return Err(BackendError::invalid(
            "invalid_share_file",
            "分享路径不是文件",
        ));
    }
    if metadata.len() > MAX_PACKAGE_BYTES as u64 {
        return Err(BackendError::new(
            "share_package_too_large",
            "分享包超过 2 MB 上限",
        ));
    }
    let bytes = std::fs::read(path)?;
    let content = String::from_utf8(bytes)
        .map_err(|_| BackendError::invalid("invalid_share_package", "分享包不是有效 UTF-8 JSON"))?;
    read_package(state, &content)
}
