use super::super::error::{BackendError, Result};
use super::{ChatMessage, ProviderProfile};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::Read;
use std::net::ToSocketAddrs;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const MAX_SSE_LINE_BYTES: usize = 64 * 1024;
const MAX_COMPLETION_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenUsage {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cached_input_tokens: Option<u64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatResult {
    pub text: String,
    pub model: String,
    pub usage: Option<TokenUsage>,
    pub finish_reason: Option<String>,
    pub latency_ms: u64,
    pub attempts: u8,
}

#[derive(Debug)]
pub struct ChatError {
    pub error: BackendError,
    pub partial_text: String,
    pub usage: Option<TokenUsage>,
    pub attempts: u8,
}

impl ChatError {
    fn new(code: &str, message: &str) -> Self {
        Self {
            error: BackendError::new(code, message),
            partial_text: String::new(),
            usage: None,
            attempts: 1,
        }
    }

    fn partial(self, text: String, usage: Option<TokenUsage>) -> Self {
        Self {
            error: self.error,
            partial_text: text,
            usage,
            attempts: self.attempts,
        }
    }
}

impl From<BackendError> for ChatError {
    fn from(error: BackendError) -> Self {
        Self {
            error,
            partial_text: String::new(),
            usage: None,
            attempts: 1,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Delta {
    pub text: String,
    pub reasoning: bool,
}

fn public_address(address: std::net::IpAddr) -> bool {
    match address {
        std::net::IpAddr::V4(address) => {
            let [a, b, _, _] = address.octets();
            !(address.is_unspecified()
                || address.is_loopback()
                || address.is_private()
                || address.is_link_local()
                || address.is_broadcast()
                || address.is_documentation()
                || a == 0
                || (a == 100 && (64..=127).contains(&b))
                || (a == 192 && b == 0)
                || (a == 198 && (18..=19).contains(&b))
                || a >= 224)
        }
        std::net::IpAddr::V6(address) => {
            if let Some(address) = address.to_ipv4_mapped() {
                return public_address(address.into());
            }
            let segments = address.segments();
            let global_unicast = segments[0] & 0xe000 == 0x2000
                || (segments[0] == 0x0064
                    && segments[1] == 0xff9b
                    && segments[2] == 0
                    && segments[3] == 0);
            global_unicast
                && !address.is_unspecified()
                && !address.is_loopback()
                && !address.is_unique_local()
                && !address.is_unicast_link_local()
                && !address.is_multicast()
                && !(segments[0] == 0x2001 && segments[1] == 0x0db8)
                && !(segments[0] == 0x2001 && segments[1] == 0x0002)
                && !(segments[0] == 0x2001 && segments[1] & 0xfff0 == 0x0010)
                && !(segments[0] == 0x0100 && segments[1] == 0)
                && !(segments[0] & 0xfff0 == 0x3fff)
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum EndpointScope {
    Remote,
    LocalHttp,
    LocalHttps,
}

fn endpoint_scope(url: &reqwest::Url, kind: &str) -> EndpointScope {
    if kind != "本地模型" {
        EndpointScope::Remote
    } else if url.scheme() == "http" {
        EndpointScope::LocalHttp
    } else {
        EndpointScope::LocalHttps
    }
}

fn socket_addresses(
    host: &str,
    port: u16,
    scope: EndpointScope,
) -> Result<Vec<std::net::SocketAddr>> {
    let addresses = (host, port)
        .to_socket_addrs()
        .map_err(|_| BackendError::new("endpoint_unreachable", "无法解析模型服务地址"))?
        .collect::<Vec<_>>();
    if addresses.is_empty() {
        return Err(BackendError::new(
            "endpoint_unreachable",
            "模型服务地址没有可用 IP",
        ));
    }
    if scope == EndpointScope::LocalHttp
        && addresses.iter().any(|address| public_address(address.ip()))
    {
        return Err(BackendError::new(
            "invalid_local_endpoint",
            "本地 HTTP 服务仅允许回环或私网地址；公网服务必须使用 HTTPS",
        ));
    }
    if scope == EndpointScope::Remote
        && addresses
            .iter()
            .any(|address| !public_address(address.ip()))
    {
        return Err(BackendError::new(
            "ssrf_blocked",
            "远程模型地址不得指向本机、私网或保留地址",
        ));
    }
    Ok(addresses)
}

pub(crate) fn endpoint(base_url: &str, kind: &str, suffix: &str) -> Result<reqwest::Url> {
    let raw_path = base_url.split(['?', '#']).next().unwrap_or(base_url);
    if raw_path
        .split('/')
        .any(|part| part.to_ascii_lowercase().replace("%2e", ".") == "..")
    {
        return Err(BackendError::new(
            "invalid_endpoint",
            "Base URL 不得包含父级路径",
        ));
    }
    let mut url = reqwest::Url::parse(base_url)
        .map_err(|_| BackendError::new("invalid_endpoint", "Base URL 格式无效"))?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(BackendError::new(
            "invalid_endpoint",
            "Base URL 包含不安全的地址、凭据或路径",
        ));
    }
    let host = url
        .host_str()
        .ok_or_else(|| BackendError::new("invalid_endpoint", "Base URL 缺少主机"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| BackendError::new("invalid_endpoint", "Base URL 缺少有效端口"))?;
    if kind != "本地模型" && url.scheme() != "https" {
        return Err(BackendError::new(
            "insecure_endpoint",
            "远程模型服务必须使用 HTTPS",
        ));
    }
    let _ = socket_addresses(host, port, endpoint_scope(&url, kind))?;
    url.path_segments_mut()
        .map_err(|_| BackendError::new("invalid_endpoint", "Base URL 不能作为服务根地址"))?
        .pop_if_empty()
        .push(suffix);
    Ok(url)
}

fn client(url: &reqwest::Url, kind: &str) -> Result<reqwest::blocking::Client> {
    let (host, addresses) = pinned_addresses(url, kind)?;
    reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .resolve_to_addrs(&host, &addresses)
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|_| BackendError::new("http_client_error", "无法初始化原生网络客户端"))
}

fn streaming_client(url: &reqwest::Url, kind: &str) -> Result<reqwest::Client> {
    let (host, addresses) = pinned_addresses(url, kind)?;
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .resolve_to_addrs(&host, &addresses)
        .connect_timeout(Duration::from_secs(5))
        .build()
        .map_err(|_| BackendError::new("http_client_error", "无法初始化原生网络客户端"))
}

fn pinned_addresses(url: &reqwest::Url, kind: &str) -> Result<(String, Vec<std::net::SocketAddr>)> {
    let host = url
        .host_str()
        .ok_or_else(|| BackendError::new("invalid_endpoint", "Base URL 缺少主机"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| BackendError::new("invalid_endpoint", "Base URL 缺少有效端口"))?;
    let addresses = socket_addresses(host, port, endpoint_scope(url, kind))?;
    Ok((host.to_string(), addresses))
}

fn authorize(
    request: reqwest::blocking::RequestBuilder,
    key: &Option<Zeroizing<String>>,
) -> reqwest::blocking::RequestBuilder {
    match key {
        Some(key) => request.bearer_auth(key.as_str()),
        None => request,
    }
}

fn authorize_async(
    request: reqwest::RequestBuilder,
    key: &Option<Zeroizing<String>>,
) -> reqwest::RequestBuilder {
    match key {
        Some(key) => request.bearer_auth(key.as_str()),
        None => request,
    }
}

fn read_json(response: reqwest::blocking::Response) -> Result<Value> {
    let status = response.status().as_u16();
    if status != 200 {
        return Err(http_error(status));
    }
    let mut bytes = Vec::new();
    response
        .take((MAX_RESPONSE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::new("invalid_response", "模型服务响应读取失败"))?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(BackendError::new(
            "response_too_large",
            "模型服务响应超过 1 MB 上限",
        ));
    }
    serde_json::from_slice(&bytes)
        .map_err(|_| BackendError::new("invalid_response", "模型服务返回的不是有效 JSON"))
}

fn http_error(status: u16) -> BackendError {
    match status {
        401 | 403 => BackendError::new(
            "authentication_failed",
            "模型服务拒绝了凭据，请检查 API Key",
        ),
        404 => BackendError::new("endpoint_not_found", "模型服务不存在该 API 端点"),
        408 => BackendError::new("provider_timeout", "模型服务请求超时"),
        429 => BackendError::new("rate_limited", "模型服务触发了速率限制"),
        500..=599 => BackendError::new("provider_unavailable", "模型服务暂时不可用"),
        _ => BackendError::new("provider_error", "模型服务返回了不可用的响应"),
    }
}

pub fn list_models(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
) -> Result<Vec<String>> {
    if profile.protocol != "OpenAI Chat" {
        return Err(BackendError::new(
            "unsupported_protocol",
            "该协议尚未接入原生实现",
        ));
    }
    if profile.kind != "本地模型" && key.is_none() {
        return Err(BackendError::new(
            "credential_missing",
            "请先在系统密钥库中配置 API Key",
        ));
    }
    let url = endpoint(&profile.base_url, &profile.kind, "models")?;
    let response = authorize(client(&url, &profile.kind)?.get(url), &key)
        .header("Accept", "application/json")
        .send()
        .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接模型服务端点"))?;
    let body = read_json(response)?;
    let models = body
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| BackendError::new("invalid_response", "模型列表响应缺少 data 数组"))?;
    let mut result = models
        .iter()
        .filter_map(|model| model.get("id").and_then(Value::as_str))
        .filter(|id| !id.trim().is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    result.sort();
    result.dedup();
    if result.is_empty() {
        return Err(BackendError::new(
            "invalid_response",
            "模型服务没有返回可用模型",
        ));
    }
    Ok(result)
}

fn usage(body: &Value) -> Option<TokenUsage> {
    let usage = body.get("usage")?;
    if !usage.is_object() {
        return None;
    }
    Some(TokenUsage {
        input_tokens: usage.get("prompt_tokens").and_then(Value::as_u64),
        output_tokens: usage.get("completion_tokens").and_then(Value::as_u64),
        cached_input_tokens: usage
            .get("prompt_tokens_details")
            .and_then(|details| details.get("cached_tokens"))
            .and_then(Value::as_u64),
    })
}

fn completion(body: &Value) -> Result<(String, Option<String>)> {
    let choice = body
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .ok_or_else(|| BackendError::new("invalid_response", "模型响应缺少 choices"))?;
    let text = choice
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_str)
        .ok_or_else(|| BackendError::new("invalid_response", "模型响应缺少文本内容"))?;
    Ok((
        text.to_string(),
        choice
            .get("finish_reason")
            .and_then(Value::as_str)
            .map(str::to_string),
    ))
}

pub fn chat_once(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
) -> Result<ChatResult> {
    if profile.protocol != "OpenAI Chat" {
        return Err(BackendError::new(
            "unsupported_protocol",
            "该协议尚未接入原生实现",
        ));
    }
    if profile.kind != "本地模型" && key.is_none() {
        return Err(BackendError::new(
            "credential_missing",
            "请先在系统密钥库中配置 API Key",
        ));
    }
    validate_messages(messages, max_tokens)?;
    let url = endpoint(&profile.base_url, &profile.kind, "chat/completions")?;
    let started = Instant::now();
    let response = authorize(client(&url, &profile.kind)?.post(url), &key)
        .json(&json!({
            "model": profile.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": false
        }))
        .send()
        .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接模型服务端点"))?;
    let body = read_json(response)?;
    let (text, finish_reason) = completion(&body)?;
    Ok(ChatResult {
        text,
        model: body
            .get("model")
            .and_then(Value::as_str)
            .unwrap_or(&profile.default_model)
            .to_string(),
        usage: usage(&body),
        finish_reason,
        latency_ms: started.elapsed().as_millis() as u64,
        attempts: 1,
    })
}

fn validate_messages(messages: &[ChatMessage], max_tokens: u32) -> Result<()> {
    if messages.is_empty() || messages.len() > 64 {
        return Err(BackendError::new(
            "invalid_messages",
            "对话消息必须包含 1 到 64 条内容",
        ));
    }
    if !(1..=8192).contains(&max_tokens) {
        return Err(BackendError::new(
            "invalid_max_tokens",
            "max_tokens 必须位于 1 到 8192",
        ));
    }
    let total = messages
        .iter()
        .map(|message| message.content.len())
        .sum::<usize>();
    if total > 256 * 1024 {
        return Err(BackendError::new(
            "context_too_large",
            "单次模型上下文不能超过 256 KB",
        ));
    }
    if messages.iter().any(|message| {
        !matches!(message.role.as_str(), "system" | "user" | "assistant")
            || message.content.trim().is_empty()
    }) {
        return Err(BackendError::new("invalid_messages", "消息角色或内容无效"));
    }
    Ok(())
}

pub fn stream_chat<F>(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    cancel: Arc<AtomicBool>,
    mut on_delta: F,
) -> std::result::Result<ChatResult, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    if profile.protocol != "OpenAI Chat" {
        return Err(ChatError::new(
            "unsupported_protocol",
            "该协议尚未接入原生实现",
        ));
    }
    if profile.kind != "本地模型" && key.is_none() {
        return Err(ChatError::new(
            "credential_missing",
            "请先在系统密钥库中配置 API Key",
        ));
    }
    validate_messages(messages, max_tokens).map_err(ChatError::from)?;
    for attempt in 1..=2u8 {
        let started = Instant::now();
        match tauri::async_runtime::block_on(stream_once(
            profile,
            &key,
            messages,
            max_tokens,
            &cancel,
            &mut on_delta,
        )) {
            Ok((text, model, usage, finish_reason)) => {
                return Ok(ChatResult {
                    text,
                    model,
                    usage,
                    finish_reason,
                    latency_ms: started.elapsed().as_millis() as u64,
                    attempts: attempt,
                })
            }
            Err(mut error) => {
                let retry = matches!(
                    error.error.code.as_str(),
                    "provider_unavailable" | "rate_limited"
                );
                if !retry || attempt == 2 || !error.partial_text.is_empty() {
                    error.attempts = attempt;
                    return Err(error);
                }
            }
        }
    }
    unreachable!()
}

struct StreamOutput {
    text: String,
    model: String,
    usage: Option<TokenUsage>,
    finish_reason: Option<String>,
}

impl StreamOutput {
    fn error(&self, code: &str, message: &str) -> ChatError {
        ChatError::new(code, message).partial(self.text.clone(), self.usage.clone())
    }

    fn finish(
        self,
        done: bool,
    ) -> std::result::Result<(String, String, Option<TokenUsage>, Option<String>), ChatError> {
        if !done {
            return Err(self.error("incomplete_stream", "模型流式响应在结束标记前中断"));
        }
        if self.text.is_empty() {
            return Err(self.error("empty_completion", "模型没有返回文本内容"));
        }
        Ok((self.text, self.model, self.usage, self.finish_reason))
    }
}

fn consume_line<F>(
    line: &[u8],
    output: &mut StreamOutput,
    on_delta: &mut F,
) -> std::result::Result<bool, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    let line = std::str::from_utf8(line)
        .map_err(|_| output.error("invalid_response", "流式事件包含无效 UTF-8"))?
        .trim_end_matches(['\r', '\n']);
    if line.is_empty() || line.starts_with(':') {
        return Ok(false);
    }
    let Some(data) = line.strip_prefix("data:") else {
        return Ok(false);
    };
    let data = data.trim();
    if data.is_empty() {
        return Ok(false);
    }
    if data == "[DONE]" {
        return Ok(true);
    }
    let body: Value = serde_json::from_str(data)
        .map_err(|_| output.error("invalid_response", "流式事件不是有效 JSON"))?;
    if body.get("error").is_some() {
        return Err(output.error("provider_error", "模型服务在流式响应中返回了错误"));
    }
    if let Some(model) = body.get("model").and_then(Value::as_str) {
        output.model = model.to_string();
    }
    if let Some(usage) = usage(&body) {
        output.usage = Some(usage);
    }
    let Some(choice) = body
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
    else {
        return Ok(false);
    };
    if let Some(reason) = choice.get("finish_reason").and_then(Value::as_str) {
        output.finish_reason = Some(reason.to_string());
    }
    if let Some(delta) = choice.get("delta") {
        for (field, reasoning) in [("content", false), ("reasoning_content", true)] {
            if let Some(text) = delta.get(field).and_then(Value::as_str) {
                if text.is_empty() {
                    continue;
                }
                if output.text.len().saturating_add(text.len()) > MAX_COMPLETION_BYTES {
                    return Err(output.error("response_too_large", "模型输出超过 1 MB 上限"));
                }
                on_delta(Delta {
                    text: text.to_string(),
                    reasoning,
                })
                .map_err(|error| {
                    ChatError::from(error).partial(output.text.clone(), output.usage.clone())
                })?;
                output.text.push_str(text);
            }
        }
    }
    Ok(false)
}

async fn stream_once<F>(
    profile: &ProviderProfile,
    key: &Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    cancel: &Arc<AtomicBool>,
    on_delta: &mut F,
) -> std::result::Result<(String, String, Option<TokenUsage>, Option<String>), ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    let url =
        endpoint(&profile.base_url, &profile.kind, "chat/completions").map_err(ChatError::from)?;
    if cancel.load(Ordering::Acquire) {
        return Err(ChatError::new("cancelled", "模型请求已取消"));
    }
    let request = authorize_async(
        streaming_client(&url, &profile.kind)
            .map_err(ChatError::from)?
            .post(url),
        key,
    )
    .json(&json!({
        "model": profile.default_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": true,
        "stream_options": {"include_usage": true}
    }));
    let mut response = match tokio::time::timeout(Duration::from_secs(45), request.send()).await {
        Ok(Ok(response)) => response,
        Ok(Err(error)) if error.is_timeout() => {
            return Err(ChatError::new("provider_timeout", "模型服务响应超时"))
        }
        Ok(Err(_)) => {
            return Err(ChatError::new(
                "endpoint_unreachable",
                "无法连接模型服务端点",
            ))
        }
        Err(_) => return Err(ChatError::new("provider_timeout", "模型服务响应头等待超时")),
    };
    let status = response.status().as_u16();
    if status != 200 {
        return Err(ChatError::from(http_error(status)));
    }
    let mut output = StreamOutput {
        text: String::new(),
        model: profile.default_model.clone(),
        usage: None,
        finish_reason: None,
    };
    let mut pending = Vec::new();
    let mut last_chunk = Instant::now();
    let mut done = false;
    loop {
        if cancel.load(Ordering::Acquire) {
            return Err(output.error("cancelled", "模型请求已取消"));
        }
        match tokio::time::timeout(Duration::from_millis(100), response.chunk()).await {
            Ok(Ok(Some(chunk))) => {
                last_chunk = Instant::now();
                pending.extend_from_slice(&chunk);
                while let Some(end) = pending.iter().position(|byte| *byte == b'\n') {
                    let line = pending.drain(..=end).collect::<Vec<_>>();
                    done = consume_line(&line, &mut output, on_delta)?;
                    if done {
                        break;
                    }
                }
                if done {
                    break;
                }
                if pending.len() > MAX_SSE_LINE_BYTES {
                    return Err(output.error("response_too_large", "流式事件超过 64 KB 上限"));
                }
            }
            Ok(Ok(None)) => {
                if !pending.is_empty() {
                    done = consume_line(&pending, &mut output, on_delta)?;
                }
                break;
            }
            Ok(Err(_)) => return Err(output.error("invalid_response", "流式响应读取失败")),
            Err(_) => {
                if last_chunk.elapsed() >= Duration::from_secs(120) {
                    return Err(output.error("provider_timeout", "模型服务超过 120 秒没有发送数据"));
                }
            }
        }
    }
    output.finish(done)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::sync::Mutex;

    fn profile(address: &str) -> ProviderProfile {
        ProviderProfile {
            id: "p-test".into(),
            name: "test".into(),
            preset: "custom".into(),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: address.into(),
            models: vec!["test-model".into()],
            default_model: "test-model".into(),
            has_key: false,
            health: "unknown".into(),
            latency_ms: None,
            enabled: true,
            note: None,
        }
    }

    fn server<F>(handler: F) -> String
    where
        F: FnOnce(&str) -> String + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let count = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..count]).to_string();
            let body = handler(&request);
            let response = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
            stream.write_all(response.as_bytes()).unwrap();
        });
        address
    }

    fn messages() -> Vec<ChatMessage> {
        vec![ChatMessage {
            role: "user".into(),
            content: "hello".into(),
        }]
    }

    #[test]
    fn models_and_chat_use_real_protocol() {
        let address = server(|request| {
            assert!(request.contains("/models"));
            r#"{"data":[{"id":"real-model"}]}"#.into()
        });
        assert_eq!(
            list_models(&profile(&address), None).unwrap(),
            vec!["real-model"]
        );
        let address = server(|request| {
            assert!(request.contains("test-model"));
            r#"{"model":"test-model","choices":[{"message":{"content":"actual answer"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}"#.into()
        });
        let result = chat_once(&profile(&address), None, &messages(), 1).unwrap();
        assert_eq!(result.text, "actual answer");
        assert_eq!(result.usage.unwrap().input_tokens, Some(3));
    }

    #[test]
    fn sse_preserves_real_deltas_usage_and_cancellation() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let address = server(|_| {
            "data: {\"choices\":[{\"delta\":{\"content\":\"先验\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\" 后验\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":8,\"completion_tokens\":2},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"
                .into()
        });
        let cancel = Arc::new(AtomicBool::new(false));
        let result = stream_chat(&profile(&address), None, &messages(), 8, cancel, |delta| {
            events.lock().unwrap().push(delta.text);
            Ok(())
        })
        .unwrap();
        assert_eq!(result.text, "先验 后验");
        assert_eq!(result.usage.unwrap().output_tokens, Some(2));
        assert_eq!(events.lock().unwrap().as_slice(), ["先验", " 后验"]);

        let address =
            server(|_| "data: {\"choices\":[{\"delta\":{\"content\":\"first\"}}]}\n\n".into());
        let cancel = Arc::new(AtomicBool::new(true));
        let error =
            stream_chat(&profile(&address), None, &messages(), 8, cancel, |_| Ok(())).unwrap_err();
        assert_eq!(error.error.code, "cancelled");
        assert!(error.partial_text.is_empty());
        assert!(error.usage.is_none());
    }

    #[test]
    fn errors_are_typed_and_private_remote_addresses_rejected() {
        let address = server(|_| "{\"data\":[]}".into());
        let error = list_models(&profile(&address), None).unwrap_err();
        assert_eq!(error.code, "invalid_response");
        let mut remote = profile("https://127.0.0.1/v1");
        remote.kind = "兼容服务".into();
        assert_eq!(
            list_models(&remote, None).unwrap_err().code,
            "credential_missing"
        );
        assert_eq!(
            list_models(&remote, Some(Zeroizing::new("test-token".into())))
                .unwrap_err()
                .code,
            "ssrf_blocked"
        );
        assert_eq!(
            endpoint("https://[2001:db8::1]/v1", "兼容服务", "models")
                .unwrap_err()
                .code,
            "ssrf_blocked"
        );
        assert_eq!(
            endpoint("http://8.8.8.8/v1", "本地模型", "models")
                .unwrap_err()
                .code,
            "invalid_local_endpoint"
        );
        assert_eq!(
            endpoint(
                "http://127.0.0.1:8000/v1/%2e%2e/admin",
                "本地模型",
                "models"
            )
            .unwrap_err()
            .code,
            "invalid_endpoint"
        );
    }
}
