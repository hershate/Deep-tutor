use super::super::error::{BackendError, Result};
use super::tools::{ToolCall, ToolCallRequest, ToolDefinition};
use super::{ChatMessage, ProviderProfile};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
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
    pub reasoning: String,
    pub tool_calls: Vec<ToolCall>,
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
    pub partial_reasoning: String,
    pub partial_tool_calls: Vec<ToolCall>,
    pub usage: Option<TokenUsage>,
    pub attempts: u8,
}

impl ChatError {
    pub(crate) fn new(code: &str, message: &str) -> Self {
        Self {
            error: BackendError::new(code, message),
            partial_text: String::new(),
            partial_reasoning: String::new(),
            partial_tool_calls: Vec::new(),
            usage: None,
            attempts: 1,
        }
    }

    pub(crate) fn partial(self, text: String, usage: Option<TokenUsage>) -> Self {
        Self {
            error: self.error,
            partial_text: text,
            partial_reasoning: String::new(),
            partial_tool_calls: Vec::new(),
            usage,
            attempts: self.attempts,
        }
    }

    pub(crate) fn with_reasoning(mut self, reasoning: String) -> Self {
        self.partial_reasoning = reasoning;
        self
    }

    pub(crate) fn with_tool_calls(mut self, calls: Vec<ToolCall>) -> Self {
        self.partial_tool_calls = calls;
        self
    }
}

impl From<BackendError> for ChatError {
    fn from(error: BackendError) -> Self {
        Self {
            error,
            partial_text: String::new(),
            partial_reasoning: String::new(),
            partial_tool_calls: Vec::new(),
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

pub(crate) fn public_address(address: std::net::IpAddr) -> bool {
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
            // 31#30：NAT64 well-known 前缀 64:ff9b::/96 须递归校验内嵌 IPv4
            // ——原先只认前缀，AAAA 直设 64:ff9b::c0a8:0166（内嵌 192.168.1.102）
            // 可过公网检查
            if segments[0] == 0x0064
                && segments[1] == 0xff9b
                && segments[2] == 0
                && segments[3] == 0
            {
                let embedded = std::net::Ipv4Addr::new(
                    (segments[4] >> 8) as u8,
                    (segments[4] & 0xff) as u8,
                    (segments[5] >> 8) as u8,
                    (segments[5] & 0xff) as u8,
                );
                return public_address(std::net::IpAddr::V4(embedded));
            }
            let global_unicast = segments[0] & 0xe000 == 0x2000;
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
                // 3fff::/20（RFC 9637 文档段，含旧 6bone 3ffe::/16）：
                // 前 20 位掩码比较，低 4 位恒 0，须与 0x3ff0 比较（08#1，
                // 原写法 0x3fff 低 4 位为 f，恒不相等属死代码）
                && !(segments[0] & 0xfff0 == 0x3ff0)
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
        .extend(suffix.split('/').filter(|part| !part.is_empty()));
    let path = url.path().replace("%3A", ":").replace("%3a", ":");
    url.set_path(&path);
    Ok(url)
}

pub(crate) fn client(url: &reqwest::Url, kind: &str) -> Result<reqwest::blocking::Client> {
    let (host, addresses) = pinned_addresses(url, kind)?;
    reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .resolve_to_addrs(&host, &addresses)
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|_| BackendError::new("http_client_error", "无法初始化原生网络客户端"))
}

pub(crate) fn streaming_client(url: &reqwest::Url, kind: &str) -> Result<reqwest::Client> {
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

pub(crate) fn authorize_async(
    request: reqwest::RequestBuilder,
    key: &Option<Zeroizing<String>>,
) -> reqwest::RequestBuilder {
    match key {
        Some(key) => request.bearer_auth(key.as_str()),
        None => request,
    }
}

pub(crate) fn read_json(response: reqwest::blocking::Response) -> Result<Value> {
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

pub(crate) fn http_error(status: u16) -> BackendError {
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
    if !matches!(
        profile.protocol.as_str(),
        "OpenAI Chat" | "OpenAI Responses"
    ) {
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

fn parse_tool_calls(message: &Value) -> Result<Vec<ToolCall>> {
    let Some(calls) = message.get("tool_calls").and_then(Value::as_array) else {
        return Ok(Vec::new());
    };
    let requests: Vec<ToolCallRequest> = serde_json::from_value(Value::Array(calls.clone()))
        .map_err(|_| BackendError::new("invalid_tool_call", "工具调用结构无效"))?;
    requests
        .into_iter()
        .map(|call| {
            if call.kind != "function" {
                return Err(BackendError::new(
                    "unsupported_tool_type",
                    "仅支持函数工具调用",
                ));
            }
            if call.id.trim().is_empty() || call.function.name.trim().is_empty() {
                return Err(BackendError::new(
                    "invalid_tool_call",
                    "工具调用缺少 ID 或函数名",
                ));
            }
            let arguments = if call.function.arguments.trim().is_empty() {
                "{}".into()
            } else {
                call.function.arguments
            };
            if !serde_json::from_str::<Value>(&arguments)
                .map_err(|_| BackendError::new("invalid_tool_arguments", "工具参数不是有效 JSON"))?
                .is_object()
            {
                return Err(BackendError::new(
                    "invalid_tool_arguments",
                    "工具参数必须是 JSON 对象",
                ));
            }
            Ok(ToolCall {
                id: call.id,
                name: call.function.name,
                arguments,
            })
        })
        .collect()
}

fn completion(body: &Value) -> Result<(String, String, Option<String>, Vec<ToolCall>)> {
    let choice = body
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .ok_or_else(|| BackendError::new("invalid_response", "模型响应缺少 choices"))?;
    let message = choice
        .get("message")
        .ok_or_else(|| BackendError::new("invalid_response", "模型响应缺少 message"))?;
    let text = message
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let tool_calls = parse_tool_calls(message)?;
    if text.is_empty() && tool_calls.is_empty() {
        return Err(BackendError::new(
            "empty_completion",
            "模型没有返回文本内容或工具调用",
        ));
    }
    Ok((
        text.to_string(),
        message
            .get("reasoning_content")
            .or_else(|| message.get("reasoning"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        choice
            .get("finish_reason")
            .and_then(Value::as_str)
            .map(str::to_string),
        tool_calls,
    ))
}

pub fn chat_once(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
) -> Result<ChatResult> {
    chat_once_with_tools(profile, key, messages, max_tokens, None)
}

pub fn chat_once_with_tools(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
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
    // OpenAI 官方限制工具名须匹配 ^[a-zA-Z0-9_-]{1,64}$（10#8）：
    // 请求侧统一消毒（本地 web.fetch 等 → web_fetch），响应侧回译
    let names = super::protocol::ToolNames::new(tools);
    let mut body = json!({
        "model": profile.default_model,
        "messages": remote_messages(&names, messages),
        "max_tokens": max_tokens,
        "stream": false
    });
    if !names.definitions.is_empty() {
        body["tools"] = names
            .definitions
            .iter()
            .map(|(name, definition)| {
                json!({"type": "function", "function": {"name": name, "description": definition.description, "parameters": definition.parameters}})
            })
            .collect::<Vec<_>>()
            .into();
        body["tool_choice"] = json!("auto");
    }
    let response = authorize(client(&url, &profile.kind)?.post(url), &key)
        .json(&body)
        .send()
        .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接模型服务端点"))?;
    let body = read_json(response)?;
    let (text, reasoning, finish_reason, mut tool_calls) = completion(&body)?;
    for call in tool_calls.iter_mut() {
        call.name = names.local(&call.name);
    }
    let tool_calls = tool_calls;
    Ok(ChatResult {
        text,
        reasoning,
        tool_calls,
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

pub(crate) fn validate_messages(messages: &[ChatMessage], max_tokens: u32) -> Result<()> {
    if messages.is_empty() || messages.len() > 64 {
        return Err(BackendError::new(
            "invalid_messages",
            "对话消息必须包含 1 到 64 条内容",
        ));
    }
    // 上限 16384：图谱步骤对齐“最多 100 节点”的校验上界（10#16），
    // 流水线截断类失败重试也会在此界内翻倍（10#12）
    if !(1..=16384).contains(&max_tokens) {
        return Err(BackendError::new(
            "invalid_max_tokens",
            "max_tokens 必须位于 1 到 16384",
        ));
    }
    let total = messages
        .iter()
        .map(|message| {
            message.content.len()
                + message.tool_calls.as_ref().map_or(0, |calls| {
                    calls
                        .iter()
                        .map(|call| call.function.arguments.len())
                        .sum::<usize>()
                })
        })
        .sum::<usize>();
    if total > 256 * 1024 {
        return Err(BackendError::new(
            "context_too_large",
            "单次模型上下文不能超过 256 KB",
        ));
    }
    for message in messages {
        let valid = match message.role.as_str() {
            "system" | "user" => !message.content.trim().is_empty(),
            "assistant" => {
                !message.content.trim().is_empty()
                    || message
                        .tool_calls
                        .as_ref()
                        .is_some_and(|calls| !calls.is_empty())
            }
            "tool" => {
                !message.content.trim().is_empty()
                    && message
                        .tool_call_id
                        .as_deref()
                        .is_some_and(|id| !id.is_empty())
                    && message.name.as_deref().is_some_and(|name| !name.is_empty())
            }
            _ => false,
        };
        if !valid {
            return Err(BackendError::new(
                "invalid_messages",
                "消息角色、内容或工具关联无效",
            ));
        }
        if let Some(calls) = &message.tool_calls {
            for call in calls {
                if call.kind != "function"
                    || call.id.trim().is_empty()
                    || call.function.name.trim().is_empty()
                    || !serde_json::from_str::<Value>(&call.function.arguments)
                        .map_err(|_| {
                            BackendError::new("invalid_tool_arguments", "工具参数不是有效 JSON")
                        })?
                        .is_object()
                {
                    return Err(BackendError::new(
                        "invalid_tool_call",
                        "消息中的工具调用无效",
                    ));
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
pub fn stream_chat<F>(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    cancel: Arc<AtomicBool>,
    on_delta: F,
) -> std::result::Result<ChatResult, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    stream_chat_with_tools(profile, key, messages, max_tokens, None, cancel, on_delta)
}

#[derive(Clone, Copy)]
pub struct StreamPolicy {
    pub max_attempts: u8,
    pub request_timeout: Duration,
    pub chunk_timeout: Duration,
}

impl Default for StreamPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 2,
            request_timeout: Duration::from_secs(45),
            chunk_timeout: Duration::from_secs(120),
        }
    }
}

#[cfg(test)]
pub fn stream_chat_with_tools<F>(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
    cancel: Arc<AtomicBool>,
    on_delta: F,
) -> std::result::Result<ChatResult, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    stream_chat_with_policy(
        profile,
        key,
        messages,
        max_tokens,
        tools,
        cancel,
        None,
        StreamPolicy::default(),
        on_delta,
    )
}

pub fn stream_chat_with_policy<F>(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
    cancel: Arc<AtomicBool>,
    parent_cancel: Option<Arc<AtomicBool>>,
    policy: StreamPolicy,
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
    if !(1..=4).contains(&policy.max_attempts) {
        return Err(ChatError::new(
            "invalid_retry_policy",
            "单上游重试次数须为 0 到 3",
        ));
    }
    validate_messages(messages, max_tokens).map_err(ChatError::from)?;
    for attempt in 1..=policy.max_attempts {
        let started = Instant::now();
        match tauri::async_runtime::block_on(stream_once(
            profile,
            &key,
            messages,
            max_tokens,
            tools,
            &cancel,
            &parent_cancel,
            &policy,
            &mut on_delta,
        )) {
            Ok((text, reasoning, model, usage, finish_reason, tool_calls)) => {
                return Ok(ChatResult {
                    text,
                    reasoning,
                    tool_calls,
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
                    "provider_unavailable"
                        | "rate_limited"
                        | "provider_timeout"
                        | "endpoint_unreachable"
                        | "incomplete_stream"
                );
                if !retry
                    || attempt == policy.max_attempts
                    || !error.partial_text.is_empty()
                    || !error.partial_reasoning.is_empty()
                    || !error.partial_tool_calls.is_empty()
                {
                    error.attempts = attempt;
                    return Err(error);
                }
                // 10#2：同上游重试前退避，避免限流窗口内原样重发
                super::protocol::backoff(attempt);
            }
        }
    }
    unreachable!()
}

pub(crate) type StreamSuccess = (
    String,
    String,
    String,
    Option<TokenUsage>,
    Option<String>,
    Vec<ToolCall>,
);

pub(crate) struct StreamOutput {
    pub(crate) text: String,
    pub(crate) reasoning: String,
    pub(crate) model: String,
    pub(crate) usage: Option<TokenUsage>,
    pub(crate) finish_reason: Option<String>,
    pub(crate) tool_calls: BTreeMap<usize, ToolCall>,
}

impl StreamOutput {
    pub(crate) fn new(model: String) -> Self {
        Self {
            text: String::new(),
            reasoning: String::new(),
            model,
            usage: None,
            finish_reason: None,
            tool_calls: BTreeMap::new(),
        }
    }

    pub(crate) fn error(&self, code: &str, message: &str) -> ChatError {
        ChatError::new(code, message)
            .partial(self.text.clone(), self.usage.clone())
            .with_reasoning(self.reasoning.clone())
            .with_tool_calls(self.tool_calls.values().cloned().collect())
    }

    pub(crate) fn finish(self, done: bool) -> std::result::Result<StreamSuccess, ChatError> {
        if !done {
            return Err(self.error("incomplete_stream", "模型流式响应在结束标记前中断"));
        }
        if self.text.is_empty() && self.tool_calls.is_empty() {
            return Err(self.error("empty_completion", "模型没有返回文本内容或工具调用"));
        }
        let partial_text = self.text.clone();
        let partial_reasoning = self.reasoning.clone();
        let partial_tool_calls = self.tool_calls.values().cloned().collect::<Vec<_>>();
        let partial_usage = self.usage.clone();
        let mut calls = Vec::new();
        for call in self.tool_calls.into_values() {
            if call.id.trim().is_empty() || call.name.trim().is_empty() {
                return Err(
                    ChatError::new("invalid_tool_call", "工具调用缺少 ID 或函数名")
                        .partial(partial_text.clone(), partial_usage.clone())
                        .with_reasoning(partial_reasoning.clone())
                        .with_tool_calls(partial_tool_calls.clone()),
                );
            }
            let arguments = if call.arguments.trim().is_empty() {
                "{}".into()
            } else {
                call.arguments
            };
            if !serde_json::from_str::<Value>(&arguments)
                .map_err(|_| {
                    ChatError::new("invalid_tool_arguments", "工具参数不是有效 JSON")
                        .partial(partial_text.clone(), partial_usage.clone())
                        .with_reasoning(partial_reasoning.clone())
                        .with_tool_calls(partial_tool_calls.clone())
                })?
                .is_object()
            {
                return Err(
                    ChatError::new("invalid_tool_arguments", "工具参数必须是 JSON 对象")
                        .partial(partial_text, partial_usage)
                        .with_reasoning(partial_reasoning)
                        .with_tool_calls(partial_tool_calls),
                );
            }
            calls.push(ToolCall {
                id: call.id,
                name: call.name,
                arguments,
            });
        }
        Ok((
            self.text,
            self.reasoning,
            self.model,
            self.usage,
            self.finish_reason,
            calls,
        ))
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
        // 31#39：空字段不覆盖已记录值（与 anthropic/gemini 同口径）——部分
        // 代理/旧 vLLM 中间 chunk 发空 usage:{}，整体覆盖会把已累计计数
        // 抹成 None 落库 missing_usage
        let previous = output.usage.take().unwrap_or(TokenUsage {
            input_tokens: None,
            output_tokens: None,
            cached_input_tokens: None,
        });
        output.usage = Some(TokenUsage {
            input_tokens: usage.input_tokens.or(previous.input_tokens),
            output_tokens: usage.output_tokens.or(previous.output_tokens),
            cached_input_tokens: usage.cached_input_tokens.or(previous.cached_input_tokens),
        });
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
        if let Some(calls) = delta.get("tool_calls").and_then(Value::as_array) {
            for call in calls {
                if call
                    .get("type")
                    .and_then(Value::as_str)
                    .is_some_and(|kind| kind != "function")
                {
                    return Err(output.error("unsupported_tool_type", "仅支持函数工具调用"));
                }
                let index = call.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
                let function = match call.get("function") {
                    Some(function) => function,
                    None => return Err(output.error("invalid_tool_call", "工具调用缺少 function")),
                };
                let target = output.tool_calls.entry(index).or_default();
                if let Some(id) = call.get("id").and_then(Value::as_str) {
                    if !target.id.is_empty() && target.id != id {
                        return Err(output.error("invalid_tool_call", "同一工具索引出现了不同 ID"));
                    }
                    target.id = id.into();
                }
                if let Some(name) = function.get("name").and_then(Value::as_str) {
                    if !target.name.is_empty() && target.name != name {
                        return Err(
                            output.error("invalid_tool_call", "同一工具索引出现了不同函数名")
                        );
                    }
                    if target.name.is_empty() {
                        target.name = name.into();
                    }
                }
                if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
                    if target.arguments.len().saturating_add(arguments.len()) > 256 * 1024 {
                        return Err(
                            output.error("response_too_large", "单个工具参数超过 256 KB 上限")
                        );
                    }
                    target.arguments.push_str(arguments);
                }
            }
        }
        for (field, reasoning) in [
            ("content", false),
            ("reasoning_content", true),
            ("reasoning", true),
        ] {
            if let Some(text) = delta.get(field).and_then(Value::as_str) {
                if text.is_empty() {
                    continue;
                }
                let current_length = if reasoning {
                    output.reasoning.len()
                } else {
                    output.text.len()
                };
                if current_length.saturating_add(text.len()) > MAX_COMPLETION_BYTES {
                    return Err(output.error("response_too_large", "模型输出超过 1 MB 上限"));
                }
                on_delta(Delta {
                    text: text.to_string(),
                    reasoning,
                })
                .map_err(|error| {
                    ChatError::from(error)
                        .partial(output.text.clone(), output.usage.clone())
                        .with_reasoning(output.reasoning.clone())
                })?;
                if reasoning {
                    output.reasoning.push_str(text);
                } else {
                    output.text.push_str(text);
                }
            }
        }
    }
    Ok(false)
}

pub(crate) fn cancelled(cancel: &Arc<AtomicBool>, parent: &Option<Arc<AtomicBool>>) -> bool {
    cancel.load(Ordering::Acquire)
        || parent
            .as_ref()
            .is_some_and(|flag| flag.load(Ordering::Acquire))
}

async fn stream_once<F>(
    profile: &ProviderProfile,
    key: &Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
    cancel: &Arc<AtomicBool>,
    parent_cancel: &Option<Arc<AtomicBool>>,
    policy: &StreamPolicy,
    on_delta: &mut F,
) -> std::result::Result<StreamSuccess, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    let url =
        endpoint(&profile.base_url, &profile.kind, "chat/completions").map_err(ChatError::from)?;
    if cancelled(cancel, parent_cancel) {
        return Err(ChatError::new("cancelled", "模型请求已取消"));
    }
    let names = super::protocol::ToolNames::new(tools);
    let mut body = json!({
        "model": profile.default_model,
        "messages": remote_messages(&names, messages),
        "max_tokens": max_tokens,
        "stream": true,
        "stream_options": {"include_usage": true}
    });
    if !names.definitions.is_empty() {
        body["tools"] = names
            .definitions
            .iter()
            .map(|(name, definition)| {
                json!({"type": "function", "function": {"name": name, "description": definition.description, "parameters": definition.parameters}})
            })
            .collect::<Vec<_>>()
            .into();
        body["tool_choice"] = json!("auto");
    }
    let request = authorize_async(
        streaming_client(&url, &profile.kind)
            .map_err(ChatError::from)?
            .post(url),
        key,
    )
    .json(&body);
    let mut response = match tokio::time::timeout(policy.request_timeout, request.send()).await {
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
        reasoning: String::new(),
        model: profile.default_model.clone(),
        usage: None,
        finish_reason: None,
        tool_calls: BTreeMap::new(),
    };
    let mut pending = Vec::new();
    let mut last_chunk = Instant::now();
    let mut done = false;
    loop {
        if cancelled(cancel, parent_cancel) {
            return Err(output.error("cancelled", "模型请求已取消"));
        }
        let next = tokio::time::timeout(Duration::from_millis(100), response.chunk()).await;
        if cancelled(cancel, parent_cancel) {
            return Err(output.error("cancelled", "模型请求已取消"));
        }
        match next {
            Ok(Ok(Some(chunk))) => {
                last_chunk = Instant::now();
                pending.extend_from_slice(&chunk);
                while let Some(end) = pending.iter().position(|byte| *byte == b'\n') {
                    if cancelled(cancel, parent_cancel) {
                        return Err(output.error("cancelled", "模型请求已取消"));
                    }
                    // 31#36：整行随单次 chunk 到达时同样受限——原先上限只作用于
                    // 无换行的剩余段，>64KB 完整行一次读取即绕过
                    if end + 1 > MAX_SSE_LINE_BYTES {
                        return Err(output.error("response_too_large", "流式事件超过 64 KB 上限"));
                    }
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
                if last_chunk.elapsed() >= policy.chunk_timeout {
                    return Err(output.error(
                        "provider_timeout",
                        &format!(
                            "模型服务超过 {} 秒没有发送数据",
                            policy.chunk_timeout.as_secs()
                        ),
                    ));
                }
            }
        }
    }
    let (text, reasoning, model, usage, finish_reason, mut tool_calls) = output.finish(done)?;
    for call in tool_calls.iter_mut() {
        call.name = names.local(&call.name);
    }
    Ok((text, reasoning, model, usage, finish_reason, tool_calls))
}

/// 请求侧：历史消息构造为 OpenAI wire 格式（15#5）。
/// ChatMessage 结构体带 `rename_all = "camelCase"`（前端 IPC 契约），直接
/// 序列化上线会把 tool_calls/tool_call_id 写成 toolCalls/toolCallId，
/// 严格端点对 tool 消息必报 400——因此这里手工按 snake_case 键名构造，
/// 同时把历史中的本地工具名（含点号）→ 远端消毒名（10#8）。
fn remote_messages(
    names: &super::protocol::ToolNames,
    messages: &[ChatMessage],
) -> serde_json::Value {
    messages
        .iter()
        .map(|message| {
            let mut wire = json!({
                "role": message.role,
                "content": message.content,
            });
            if let Some(calls) = &message.tool_calls {
                wire["tool_calls"] = calls
                    .iter()
                    .map(|call| {
                        json!({
                            "id": call.id,
                            "type": call.kind,
                            "function": {
                                "name": names.remote(&call.function.name),
                                "arguments": call.function.arguments,
                            }
                        })
                    })
                    .collect::<Vec<_>>()
                    .into();
            }
            if let Some(id) = &message.tool_call_id {
                wire["tool_call_id"] = json!(id);
            }
            if let Some(name) = &message.name {
                wire["name"] = json!(names.remote(name));
            }
            wire
        })
        .collect::<Vec<_>>()
        .into()
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
        let _ = crate::backend::security::spawn(move || {
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

    #[test]
    fn hostname_endpoints_drive_resolution_pinning_and_connect() {
        // 12#17：此前全部回环测试用 127.0.0.1 字面量，“域名解析→钉扎→
        // 按钉扎地址连接”的正向链路零覆盖（仅拒绝态负例）。这里用
        // localhost 主机名驱动完整链路连到 127.0.0.1 回环服务。
        let address = server(|_| "{}".into());
        let port = address.trim_start_matches("http://127.0.0.1:").to_string();
        let url: reqwest::Url = format!("http://localhost:{port}/v1/models")
            .parse()
            .unwrap();
        let client = client(&url, "本地模型").unwrap();
        let response = client.get(url).send().unwrap();
        assert_eq!(response.status().as_u16(), 200);
    }

    fn messages() -> Vec<ChatMessage> {
        vec![ChatMessage {
            role: "user".into(),
            content: "hello".into(),
            ..Default::default()
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
    fn tool_calls_use_native_arguments_and_allow_empty_text() {
        let tools = vec![ToolDefinition {
            kind: "function".into(),
            function: super::super::tools::ToolDefinitionFunction {
                name: "memory.recall".into(),
                description: "Read an approved memory entry".into(),
                parameters: json!({"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
            },
        }];
        let address = server(|request| {
            // 10#8：请求侧发送消毒名（点号 → 下划线），不再原样透出本地名
            assert!(request.contains("memory_recall"));
            assert!(!request.contains("memory.recall"));
            assert!(request.contains("\"tools\""));
            r#"{"choices":[{"message":{"content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"memory_recall","arguments":"{\"key\":\"topic\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":5,"completion_tokens":2}}"#.into()
        });
        let result =
            chat_once_with_tools(&profile(&address), None, &messages(), 64, Some(&tools)).unwrap();
        assert_eq!(result.tool_calls.len(), 1);
        assert_eq!(result.tool_calls[0].id, "call-1");
        assert_eq!(result.tool_calls[0].name, "memory.recall");
        assert_eq!(result.tool_calls[0].arguments, r#"{"key":"topic"}"#);

        let address = server(|request| {
            assert!(request.contains("memory_recall"));
            assert!(!request.contains("memory.recall"));
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call-1\",\"type\":\"function\",\"function\":{\"name\":\"memory_recall\",\"arguments\":\"{\\\"key\\\":\"}}]}}]}\n\ndata: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"topic\\\"}\"}}]}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":3},\"choices\":[{\"delta\":{},\"finish_reason\":\"tool_calls\"}]}\n\ndata: [DONE]\n\n".into()
        });
        let result = stream_chat_with_tools(
            &profile(&address),
            None,
            &messages(),
            64,
            Some(&tools),
            Arc::new(AtomicBool::new(false)),
            |_| Ok(()),
        )
        .unwrap();
        assert!(result.text.is_empty());
        assert_eq!(result.tool_calls.len(), 1);
        assert_eq!(result.tool_calls[0].arguments, r#"{"key":"topic"}"#);
        assert_eq!(result.usage.unwrap().input_tokens, Some(7));
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
