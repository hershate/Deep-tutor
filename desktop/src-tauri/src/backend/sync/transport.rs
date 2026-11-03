use super::super::error::{BackendError, Result};
use super::super::providers::openai;
use super::{SyncConfig, SyncCredentials};
use reqwest::blocking::Client;
use sha2::{Digest, Sha256};
use std::io::Read;
use time::OffsetDateTime;
use zeroize::Zeroizing;

const MAX_RESPONSE_BYTES: u64 = 1024 * 1024 + 1;

pub(crate) fn hmac(key: &[u8], data: &[u8]) -> [u8; 32] {
    let mut block = [0_u8; 64];
    if key.len() > block.len() {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = [0x36_u8; 64];
    let mut outer = [0x5c_u8; 64];
    for index in 0..64 {
        inner[index] ^= block[index];
        outer[index] ^= block[index];
    }
    let inner = Sha256::digest([inner.as_slice(), data].concat());
    let mut output = [0_u8; 32];
    output.copy_from_slice(&Sha256::digest(
        [outer.as_slice(), inner.as_slice()].concat(),
    ));
    output
}

fn signature(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    url: &reqwest::Url,
    method: &str,
    payload_hash: &str,
) -> Result<(String, String)> {
    let SyncCredentials::S3 {
        access_key,
        secret_key,
    } = credentials
    else {
        return Err(BackendError::new(
            "sync_credentials_missing",
            "S3 凭据未配置",
        ));
    };
    let timestamp_format = time::format_description::parse_borrowed::<2>("[year padding:zero][month padding:zero][day padding:zero]T[hour padding:zero][minute padding:zero][second padding:zero]Z").map_err(|_| BackendError::new("sync_failed", "S3 时间格式初始化失败"))?;
    let date_format = time::format_description::parse_borrowed::<2>(
        "[year padding:zero][month padding:zero][day padding:zero]",
    )
    .map_err(|_| BackendError::new("sync_failed", "S3 日期格式初始化失败"))?;
    let now = OffsetDateTime::now_utc();
    let timestamp = now.format(&timestamp_format)?;
    let date = now.format(&date_format)?;
    let host = url
        .host_str()
        .ok_or_else(|| BackendError::invalid("invalid_sync_endpoint", "S3 地址缺少主机"))?;
    let host = url
        .port()
        .map_or_else(|| host.to_string(), |port| format!("{host}:{port}"));
    let canonical_headers =
        format!("host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{timestamp}\n");
    let canonical = format!(
        "{method}\n{}\n\n{canonical_headers}\nhost;x-amz-content-sha256;x-amz-date\n{payload_hash}",
        url.path()
    );
    let scope = format!("{date}/{}/s3/aws4_request", config.region);
    let string_to_sign = format!(
        "AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{:x}",
        Sha256::digest(canonical.as_bytes())
    );
    let date_key = hmac(
        format!("AWS4{}", secret_key.as_str()).as_bytes(),
        date.as_bytes(),
    );
    let region_key = hmac(&date_key, config.region.as_bytes());
    let service_key = hmac(&region_key, b"s3");
    let signing_key = Zeroizing::new(hmac(&service_key, b"aws4_request"));
    let signature = hmac(&signing_key[..], string_to_sign.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let authorization = format!("AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={signature}", access_key.as_str());
    Ok((timestamp, authorization))
}

pub fn send(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    method: &str,
    body: Option<Vec<u8>>,
) -> Result<Vec<u8>> {
    let kind = if config.allow_private {
        "本地模型"
    } else {
        "官方"
    };
    let suffix = if config.kind == "s3" {
        format!("{}/{}", config.bucket, config.remote_path)
    } else {
        config.remote_path.clone()
    };
    let url = openai::endpoint(&config.endpoint, kind, &suffix)?;
    let client: Client = openai::client(&url, kind)?;
    let payload_hash = format!("{:x}", Sha256::digest(body.as_deref().unwrap_or_default()));
    let mut request = client.request(
        reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|_| BackendError::invalid("invalid_sync_method", "不支持的同步请求方法"))?,
        url.clone(),
    );
    if method == "PROPFIND" {
        request = request.header("Depth", "0");
    }
    if config.kind == "s3" {
        let (timestamp, authorization) =
            signature(config, credentials, &url, method, &payload_hash)?;
        request = request
            .header("x-amz-content-sha256", payload_hash)
            .header("x-amz-date", timestamp)
            .header("Authorization", authorization);
    } else {
        match credentials {
            SyncCredentials::Webdav { username, password } => {
                request = if config.auth_kind == "basic" {
                    request.basic_auth(username, Some(password.as_str()))
                } else {
                    request.bearer_auth(password.as_str())
                };
            }
            SyncCredentials::None => {}
            SyncCredentials::S3 { .. } => {
                return Err(BackendError::new(
                    "sync_credentials_missing",
                    "WebDAV 凭据类型不匹配",
                ))
            }
        }
    }
    if let Some(body) = body {
        request = request
            .header("Content-Type", "application/json")
            .body(body);
    }
    let response = request.send().map_err(|error| {
        if error.is_timeout() {
            BackendError::new("sync_timeout", "远端同步请求超时，请稍后重试")
        } else {
            BackendError::new("sync_network_error", "无法连接远端同步服务")
        }
    })?;
    let status = response.status();
    if status.is_redirection() {
        return Err(BackendError::new(
            "sync_redirect_blocked",
            "同步服务重定向已被阻止，请核对实际服务地址",
        ));
    }
    if !status.is_success() {
        return Err(BackendError::new(
            "sync_http_error",
            &format!("远端同步服务返回 HTTP {}", status.as_u16()),
        ));
    }
    let mut bytes = Vec::new();
    response.take(MAX_RESPONSE_BYTES).read_to_end(&mut bytes)?;
    if bytes.len() as u64 >= MAX_RESPONSE_BYTES {
        return Err(BackendError::new(
            "sync_payload_too_large",
            "远端响应超过 1 MB 上限",
        ));
    }
    Ok(bytes)
}
