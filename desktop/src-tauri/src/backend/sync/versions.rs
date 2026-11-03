use super::super::error::{BackendError, Result};
use super::providers::sync_config::SyncPreview;
use super::{
    check_cancelled, credentials, stage, transport, Outcome, SyncConfig, SyncCredentials,
    SyncService,
};
use quick_xml::events::Event;
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Runtime};
use time::OffsetDateTime;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncVersion {
    pub path: String,
    pub size: Option<u64>,
    pub etag: Option<String>,
    pub last_modified: Option<String>,
}

fn base(config: &SyncConfig) -> Result<String> {
    config
        .remote_path
        .strip_suffix(".json")
        .map(str::to_string)
        .ok_or_else(|| BackendError::invalid("invalid_sync_path", "同步对象必须使用 .json 文件"))
}

pub fn valid_version_path(config: &SyncConfig, path: &str) -> Result<()> {
    let prefix = format!("{}/versions/", base(config)?);
    let name = path.strip_prefix(&prefix).ok_or_else(|| {
        BackendError::invalid("invalid_sync_version", "只能恢复当前配置历史目录中的版本")
    })?;
    if name.is_empty()
        || name.len() > 200
        || name.starts_with('.')
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        || !name.ends_with(".json")
    {
        return Err(BackendError::invalid(
            "invalid_sync_version",
            "历史版本文件名无效",
        ));
    }
    Ok(())
}

fn push_version(
    result: &mut Vec<SyncVersion>,
    config: &SyncConfig,
    value: &str,
    size: Option<u64>,
    etag: Option<String>,
    last_modified: Option<String>,
) -> Result<()> {
    if !value.contains("/versions/") || !value.ends_with(".json") {
        return Ok(());
    }
    let name = value.rsplit('/').next().unwrap_or_default();
    let path = format!("{}/versions/{name}", base(config)?);
    valid_version_path(config, &path)?;
    result.push(SyncVersion {
        path,
        size,
        etag,
        last_modified,
    });
    Ok(())
}

fn parse_versions(config: &SyncConfig, bytes: &[u8]) -> Result<Vec<SyncVersion>> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| BackendError::new("invalid_sync_versions", "远端版本列表不是有效 UTF-8"))?;
    let mut reader = quick_xml::Reader::from_str(text);
    reader.config_mut().trim_text(true);
    let mut current = String::new();
    let mut value = String::new();
    let mut size = None;
    let mut etag = None;
    let mut last_modified = None;
    let mut truncated = false;
    let mut result = Vec::new();
    loop {
        match reader
            .read_event()
            .map_err(|_| BackendError::new("invalid_sync_versions", "远端版本列表 XML 无效"))?
        {
            Event::Start(event) => {
                current = event.local_name().as_ref().to_string();
                if matches!(current.as_str(), "response" | "Contents") {
                    value.clear();
                    size = None;
                    etag = None;
                    last_modified = None;
                }
            }
            Event::Text(event) => {
                let decoded = event.xml10_content();
                let text = quick_xml::escape::unescape(&decoded)
                    .map_err(|_| {
                        BackendError::new("invalid_sync_versions", "远端版本列表转义无效")
                    })?
                    .into_owned();
                match current.as_str() {
                    "href" | "Key" => value = text,
                    "Size" | "GetContentLength" => size = text.parse::<u64>().ok(),
                    "ETag" => etag = Some(text.trim_matches('"').to_string()),
                    "LastModified" | "GetLastModified" => last_modified = Some(text),
                    "IsTruncated" => truncated |= text == "true",
                    _ => {}
                }
            }
            Event::End(event) => {
                let name = event.local_name();
                if matches!(name.as_ref(), "response" | "Contents") {
                    push_version(
                        &mut result,
                        config,
                        &value,
                        size,
                        etag.take(),
                        last_modified.take(),
                    )?;
                    value.clear();
                    size = None;
                    last_modified = None;
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if truncated {
        return Err(BackendError::new(
            "sync_version_overflow",
            "远端版本列表被截断，未执行清理",
        ));
    }
    result.sort_by(|left, right| right.path.cmp(&left.path));
    Ok(result)
}

pub fn list(service: &SyncService, config: &SyncConfig) -> Result<Vec<SyncVersion>> {
    let credentials = credentials(service, config)?;
    let bytes = if config.kind == "s3" {
        let prefix = format!("{}/versions/", base(config)?);
        transport::send_at(
            config,
            &credentials,
            "GET",
            None,
            "",
            &[("list-type", "2"), ("prefix", &prefix)],
            None,
        )?
    } else {
        let path = format!("{}/versions/", base(config)?);
        transport::send_at(config, &credentials, "PROPFIND", None, &path, &[], Some(1))?
    };
    parse_versions(config, &bytes)
}

pub fn upload<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
    id: &str,
    config: &SyncConfig,
    credentials: &SyncCredentials,
    bytes: Vec<u8>,
    preview: SyncPreview,
) -> Result<Outcome> {
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let format = time::format_description::parse_borrowed::<2>("[year padding:zero][month padding:zero][day padding:zero]T[hour padding:zero][minute padding:zero][second padding:zero].[subsecond digits:9]Z").map_err(|_| BackendError::new("sync_failed", "历史版本时间格式初始化失败"))?;
    let timestamp = OffsetDateTime::now_utc().format(&format)?;
    let history = format!("{}/versions/{timestamp}-{id}-{sha256}.json", base(config)?);
    valid_version_path(config, &history)?;
    stage(app, service, id, "running", "version")?;
    let _ = transport::send_at(
        config,
        credentials,
        "PUT",
        Some(bytes.clone()),
        &history,
        &[],
        None,
    )?;
    check_cancelled(service, id)?;
    stage(app, service, id, "running", "request")?;
    let _ = transport::send(config, credentials, "PUT", Some(bytes.clone()))?;
    check_cancelled(service, id)?;
    stage(app, service, id, "running", "verify")?;
    if transport::send(config, credentials, "GET", None)? != bytes
        || transport::send_at(config, credentials, "GET", None, &history, &[], None)? != bytes
    {
        return Err(BackendError::new(
            "sync_verify_failed",
            "当前配置或历史版本回读内容不一致",
        ));
    }
    check_cancelled(service, id)?;
    stage(app, service, id, "running", "retention")?;
    let versions = list(service, config)?;
    for version in versions.iter().skip(10) {
        let _ = transport::send_at(
            config,
            credentials,
            "DELETE",
            None,
            &version.path,
            &[],
            None,
        )?;
    }
    let remaining = list(service, config)?.len();
    if remaining > 10 {
        return Err(BackendError::new(
            "sync_retention_failed",
            "远端历史版本清理未完成，请检查删除权限",
        ));
    }
    Ok(Outcome {
        result: json!({"sha256": sha256, "bytes": bytes.len(), "profiles": preview.profiles, "historyPath": history, "versions": remaining, "remoteConfirmed": true}),
        local_path: None,
    })
}
