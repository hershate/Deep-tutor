pub mod commands;
mod html;
#[cfg(test)]
mod tests;

use super::error::{BackendError, Result};
use reqwest::Url;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::io::Read;
use std::net::ToSocketAddrs;
use std::time::Duration;

const MAX_BYTES: u64 = 1024 * 1024;
const MAX_TEXT_BYTES: usize = 256 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WebPage {
    pub url: String,
    pub title: String,
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
    pub sha256: String,
}

struct Document {
    url: Url,
    body: String,
    content_type: String,
}

#[cfg(test)]
static SEARCH_ENDPOINT: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

/// 12#20：web 与 agent 两域测试共用保存/恢复式检索端点，并行会互相劫持；
/// 设置端点的测试须先持有此互斥（覆盖设置→使用→恢复的整个窗口）。
#[cfg(test)]
pub static SEARCH_ENDPOINT_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
pub struct SearchEndpointGuard(Option<String>);

#[cfg(test)]
impl Drop for SearchEndpointGuard {
    fn drop(&mut self) {
        *SEARCH_ENDPOINT.lock().unwrap() = self.0.take();
    }
}

#[cfg(test)]
pub fn set_search_endpoint_for_tests(value: Option<String>) -> SearchEndpointGuard {
    let previous = std::mem::replace(&mut *SEARCH_ENDPOINT.lock().unwrap(), value);
    SearchEndpointGuard(previous)
}

fn search_endpoint() -> String {
    #[cfg(test)]
    if let Some(endpoint) = SEARCH_ENDPOINT.lock().unwrap().clone() {
        return endpoint;
    }
    #[cfg(test)]
    return "http://127.0.0.1:9/".into();
    #[cfg(not(test))]
    "https://html.duckduckgo.com/html/".into()
}

#[derive(Debug)]
struct Target {
    url: Url,
    host: String,
    addresses: Vec<std::net::SocketAddr>,
}

fn resolve(value: Url, allow_loopback: bool) -> Result<Target> {
    if !matches!(value.scheme(), "http" | "https")
        || value.host_str().is_none()
        || !value.username().is_empty()
        || value.password().is_some()
        || value.as_str().len() > 900
    {
        return Err(BackendError::invalid(
            "invalid_web_url",
            "网页地址必须是有效的 HTTP/HTTPS 地址，不能包含凭据且不超过 900 字节",
        ));
    }
    if value.query_pairs().any(|(key, _)| {
        [
            "access_token",
            "api_key",
            "apikey",
            "key",
            "token",
            "signature",
            "sig",
            "password",
            "secret",
        ]
        .contains(&key.to_ascii_lowercase().as_str())
    }) {
        return Err(BackendError::invalid(
            "web_credentials_blocked",
            "网页地址不得携带密钥或访问令牌参数",
        ));
    }
    let host = value
        .host_str()
        .unwrap_or("")
        .trim_start_matches('[')
        .trim_end_matches(']')
        .to_string();
    let port = value
        .port_or_known_default()
        .ok_or_else(|| BackendError::invalid("invalid_web_url", "网页地址缺少有效端口"))?;
    let addresses = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|_| BackendError::new("web_unreachable", "无法解析网页主机地址"))?
        .collect::<Vec<_>>();
    if addresses.is_empty() {
        return Err(BackendError::new("web_unreachable", "网页主机没有可用 IP"));
    }
    if addresses.iter().any(|address| {
        if allow_loopback {
            !address.ip().is_loopback()
        } else {
            !super::providers::openai::public_address(address.ip())
        }
    }) {
        return Err(BackendError::new(
            "ssrf_blocked",
            "网页地址不得指向本机、私网或保留地址",
        ));
    }
    let mut url = value;
    url.set_fragment(None);
    Ok(Target {
        url,
        host,
        addresses,
    })
}

fn document(value: Url, depth: u8) -> Result<Document> {
    if depth > 3 {
        return Err(BackendError::new(
            "web_redirect_limit",
            "网页重定向超过 3 次上限",
        ));
    }
    let target = resolve(value, cfg!(test))?;
    let url = target.url.clone();
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .resolve_to_addrs(&target.host, &target.addresses)
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(12))
        .user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)              Chrome/124.0.0.0 Safari/537.36",
        )
        .build()
        .map_err(|_| BackendError::new("web_client_error", "无法初始化原生网页客户端"))?;
    let response = client
        .get(url.clone())
        .header(
            reqwest::header::ACCEPT,
            "text/html,application/xhtml+xml,text/plain;q=0.9",
        )
        .send()
        .map_err(|error| {
            if error.is_timeout() {
                BackendError::new("web_timeout", "网页请求超时")
            } else {
                BackendError::new("web_network_error", "无法连接公开网页服务")
            }
        })?;
    let status = response.status();
    if status.is_redirection() {
        let location = response
            .headers()
            .get(reqwest::header::LOCATION)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| BackendError::new("web_redirect_invalid", "网页重定向缺少有效目标"))?;
        let target = url
            .join(location)
            .map_err(|_| BackendError::invalid("invalid_web_url", "网页重定向地址无效"))?;
        return document(target, depth + 1);
    }
    if status.as_u16() == 403 || status.as_u16() == 429 {
        return Err(BackendError::new(
            "web_rate_limited",
            &format!(
                "公开网页服务限流（HTTP {}），请等待一段时间后再试，立即重试会加剧限制",
                status.as_u16()
            ),
        ));
    }
    if !status.is_success() {
        return Err(BackendError::new(
            "web_http_error",
            &format!("网页返回 HTTP {}", status.as_u16()),
        ));
    }
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !["text/html", "application/xhtml+xml", "text/plain"]
        .iter()
        .any(|kind| content_type.starts_with(kind))
    {
        return Err(BackendError::new(
            "web_content_type_blocked",
            "仅允许抓取 HTML 或纯文本网页",
        ));
    }
    if content_type.contains("charset=")
        && !content_type.contains("utf-8")
        && !content_type.contains("utf8")
    {
        return Err(BackendError::new(
            "web_encoding_unsupported",
            "网页必须使用 UTF-8 编码",
        ));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_BYTES)
    {
        return Err(BackendError::new(
            "web_response_too_large",
            "网页内容超过 1 MB 上限",
        ));
    }
    let mut bytes = Vec::new();
    response
        .take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::new("web_read_failed", "网页响应读取失败"))?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err(BackendError::new(
            "web_response_too_large",
            "网页内容超过 1 MB 上限",
        ));
    }
    let body = String::from_utf8(bytes)
        .map_err(|_| BackendError::new("web_encoding_unsupported", "网页必须使用 UTF-8 编码"))?;
    Ok(Document {
        url,
        body,
        content_type,
    })
}

fn truncate(value: &str) -> String {
    let mut end = value.len().min(MAX_TEXT_BYTES);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_string()
}

pub fn fetch(value: &str) -> Result<WebPage> {
    let url = Url::parse(value)
        .map_err(|_| BackendError::invalid("invalid_web_url", "网页地址格式无效"))?;
    let document = document(url, 0)?;
    let parsed = html::parse(&document.body, false)?;
    let text = if document.content_type.starts_with("text/plain") {
        document.body.clone()
    } else {
        parsed.text
    };
    if text.trim().is_empty() {
        return Err(BackendError::new(
            "web_empty_content",
            "网页没有可提取的文字内容",
        ));
    }
    let title = if parsed.title.is_empty() {
        document.url.host_str().unwrap_or("公开网页").to_string()
    } else {
        parsed.title
    };
    // 10#25：网页标题超过资料 200 字节上限会导致抓取成功后导入必败，
    // 在抓取侧按字符边界钳制；正文截断同样落警示而非静默丢弃
    let mut title_clamped = false;
    let title = if title.len() > 197 {
        title_clamped = true;
        let mut end = 197;
        while !title.is_char_boundary(end) {
            end -= 1;
        }
        format!("{}…", &title[..end])
    } else {
        title
    };
    let body_truncated = text.len() > MAX_TEXT_BYTES;
    let warning = match (title_clamped, body_truncated) {
        (false, false) => None,
        (true, true) => {
            Some("网页标题过长已截断；正文超过 256 KiB 上限，仅保存前 256 KiB。".into())
        }
        (true, false) => Some("网页标题过长已截断。".into()),
        (false, true) => Some("网页正文超过 256 KiB 上限，仅保存前 256 KiB。".into()),
    };
    Ok(WebPage {
        url: document.url.to_string(),
        title,
        text: truncate(&text),
        warning,
        sha256: format!("{:x}", Sha256::digest(document.body.as_bytes())),
    })
}

fn candidate_url(base: &Url, value: &str) -> Result<Url> {
    let mut url = base
        .join(value)
        .map_err(|_| BackendError::invalid("invalid_web_url", "检索结果链接无效"))?;
    if url
        .host_str()
        .is_some_and(|host| host.eq_ignore_ascii_case("duckduckgo.com"))
        && url.path().starts_with("/l/")
    {
        if let Some(target) = url
            .query_pairs()
            .find(|(key, _)| key == "uddg")
            .map(|(_, value)| value.into_owned())
        {
            url = Url::parse(&target)
                .map_err(|_| BackendError::invalid("invalid_web_url", "检索结果跳转链接无效"))?;
        }
    }
    resolve(url, cfg!(test)).map(|target| target.url)
}

pub fn search(query: &str, limit: usize) -> Result<Value> {
    let query = query.trim();
    if query.is_empty() || query.len() > 300 || !(1..=5).contains(&limit) {
        return Err(BackendError::invalid(
            "invalid_search_query",
            "检索词须为 1 到 300 字节，结果数须为 1 到 5",
        ));
    }
    #[cfg(test)]
    if SEARCH_ENDPOINT.lock().unwrap().is_none() {
        return Err(BackendError::new(
            "web_network_error",
            "测试未配置回环检索服务",
        ));
    }
    let mut endpoint = Url::parse(&search_endpoint())
        .map_err(|_| BackendError::new("search_unavailable", "公开检索入口无效"))?;
    // 10#19：kl 区域参数让中文查询拿到本地化结果，而不是默认美区
    endpoint
        .query_pairs_mut()
        .append_pair("q", query)
        .append_pair("kl", "cn-zh");
    let document = document(endpoint, 0)?;
    let parsed = html::parse(&document.body, true)?;
    let mut results = Vec::new();
    let mut rejected = Vec::new();
    let mut seen = BTreeSet::new();
    // 10#20：候选普遍不可达时不再无限串行抓取（单次 12s 超时 × 全部候选
    // 会阻塞分钟级且无中间事件），最多尝试验证 10 个候选
    const MAX_CANDIDATE_FETCHES: usize = 10;
    let mut fetches = 0usize;
    for candidate in parsed
        .candidates
        .into_iter()
        .filter(|candidate| !candidate.href.is_empty() && !candidate.title.is_empty())
    {
        if results.len() >= limit || fetches >= MAX_CANDIDATE_FETCHES {
            break;
        }
        fetches += 1;
        match candidate_url(&document.url, &candidate.href).and_then(|url| {
            if !seen.insert(url.to_string()) {
                return Ok(None);
            }
            fetch(url.as_str()).map(Some)
        }) {
            Ok(Some(page)) => results.push(json!({"title":page.title,"url":page.url,"snippet":candidate.snippet,"sha256":page.sha256,"verified":true})),
            Ok(None) => {}
            Err(error) => rejected.push(json!({"title":candidate.title,"error":{"code":error.code,"message":error.message}})),
        }
    }
    // 10#20：rejected 明细只保留前 5 条，其余以计数说明，避免工具结果无上限膨胀
    if rejected.len() > 5 {
        let omitted = rejected.len() - 5;
        rejected.truncate(5);
        rejected.push(json!({"note": format!("另有 {omitted} 个候选验证失败，已省略")}));
    }
    if results.is_empty() {
        return Err(BackendError::new(
            "search_unavailable",
            "公开检索没有返回可验证的网页结果，未生成替代链接",
        ));
    }
    Ok(json!({"query":query,"provider":"DuckDuckGo HTML","results":results,"rejected":rejected}))
}
