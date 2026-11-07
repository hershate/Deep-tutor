#[path = "anthropic.rs"]
mod anthropic;
#[path = "gemini.rs"]
mod gemini;
#[path = "responses.rs"]
mod responses;

use super::super::error::{BackendError, Result};
use super::openai::{
    self, ChatError, ChatResult, Delta, StreamOutput, StreamPolicy, StreamSuccess, TokenUsage,
};
use super::tools::{ToolDefinition, ToolDefinitionFunction};
use super::{ChatMessage, ProviderProfile};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

const MAX_SSE_LINE_BYTES: usize = 64 * 1024;
const MAX_COMPLETION_BYTES: usize = 1024 * 1024;

pub(crate) struct ToolNames {
    pub local_by_remote: HashMap<String, String>,
    pub remote_by_local: HashMap<String, String>,
    pub definitions: Vec<(String, ToolDefinitionFunction)>,
}

impl ToolNames {
    pub fn new(tools: Option<&[ToolDefinition]>) -> Self {
        let mut used = HashSet::new();
        let mut local_by_remote = HashMap::new();
        let mut remote_by_local = HashMap::new();
        let mut definitions = Vec::new();
        for definition in tools.unwrap_or(&[]) {
            let original = &definition.function.name;
            let mut remote = original
                .chars()
                .map(|character| {
                    if character.is_ascii_alphanumeric() || character == '_' || character == '-' {
                        character
                    } else {
                        '_'
                    }
                })
                .collect::<String>();
            if remote.is_empty() {
                remote = "tool".into();
            }
            if remote.len() > 56 {
                remote.truncate(56);
            }
            if used.contains(&remote) {
                let suffix = format!("{:x}", Sha256::digest(original.as_bytes()));
                remote = format!("{}_{}", &remote[..56.min(remote.len())], &suffix[..7]);
            }
            let mut index = 2;
            let base = remote.clone();
            while !used.insert(remote.clone()) {
                remote = format!("{base}_{index}");
                index += 1;
            }
            local_by_remote.insert(remote.clone(), original.clone());
            remote_by_local.insert(original.clone(), remote.clone());
            definitions.push((remote, definition.function.clone()));
        }
        Self {
            local_by_remote,
            remote_by_local,
            definitions,
        }
    }

    /// 响应侧：远端消毒名 → 本地名
    pub fn local(&self, remote: &str) -> String {
        self.local_by_remote
            .get(remote)
            .cloned()
            .unwrap_or_else(|| remote.into())
    }

    /// 请求侧：历史里的本地名（含点号）→ 远端消毒名；查不到原样返回。
    /// 只有 local() 单向表时，请求侧会把带点号的原名发往 Anthropic/Gemini/Responses。
    pub fn remote(&self, local: &str) -> String {
        self.remote_by_local
            .get(local)
            .cloned()
            .unwrap_or_else(|| local.into())
    }
}

pub(crate) fn gemini_schema(value: &Value) -> Value {
    match value {
        Value::Object(object) => {
            let mut result = serde_json::Map::new();
            for (key, value) in object {
                if !matches!(
                    key.as_str(),
                    "type"
                        | "format"
                        | "description"
                        | "nullable"
                        | "required"
                        | "enum"
                        | "properties"
                        | "items"
                        | "maxItems"
                        | "minItems"
                        | "maximum"
                        | "minimum"
                        | "maxLength"
                        | "minLength"
                        | "pattern"
                        | "example"
                        | "default"
                ) {
                    continue;
                }
                if key == "type" {
                    if let Some(kind) = value.as_str() {
                        result.insert(key.clone(), json!(kind.to_uppercase()));
                    } else if let Some(kinds) = value.as_array() {
                        let kind = kinds
                            .iter()
                            .filter_map(Value::as_str)
                            .find(|kind| *kind != "null")
                            .unwrap_or("string");
                        result.insert(key.clone(), json!(kind.to_uppercase()));
                        if kinds.iter().any(|kind| kind == "null") {
                            result.insert("nullable".into(), json!(true));
                        }
                    }
                } else {
                    result.insert(key.clone(), gemini_schema(value));
                }
            }
            Value::Object(result)
        }
        Value::Array(items) => Value::Array(items.iter().map(gemini_schema).collect()),
        _ => value.clone(),
    }
}

pub(crate) fn emit<F>(
    output: &mut StreamOutput,
    text: &str,
    reasoning: bool,
    on_delta: &mut F,
) -> std::result::Result<(), ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    if text.is_empty() {
        return Ok(());
    }
    let current = if reasoning {
        output.reasoning.len()
    } else {
        output.text.len()
    };
    if current.saturating_add(text.len()) > MAX_COMPLETION_BYTES {
        return Err(output.error("response_too_large", "模型输出超过 1 MB 上限"));
    }
    on_delta(Delta {
        text: text.into(),
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
    Ok(())
}

/// 同上游重试前的指数退避（10#2）：429/瞬断后立即原样重发大概率再撞限流。
/// 400ms/800ms/1600ms 封顶；等待发生在同步重试循环内（block_on 上下文）。
pub(crate) fn backoff(failed_attempt: u8) {
    let delay = 400u64 << (failed_attempt.saturating_sub(1)).min(3);
    std::thread::sleep(std::time::Duration::from_millis(delay));
}

pub(crate) fn retryable(error: &ChatError, attempt: u8, policy: &StreamPolicy) -> bool {
    attempt < policy.max_attempts
        && error.partial_text.is_empty()
        && error.partial_reasoning.is_empty()
        && error.partial_tool_calls.is_empty()
        && matches!(
            error.error.code.as_str(),
            "provider_unavailable"
                | "rate_limited"
                | "provider_timeout"
                | "endpoint_unreachable"
                | "incomplete_stream"
        )
}

pub(crate) fn success(result: StreamSuccess, started: Instant, attempt: u8) -> ChatResult {
    let (text, reasoning, model, usage, finish_reason, tool_calls) = result;
    ChatResult {
        text,
        reasoning,
        tool_calls,
        model,
        usage,
        finish_reason,
        latency_ms: started.elapsed().as_millis() as u64,
        attempts: attempt,
    }
}

pub(crate) fn validate_policy(policy: &StreamPolicy) -> std::result::Result<(), ChatError> {
    if (1..=4).contains(&policy.max_attempts) {
        Ok(())
    } else {
        Err(ChatError::new(
            "invalid_retry_policy",
            "单上游重试次数须为 0 到 3",
        ))
    }
}

pub(crate) async fn stream_events<F>(
    url: reqwest::Url,
    kind: &str,
    key: Option<Zeroizing<String>>,
    bearer: bool,
    headers: &[(&str, &str)],
    body: Value,
    cancel: &Arc<AtomicBool>,
    parent_cancel: &Option<Arc<AtomicBool>>,
    policy: &StreamPolicy,
    model: String,
    mut on_event: F,
) -> std::result::Result<StreamSuccess, ChatError>
where
    F: FnMut(Value, &mut StreamOutput) -> std::result::Result<bool, ChatError>,
{
    let request = openai::streaming_client(&url, kind)
        .map_err(ChatError::from)?
        .post(url)
        .json(&body);
    let request = if bearer {
        openai::authorize_async(request, &key)
    } else {
        request
    };
    let request = headers.iter().fold(request, |request, (name, value)| {
        request.header(*name, *value)
    });
    let mut response = match tokio::time::timeout(policy.request_timeout, request.send()).await {
        Ok(Ok(response)) => response,
        Ok(Err(error)) if error.is_timeout() => {
            return Err(ChatError::new("provider_timeout", "模型服务响应超时"));
        }
        Ok(Err(_)) => {
            return Err(ChatError::new(
                "endpoint_unreachable",
                "无法连接模型服务端点",
            ));
        }
        Err(_) => return Err(ChatError::new("provider_timeout", "模型服务响应头等待超时")),
    };
    let status = response.status().as_u16();
    if status != 200 {
        return Err(ChatError::from(openai::http_error(status)));
    }
    let mut output = StreamOutput::new(model);
    let mut pending = Vec::new();
    let mut last_chunk = Instant::now();
    let mut done = false;
    let mut process = |line: &[u8]| -> std::result::Result<bool, ChatError> {
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
        let event: Value = serde_json::from_str(data)
            .map_err(|_| output.error("invalid_response", "流式事件不是有效 JSON"))?;
        on_event(event, &mut output)
    };
    loop {
        if openai::cancelled(cancel, parent_cancel) {
            return Err(output.error("cancelled", "模型请求已取消"));
        }
        let next = tokio::time::timeout(Duration::from_millis(100), response.chunk()).await;
        if openai::cancelled(cancel, parent_cancel) {
            return Err(output.error("cancelled", "模型请求已取消"));
        }
        match next {
            Ok(Ok(Some(chunk))) => {
                last_chunk = Instant::now();
                pending.extend_from_slice(&chunk);
                while let Some(end) = pending.iter().position(|byte| *byte == b'\n') {
                    if openai::cancelled(cancel, parent_cancel) {
                        return Err(output.error("cancelled", "模型请求已取消"));
                    }
                    let line = pending.drain(..=end).collect::<Vec<_>>();
                    done = process(&line)? || done;
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
                    done = process(&pending)? || done;
                }
                break;
            }
            Ok(Err(_)) => return Err(output.error("invalid_response", "流式响应读取失败")),
            Err(_) => {
                if last_chunk.elapsed() >= policy.chunk_timeout {
                    return Err(output.error("provider_timeout", "模型流式响应超时"));
                }
            }
        }
    }
    output.finish(done)
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
    on_delta: F,
    request_id: &str,
) -> std::result::Result<ChatResult, ChatError>
where
    F: FnMut(Delta) -> Result<()>,
{
    match profile.protocol.as_str() {
        "OpenAI Chat" => openai::stream_chat_with_policy(
            profile,
            key,
            messages,
            max_tokens,
            tools,
            cancel,
            parent_cancel,
            policy,
            on_delta,
        ),
        "OpenAI Responses" => responses::stream(
            profile,
            key,
            messages,
            max_tokens,
            tools,
            cancel,
            parent_cancel,
            policy,
            on_delta,
        ),
        "Anthropic Messages" => anthropic::stream(
            profile,
            key,
            messages,
            max_tokens,
            tools,
            cancel,
            parent_cancel,
            policy,
            on_delta,
        ),
        "Gemini Native" => gemini::stream(
            profile,
            key,
            messages,
            max_tokens,
            tools,
            cancel,
            parent_cancel,
            policy,
            on_delta,
            request_id,
        ),
        _ => Err(ChatError::new(
            "unsupported_protocol",
            "该协议尚未接入原生实现",
        )),
    }
}

pub fn chat_once(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
    messages: &[ChatMessage],
    max_tokens: u32,
) -> Result<ChatResult> {
    if profile.protocol == "OpenAI Chat" {
        return openai::chat_once(profile, key, messages, max_tokens);
    }
    stream_chat_with_policy(
        profile,
        key,
        messages,
        max_tokens,
        None,
        Arc::new(AtomicBool::new(false)),
        None,
        StreamPolicy::default(),
        |_| Ok(()),
        "provider-test",
    )
    .map_err(|error| error.error)
}

fn model_ids(body: &Value, collection: &str, field: &str, prefix: &str) -> Result<Vec<String>> {
    let models = body
        .get(collection)
        .and_then(Value::as_array)
        .ok_or_else(|| BackendError::new("invalid_response", "模型列表响应缺少模型数组"))?;
    let mut ids = models
        .iter()
        .filter_map(|model| model.get(field).and_then(Value::as_str))
        .filter(|id| !id.trim().is_empty())
        .map(|id| id.strip_prefix(prefix).unwrap_or(id).to_string())
        .collect::<Vec<_>>();
    ids.sort();
    ids.dedup();
    if ids.is_empty() {
        return Err(BackendError::new(
            "invalid_response",
            "模型服务没有返回可用模型",
        ));
    }
    Ok(ids)
}

pub fn list_models(
    profile: &ProviderProfile,
    key: Option<Zeroizing<String>>,
) -> Result<Vec<String>> {
    if profile.kind != "本地模型" && key.is_none() {
        return Err(BackendError::new(
            "credential_missing",
            "请先在系统密钥库中配置 API Key",
        ));
    }
    match profile.protocol.as_str() {
        "OpenAI Chat" | "OpenAI Responses" => openai::list_models(profile, key),
        "Anthropic Messages" => {
            let suffix = if profile.base_url.trim_end_matches('/').ends_with("/v1") {
                "models"
            } else {
                "v1/models"
            };
            let url = openai::endpoint(&profile.base_url, &profile.kind, suffix)?;
            let response = openai::client(&url, &profile.kind)?
                .get(url)
                .header(
                    "x-api-key",
                    key.as_ref().map(|key| key.as_str()).unwrap_or(""),
                )
                .header("anthropic-version", "2023-06-01")
                .send()
                .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接模型服务端点"))?;
            model_ids(&openai::read_json(response)?, "data", "id", "")
        }
        "Gemini Native" => {
            let url = openai::endpoint(&profile.base_url, &profile.kind, "models")?;
            let response = openai::client(&url, &profile.kind)?
                .get(url)
                .header(
                    "x-goog-api-key",
                    key.as_ref().map(|key| key.as_str()).unwrap_or(""),
                )
                .send()
                .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接模型服务端点"))?;
            model_ids(&openai::read_json(response)?, "models", "name", "models/")
        }
        _ => Err(BackendError::new(
            "unsupported_protocol",
            "该协议尚未接入原生实现",
        )),
    }
}

pub(crate) fn response_usage(body: &Value) -> Option<TokenUsage> {
    let usage = body.get("usage")?;
    if !usage.is_object() {
        return None;
    }
    Some(TokenUsage {
        input_tokens: usage.get("input_tokens").and_then(Value::as_u64),
        output_tokens: usage.get("output_tokens").and_then(Value::as_u64),
        cached_input_tokens: usage
            .get("input_tokens_details")
            .and_then(|details| details.get("cached_tokens"))
            .and_then(Value::as_u64),
    })
}

pub(crate) fn argument_object(arguments: &str) -> Result<Value> {
    let value = serde_json::from_str::<Value>(arguments)
        .map_err(|_| BackendError::invalid("invalid_tool_arguments", "工具参数不是有效 JSON"))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(BackendError::invalid(
            "invalid_tool_arguments",
            "工具参数必须是 JSON 对象",
        ))
    }
}
