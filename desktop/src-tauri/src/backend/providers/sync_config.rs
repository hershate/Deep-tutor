use super::super::error::{BackendError, Result};
use super::{ProfileInput, ProviderProfile, ProviderState};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Digest;
use std::collections::HashMap;
use tauri::{AppHandle, Runtime};

const MAX_CONFIG_BYTES: usize = 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RemoteConfig {
    version: u32,
    active_provider: Option<String>,
    routing: super::routing::RoutingSettings,
    providers: Vec<ProviderProfile>,
    notice: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncPreview {
    pub profiles: usize,
    pub routing_mode: String,
    pub active_provider: Option<String>,
    pub sha256: String,
}

fn reject_sensitive(value: &Value) -> Result<()> {
    match value {
        Value::Object(object) => {
            for (key, value) in object {
                let key = key.to_ascii_lowercase().replace(['_', '-'], "");
                if [
                    "apikey",
                    "secret",
                    "secretkey",
                    "password",
                    "token",
                    "credential",
                    "credentials",
                    "accesskey",
                ]
                .contains(&key.as_str())
                {
                    return Err(BackendError::new(
                        "sync_sensitive_payload",
                        "远端配置包含禁止传输的凭据字段",
                    ));
                }
                reject_sensitive(value)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                reject_sensitive(item)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn parse(bytes: &[u8]) -> Result<RemoteConfig> {
    if bytes.len() > MAX_CONFIG_BYTES {
        return Err(BackendError::new(
            "sync_payload_too_large",
            "同步配置超过 1 MB 上限",
        ));
    }
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|_| BackendError::invalid("invalid_sync_payload", "远端配置不是有效 JSON"))?;
    reject_sensitive(&value)?;
    let config: RemoteConfig = serde_json::from_value(value).map_err(|_| {
        BackendError::invalid("invalid_sync_payload", "远端配置结构不符合脱敏配置约定")
    })?;
    if config.version != 1
        || config.notice != "API keys are never exported."
        || config.providers.len() > 200
    {
        return Err(BackendError::invalid(
            "invalid_sync_payload",
            "远端配置版本或档案数量无效",
        ));
    }
    if !["direct", "routing", "aggregate"].contains(&config.routing.mode.as_str()) {
        return Err(BackendError::invalid(
            "invalid_sync_payload",
            "远端路由模式无效",
        ));
    }
    let mut ids = std::collections::HashSet::new();
    for profile in &config.providers {
        if !ids.insert(&profile.id) {
            return Err(BackendError::new(
                "sync_duplicate_profile",
                "远端配置包含重复的档案标识",
            ));
        }
        super::validate_input(&ProfileInput {
            id: Some(profile.id.clone()),
            name: profile.name.clone(),
            preset: Some(profile.preset.clone()),
            kind: profile.kind.clone(),
            protocol: profile.protocol.clone(),
            base_url: profile.base_url.clone(),
            default_model: profile.default_model.clone(),
            models: profile.models.clone(),
            enabled: Some(profile.enabled),
            note: profile.note.clone(),
            api_key: None,
        })?;
    }
    let references = config
        .routing
        .aggregate_provider_ids
        .iter()
        .chain(config.routing.aggregate_default.iter())
        .chain(config.routing.failover.iter())
        .chain(
            config
                .routing
                .routes
                .values()
                .filter_map(|route| route.provider_id.as_ref()),
        );
    for id in references {
        if !config.providers.iter().any(|profile| &profile.id == id) {
            return Err(BackendError::new(
                "sync_profile_missing",
                "远端路由引用了不存在的档案",
            ));
        }
    }
    if let Some(active) = &config.active_provider {
        if !config.providers.iter().any(|profile| &profile.id == active) {
            return Err(BackendError::new(
                "sync_profile_missing",
                "远端默认档案不存在",
            ));
        }
    }
    Ok(config)
}

pub fn preview(bytes: &[u8]) -> Result<SyncPreview> {
    let config = parse(bytes)?;
    Ok(SyncPreview {
        profiles: config.providers.len(),
        routing_mode: config.routing.mode,
        active_provider: config.active_provider,
        sha256: format!("{:x}", sha2::Sha256::digest(bytes)),
    })
}

/// 94#2：导入快照与回滚——恢复/导入逐档案 upsert 各自提交，进行到第 N 个
/// 档案时失败或进程被杀会留下「部分新 + 部分旧」的混合配置且无回滚入口
/// （upsert/routing/set_active 三段均无总事务）。导入前把 provider_profiles
/// 与 provider_meta（路由设置/默认档案）整体快照为 JSON 存入 provider_meta
/// 保留键：任一步失败立即回滚；进程中断残留的快照由下次导入开头先回滚。
/// keyring 不受影响——导入路径 api_key 恒 None，不写不删密钥
const ROLLBACK_SNAPSHOT_KEY: &str = "import_rollback_snapshot";

fn take_import_snapshot(conn: &rusqlite::Connection) -> Result<()> {
    let mut profiles = Vec::new();
    {
        let mut statement = conn.prepare(
            "SELECT id, name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, health, latency_ms, created_at, updated_at FROM provider_profiles",
        )?;
        let rows = statement.query_map([], |row| {
            Ok(json!([
                row.get::<_, i64>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?,
                row.get::<_, String>(3)?, row.get::<_, String>(4)?, row.get::<_, String>(5)?,
                row.get::<_, String>(6)?, row.get::<_, String>(7)?, row.get::<_, i64>(8)?,
                row.get::<_, Option<String>>(9)?, row.get::<_, String>(10)?, row.get::<_, Option<i64>>(11)?,
                row.get::<_, String>(12)?, row.get::<_, String>(13)?,
            ]))
        })?;
        for row in rows {
            profiles.push(row?);
        }
    }
    let mut meta = Vec::new();
    {
        let mut statement = conn.prepare(&format!(
            "SELECT key, value FROM provider_meta WHERE key != '{ROLLBACK_SNAPSHOT_KEY}'"
        ))?;
        let rows = statement
            .query_map([], |row| Ok(json!([row.get::<_, String>(0)?, row.get::<_, String>(1)?])))?;
        for row in rows {
            meta.push(row?);
        }
    }
    let snapshot = serde_json::to_string(&json!({"profiles": profiles, "meta": meta}))?;
    conn.execute(
        "INSERT INTO provider_meta (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        rusqlite::params![ROLLBACK_SNAPSHOT_KEY, snapshot],
    )?;
    Ok(())
}

/// 回滚到快照并丢弃；无快照时为无操作
fn rollback_import_snapshot(conn: &rusqlite::Connection) -> Result<()> {
    let snapshot: Option<String> = conn
        .query_row(
            "SELECT value FROM provider_meta WHERE key = ?1",
            rusqlite::params![ROLLBACK_SNAPSHOT_KEY],
            |row| row.get(0),
        )
        .optional()?;
    let Some(snapshot) = snapshot else {
        return Ok(());
    };
    let parsed: Value = serde_json::from_str(&snapshot)
        .map_err(|_| BackendError::new("sync_snapshot_invalid", "导入回滚快照损坏"))?;
    let profiles: Vec<(i64, String, String, String, String, String, String, String, i64, Option<String>, String, Option<i64>, String, String)> = serde_json::from_value(
        parsed["profiles"].clone(),
    )?;
    let meta: Vec<(String, String)> = serde_json::from_value(parsed["meta"].clone())?;
    conn.execute("DELETE FROM provider_profiles", [])?;
    for profile in profiles {
        conn.execute(
            "INSERT INTO provider_profiles (id, name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, health, latency_ms, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            rusqlite::params![
                profile.0, profile.1, profile.2, profile.3, profile.4, profile.5, profile.6,
                profile.7, profile.8, profile.9, profile.10, profile.11, profile.12, profile.13,
            ],
        )?;
    }
    conn.execute(
        &format!("DELETE FROM provider_meta WHERE key != '{ROLLBACK_SNAPSHOT_KEY}'"),
        [],
    )?;
    for (key, value) in meta {
        conn.execute(
            "INSERT INTO provider_meta (key, value) VALUES (?1, ?2)",
            rusqlite::params![key, value],
        )?;
    }
    drop_import_snapshot(conn)?;
    Ok(())
}

fn drop_import_snapshot(conn: &rusqlite::Connection) -> Result<()> {
    conn.execute(
        "DELETE FROM provider_meta WHERE key = ?1",
        rusqlite::params![ROLLBACK_SNAPSHOT_KEY],
    )?;
    Ok(())
}

pub fn import<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    bytes: &[u8],
) -> Result<SyncPreview> {
    let config = parse(bytes)?;
    // E-2：parse 已对每个档案跑过输入校验，这里再整批预检停用约束——
    // 远端含 enabled:false 且 id 撞本地激活档案时，逐档案 upsert 会在
    // 中途失败留下部分提交；预检失败时零落库，用户可先处理冲突再重试
    for profile in &config.providers {
        super::check_disable_constraints(
            state,
            &ProfileInput {
                id: Some(profile.id.clone()),
                name: profile.name.clone(),
                preset: Some(profile.preset.clone()),
                kind: profile.kind.clone(),
                protocol: profile.protocol.clone(),
                base_url: profile.base_url.clone(),
                default_model: profile.default_model.clone(),
                models: profile.models.clone(),
                enabled: Some(profile.enabled),
                note: profile.note.clone(),
                api_key: None,
            },
        )?;
    }
    // 94#2：先回滚上次中断残留的快照（若有），再为本次导入建立快照；
    // 批内任一步失败回滚到导入前状态，消除半套配置
    {
        let store = state.store()?;
        rollback_import_snapshot(&store.conn)?;
        take_import_snapshot(&store.conn)?;
    }
    let outcome = apply_import(app, state, config, bytes);
    match outcome {
        Ok(preview) => {
            let store = state.store()?;
            drop_import_snapshot(&store.conn)?;
            Ok(preview)
        }
        Err(error) => {
            let rolled_back = state
                .store()
                .and_then(|store| rollback_import_snapshot(&store.conn))
                .is_ok();
            if rolled_back {
                Err(BackendError::new(
                    &error.code,
                    &format!("导入失败，已回滚到导入前的本地配置：{}", error.message),
                ))
            } else {
                // 回滚本身失败时保留快照，下次导入开头会再次尝试回滚
                Err(error)
            }
        }
    }
}

fn apply_import<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    config: RemoteConfig,
    bytes: &[u8],
) -> Result<SyncPreview> {
    let mut mapping = HashMap::new();
    let mut applied = 0;
    for profile in config.providers {
        let old_id = profile.id.clone();
        let input = ProfileInput {
            id: Some(profile.id),
            name: profile.name,
            preset: Some(profile.preset),
            kind: profile.kind,
            protocol: profile.protocol,
            base_url: profile.base_url,
            default_model: profile.default_model,
            models: profile.models,
            enabled: Some(profile.enabled),
            note: profile.note,
            api_key: None,
        };
        // E-2：中途失败时带回「已导入 N 个」上下文，不再裸错误
        let imported = super::upsert(app, state, input).map_err(|error| {
            BackendError::new(
                &error.code,
                &format!("已导入 {applied} 个档案后中断：{}", error.message),
            )
        })?;
        mapping.insert(old_id, imported.id);
        applied += 1;
    }
    let mut routing = config.routing;
    for id in &mut routing.aggregate_provider_ids {
        if let Some(mapped) = mapping.get(id) {
            *id = mapped.clone();
        }
    }
    routing.aggregate_default = routing
        .aggregate_default
        .map(|id| mapping.get(&id).cloned().unwrap_or(id));
    for id in &mut routing.failover {
        if let Some(mapped) = mapping.get(id) {
            *id = mapped.clone();
        }
    }
    for route in routing.routes.values_mut() {
        route.provider_id = route
            .provider_id
            .take()
            .map(|id| mapping.get(&id).cloned().unwrap_or(id));
    }
    super::routing::save_settings(app, state, routing.clone()).map_err(|error| {
        BackendError::new(
            "sync_route_restore_failed",
            &format!("已追加 {applied} 个档案，但路由恢复失败：{}", error.message),
        )
    })?;
    if let Some(active) = config.active_provider {
        let active = mapping.get(&active).cloned().unwrap_or(active);
        super::set_active(app, state, &active).map_err(|error| {
            BackendError::new(
                "sync_active_restore_failed",
                &format!(
                    "已追加 {applied} 个档案，但默认档案恢复失败：{}",
                    error.message
                ),
            )
        })?;
    }
    Ok(SyncPreview {
        profiles: applied,
        routing_mode: routing.mode,
        active_provider: super::active(state)?,
        sha256: format!("{:x}", sha2::Sha256::digest(bytes)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn import_snapshot_rollback_restores_profiles_and_meta() {
        // 94#2：恢复/导入逐档案 upsert 中断留下半套配置无回滚入口——快照
        // 机制须能完整还原 provider_profiles 与 provider_meta（路由/默认档案）
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        crate::backend::providers::migrate(&conn).unwrap();
        conn.execute(
            "INSERT INTO provider_profiles (name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, created_at, updated_at) VALUES ('本地档案', 'custom', '官方', 'OpenAI Chat', 'https://a.example', 'm', '[\"m\"]', 1, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO provider_meta (key, value) VALUES ('active_provider', 'p1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO provider_meta (key, value) VALUES ('routing_settings', '{\"mode\":\"direct\",\"routes\":{},\"failover\":[],\"aggregateProviderIds\":[],\"aggregateDefault\":null}')",
            [],
        )
        .unwrap();
        take_import_snapshot(&conn).unwrap();
        // 模拟导入进行到一半：追加新档案行 + 覆写默认档案
        conn.execute(
            "INSERT INTO provider_profiles (name, preset, kind, protocol, base_url, default_model, models_json, enabled, note, created_at, updated_at) VALUES ('半套新档案', 'custom', '官方', 'OpenAI Chat', 'https://b.example', 'm', '[\"m\"]', 1, NULL, '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z')",
            [],
        )
        .unwrap();
        conn.execute(
            "UPDATE provider_meta SET value = 'p2' WHERE key = 'active_provider'",
            [],
        )
        .unwrap();
        rollback_import_snapshot(&conn).unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM provider_profiles", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1, "回滚后只保留导入前的档案");
        let name: String = conn
            .query_row("SELECT name FROM provider_profiles", [], |row| row.get(0))
            .unwrap();
        assert_eq!(name, "本地档案");
        let active: String = conn
            .query_row(
                "SELECT value FROM provider_meta WHERE key = 'active_provider'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(active, "p1");
        // 快照键已清除；无快照时再次回滚为无操作（幂等）
        let snapshot: Option<String> = conn
            .query_row(
                "SELECT value FROM provider_meta WHERE key = ?1",
                rusqlite::params![ROLLBACK_SNAPSHOT_KEY],
                |row| row.get(0),
            )
            .optional()
            .unwrap();
        assert!(snapshot.is_none());
        rollback_import_snapshot(&conn).unwrap();
    }
}
