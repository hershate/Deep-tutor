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
/// P-018：退避期可被取消打断——返回 true 表示等待期间收到取消，调用方须
/// 放弃本次重试（否则会再向上游发一次用户已确认放弃的请求）
pub(crate) fn backoff(
    failed_attempt: u8,
    cancel: &Arc<AtomicBool>,
    parent_cancel: &Option<Arc<AtomicBool>>,
) -> bool {
    let delay = 400u64 << (failed_attempt.saturating_sub(1)).min(3);
    let deadline = Instant::now() + Duration::from_millis(delay);
    while Instant::now() < deadline {
        if openai::cancelled(cancel, parent_cancel) {
            return true;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        std::thread::sleep(Duration::from_millis(50).min(remaining));
    }
    openai::cancelled(cancel, parent_cancel)
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

/// P-018：发送请求与等待响应头期间保持取消可感知——原先一次性
/// `timeout(request_timeout, request.send())` 固定等满上限（45 秒），
/// 用户取消后仍要等到超时才生效，且超时后 attempt 循环会再向上游发一次
/// 请求（取消被吞）。改为按 100ms 切片等待，每片复核取消标志
pub(crate) async fn send_with_cancel(
    request: reqwest::RequestBuilder,
    timeout: Duration,
    cancel: &Arc<AtomicBool>,
    parent_cancel: &Option<Arc<AtomicBool>>,
) -> std::result::Result<reqwest::Response, ChatError> {
    let send = request.send();
    tokio::pin!(send);
    let deadline = Instant::now() + timeout;
    loop {
        if openai::cancelled(cancel, parent_cancel) {
            return Err(ChatError::new("cancelled", "模型请求已取消"));
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(ChatError::new("provider_timeout", "供应商响应头等待超时"));
        }
        match tokio::time::timeout(remaining.min(Duration::from_millis(100)), &mut send).await {
            Ok(Ok(response)) => return Ok(response),
            Ok(Err(error)) if error.is_timeout() => {
                return Err(ChatError::new("provider_timeout", "供应商响应超时"));
            }
            Ok(Err(_)) => {
                return Err(ChatError::new("endpoint_unreachable", "无法连接供应商端点"));
            }
            // 响应头仍在等待——复核取消标志与剩余时间后继续
            Err(_) => continue,
        }
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
    let mut response =
        match send_with_cancel(request, policy.request_timeout, cancel, parent_cancel).await {
            Ok(response) => response,
            Err(error) => return Err(error),
        };
    let status = response.status().as_u16();
    if status != 200 {
        // 109#1：读 body 前 4KB 提取诊断细节（55#4 同口径）——欠费 402/
        // 模型名错 400 等 body 说明原先整体丢弃，方案生成页只显示泛化
        // provider_error 而「检测→读取模型列表」却能看到真实原因
        let mut response = response;
        return Err(ChatError::from(
            openai::read_error_detail(&mut response, openai::http_error(status)).await,
        ));
    }
    let mut output = StreamOutput::new(model);
    let mut pending = Vec::new();
    let mut last_chunk = Instant::now();
    let mut done = false;
    // 125#1：闭包不再捕获 &mut output——空闲判定需要闭包外读
    // produced_bytes() 做产出对比（镜像 openai 私有泵口径），借用冲突由
    // 参数传递消解
    let mut process =
        |output: &mut StreamOutput, line: &[u8]| -> std::result::Result<bool, ChatError> {
            let line = std::str::from_utf8(line)
                .map_err(|_| output.error("invalid_response", "流式事件包含无效 UTF-8"))?
                .trim_end_matches(['\r', '\n']);
            // 55#7：剥流首 UTF-8 BOM，否则首行 data: 前缀匹配失败被静默跳过
            let line = line.strip_prefix('\u{FEFF}').unwrap_or(line);
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
            on_event(event, output)
        };
    // 55#1：无产出字节的累计预算（换行/注释洪泛封顶）与已消费行数（区分
    // 「零事件即断」的网络毛刺与流中途断开）——process 闭包独占 output
    // 借用，空闲判定在闭包外按「是否携带 data 载荷行」字节级启发式累计
    let mut idle_bytes: usize = 0;
    let mut processed_lines: usize = 0;
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
                // 125#1：空闲判定改产出对比（镜像 openai 私有泵口径）——原先
                // 「任何 data: 行」豁免空闲预算，故障/恶意端点持续发送零产出
                // data 行（如 data:{} 心跳）可无限滞留 worker，chunk 持续到达
                // 又不断刷新 chunk_timeout
                let produced_before = output.produced_bytes();
                pending.extend_from_slice(&chunk);
                // 55#1：游标偏移替代逐行头部 drain（O(N²) memmove 消除）；
                // 55#6：行终止按 SSE 规范接受 CRLF / LF / 裸 CR
                let mut cursor = 0usize;
                while let Some(offset) = pending[cursor..]
                    .iter()
                    .position(|byte| *byte == b'\n' || *byte == b'\r')
                {
                    let end = cursor + offset;
                    if openai::cancelled(cancel, parent_cancel) {
                        return Err(output.error("cancelled", "模型请求已取消"));
                    }
                    // 31#36：行长度上限（相对游标的行本身，含终止符）——
                    // >64KB 完整行一次读取即拒绝
                    if offset + 1 > MAX_SSE_LINE_BYTES {
                        return Err(output.error("response_too_large", "流式事件超过 64 KB 上限"));
                    }
                    let terminator =
                        if pending[end] == b'\r' && pending.get(end + 1) == Some(&b'\n') {
                            2
                        } else {
                            1
                        };
                    done = process(&mut output, &pending[cursor..end])? || done;
                    cursor = end + terminator;
                    processed_lines += 1;
                    if done {
                        break;
                    }
                }
                if done {
                    break;
                }
                pending.drain(..cursor);
                // 125#1：process 解析出有效 delta/usage/tool 才算产出——零产出
                // chunk（心跳/空 JSON/注释行）累计入 1MB 空闲预算
                if output.produced_bytes() == produced_before {
                    idle_bytes += chunk.len();
                    if idle_bytes > MAX_SSE_LINE_BYTES * 16 {
                        return Err(
                            output.error("invalid_response", "流式响应累计超过 1 MB 无有效事件")
                        );
                    }
                } else {
                    idle_bytes = 0;
                }
                if pending.len() > MAX_SSE_LINE_BYTES {
                    return Err(output.error("response_too_large", "流式事件超过 64 KB 上限"));
                }
            }
            Ok(Ok(None)) => {
                // 99#8：尾行先处理再按「是否收到过任何事件」分类——原先零行
                // 判定先于 process(&pending)，非标网关只发一条无终止符的最终
                // data 行后关连接时，该行滞留 pending、误报 endpoint_unreachable
                //（「未收到任何事件」），尾行被丢弃
                if !pending.is_empty() {
                    done = process(&mut output, &pending)? || done;
                    processed_lines += 1;
                }
                // 55#5：零事件的 EOF 同样是连接中断（干净 FIN 不走 Err 分支）——
                // 可重试/可故障转移；有事件但缺 [DONE] 仍为 incomplete_stream
                if processed_lines == 0 {
                    return Err(
                        output.error("endpoint_unreachable", "流式连接中断，未收到任何事件")
                    );
                }
                break;
            }
            Ok(Err(_)) => {
                // 55#5：零事件即断的网络毛刺归入可重试/可故障转移类
                if processed_lines == 0 {
                    return Err(
                        output.error("endpoint_unreachable", "流式连接中断，未收到任何事件")
                    );
                }
                return Err(output.error("invalid_response", "流式响应读取失败"));
            }
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
            "供应商没有返回可用模型",
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
            let secret = key.as_ref().map(|key| key.as_str()).unwrap_or("");
            // 125#4：空 key 不发送空值鉴权头
            let mut request = openai::client(&url, &profile.kind)?.get(url);
            if !secret.is_empty() {
                request = request.header("x-api-key", secret);
            }
            let response = request
                .header("anthropic-version", "2023-06-01")
                .send()
                .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接供应商端点"))?;
            model_ids(&openai::read_json(response)?, "data", "id", "")
        }
        "Gemini Native" => {
            let url = openai::endpoint(&profile.base_url, &profile.kind, "models")?;
            let secret = key.as_ref().map(|key| key.as_str()).unwrap_or("");
            let mut request = openai::client(&url, &profile.kind)?.get(url);
            if !secret.is_empty() {
                request = request.header("x-goog-api-key", secret);
            }
            let response = request
                .send()
                .map_err(|_| BackendError::new("endpoint_unreachable", "无法连接供应商端点"))?;
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
        .map_err(|_| BackendError::new("invalid_tool_arguments", "工具参数不是有效 JSON"))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(BackendError::new(
            "invalid_tool_arguments",
            "工具参数必须是 JSON 对象",
        ))
    }
}
