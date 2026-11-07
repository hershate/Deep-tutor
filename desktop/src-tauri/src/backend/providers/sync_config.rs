use super::super::error::{BackendError, Result};
use super::{ProfileInput, ProviderProfile, ProviderState};
use serde::{Deserialize, Serialize};
use serde_json::Value;
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
                &format!("已追加 {applied} 个档案，但默认档案恢复失败：{}", error.message),
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
