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

/// 94#5：RFC3986 canonical query——AWS SigV4 要求 unreserved（A-Za-z0-9-_.~）
/// 原样、其余 %XX 大写（空格 %20 不是 +），且按键排序；原先直接取
/// form_urlencoded 序列化的原串（空格→+、~→%7E、未排序），当前
/// list-type/prefix 字符集恰不受影响，未来加 continuation-token 等参数
/// 即 403 且排障方向被误导到凭据
fn canonical_query(query: &[(&str, &str)]) -> String {
    let encode = |value: &str| -> String {
        value
            .bytes()
            .map(|byte| match byte {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                    (byte as char).to_string()
                }
                _ => format!("%{byte:02X}"),
            })
            .collect()
    };
    let mut pairs: Vec<String> = query
        .iter()
        .map(|(key, value)| format!("{}={}", encode(key), encode(value)))
        .collect();
    pairs.sort();
    pairs.join("&")
}

fn signature(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    url: &reqwest::Url,
    method: &str,
    payload_hash: &str,
    query: &str,
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
    // 94#5：默认端口不进 canonical host——endpoint 显式写 https://host:443
    // 时底层按惯例发不带端口的 Host 头，canonical 带端口即签名不匹配 403
    let host = match url.port() {
        Some(port)
            if !(url.scheme() == "https" && port == 443
                || url.scheme() == "http" && port == 80) =>
        {
            format!("{host}:{port}")
        }
        _ => host.to_string(),
    };
    let canonical_headers =
        format!("host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{timestamp}\n");
    let canonical = format!(
        "{method}\n{}\n{query}\n{canonical_headers}\nhost;x-amz-content-sha256;x-amz-date\n{payload_hash}",
        url.path(),
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
    send_at(
        config,
        credentials,
        method,
        body,
        &config.remote_path,
        &[],
        None,
    )
}

pub fn send_at(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    method: &str,
    body: Option<Vec<u8>>,
    path: &str,
    query: &[(&str, &str)],
    depth: Option<u8>,
) -> Result<Vec<u8>> {
    let (status, bytes) = execute(config, credentials, method, body, path, query, depth)?;
    if !status.is_success() {
        return Err(BackendError::new(
            "sync_http_error",
            &format!("远端同步服务返回 HTTP {}", status.as_u16()),
        ));
    }
    Ok(bytes)
}

/// 与 `send_at` 相同，但把 404 映射为 `None`，用于"集合可能尚不存在"的查询。
pub fn send_at_or_missing(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    method: &str,
    body: Option<Vec<u8>>,
    path: &str,
    query: &[(&str, &str)],
    depth: Option<u8>,
) -> Result<Option<Vec<u8>>> {
    let (status, bytes) = execute(config, credentials, method, body, path, query, depth)?;
    if status.as_u16() == 404 {
        return Ok(None);
    }
    if !status.is_success() {
        return Err(BackendError::new(
            "sync_http_error",
            &format!("远端同步服务返回 HTTP {}", status.as_u16()),
        ));
    }
    Ok(Some(bytes))
}

/// MKCOL 创建 WebDAV 集合；2xx 与 405（集合已存在）都视为成功。
pub fn mkcol(config: &SyncConfig, credentials: &SyncCredentials, path: &str) -> Result<()> {
    let (status, _) = execute(config, credentials, "MKCOL", None, path, &[], None)?;
    if status.is_success() || status.as_u16() == 405 {
        Ok(())
    } else {
        Err(BackendError::new(
            "sync_http_error",
            &format!("远端同步服务返回 HTTP {}", status.as_u16()),
        ))
    }
}

fn execute(
    config: &SyncConfig,
    credentials: &SyncCredentials,
    method: &str,
    body: Option<Vec<u8>>,
    path: &str,
    query: &[(&str, &str)],
    depth: Option<u8>,
) -> Result<(reqwest::StatusCode, Vec<u8>)> {
    let kind = if config.allow_private {
        "本地模型"
    } else {
        "官方"
    };
    let suffix = if config.kind == "s3" {
        if path.is_empty() {
            config.bucket.clone()
        } else {
            format!("{}/{}", config.bucket, path)
        }
    } else {
        path.to_string()
    };
    let mut url = openai::endpoint(&config.endpoint, kind, &suffix)?;
    // 94#5：S3 线上查询串与 canonical 同源（RFC3986）——form 编码的 +/~
    // 与 canonical 不一致时，服务端按收到的原串校验必 403；
    // WebDAV 无签名，沿用 form 编码
    let s3_query = (config.kind == "s3").then(|| canonical_query(query));
    if !query.is_empty() {
        if let Some(canonical) = &s3_query {
            url.set_query(Some(canonical));
        } else {
            url.query_pairs_mut().extend_pairs(query.iter().copied());
        }
    }
    let client: Client = openai::client(&url, kind)?;
    let payload_hash = format!("{:x}", Sha256::digest(body.as_deref().unwrap_or_default()));
    let mut request = client.request(
        reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|_| BackendError::invalid("invalid_sync_method", "不支持的同步请求方法"))?,
        url.clone(),
    );
    if method == "PROPFIND" {
        request = request.header("Depth", depth.unwrap_or(0).to_string());
    }
    if config.kind == "s3" {
        let (timestamp, authorization) = signature(
            config,
            credentials,
            &url,
            method,
            &payload_hash,
            s3_query.as_deref().unwrap_or(""),
        )?;
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
    let mut bytes = Vec::new();
    response.take(MAX_RESPONSE_BYTES).read_to_end(&mut bytes)?;
    if bytes.len() as u64 >= MAX_RESPONSE_BYTES {
        return Err(BackendError::new(
            "sync_payload_too_large",
            "远端响应超过 1 MB 上限",
        ));
    }
    Ok((status, bytes))
}
