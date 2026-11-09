use crate::backend::providers::openai::{
    self, ChatError, ChatResult, Delta, StreamOutput, StreamPolicy, TokenUsage,
};
use crate::backend::providers::protocol::{
    argument_object, backoff, emit, retryable, stream_events, success, validate_policy, ToolNames,
};
use crate::backend::providers::tools::{ToolCall, ToolDefinition};
use crate::backend::providers::{ChatMessage, ProviderProfile};
use serde_json::{json, Value};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Instant;
use zeroize::Zeroizing;

fn request_body(
    profile: &ProviderProfile,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
) -> crate::backend::error::Result<(Value, ToolNames)> {
    let names = ToolNames::new(tools);
    let mut system = Vec::new();
    let mut contents = Vec::new();
    for message in messages {
        match message.role.as_str() {
            "system" => system.push(message.content.clone()),
            "user" => contents.push(json!({"role": "user", "content": message.content})),
            "assistant" => {
                let mut content = Vec::new();
                if !message.content.is_empty() {
                    content.push(json!({"type": "text", "text": message.content}));
                }
                for call in message.tool_calls.as_deref().unwrap_or(&[]) {
                    content.push(json!({
                        "type": "tool_use",
                        "id": call.id,
                        "name": names.remote(&call.function.name),
                        "input": argument_object(&call.function.arguments)?,
                    }));
                }
                contents.push(json!({"role": "assistant", "content": content}));
            }
            "tool" => contents.push(json!({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }],
            })),
            _ => {}
        }
    }
    let mut body = json!({
        "model": profile.default_model,
        "messages": contents,
        "max_tokens": max_tokens,
        "stream": true,
    });
    if !system.is_empty() {
        body["system"] = json!(system.join("\n\n"));
    }
    if !names.definitions.is_empty() {
        body["tools"] = names
            .definitions
            .iter()
            .map(|(name, definition)| {
                json!({"name": name, "description": definition.description, "input_schema": definition.parameters})
            })
            .collect();
        body["tool_choice"] = json!({"type": "auto"});
    }
    Ok((body, names))
}

fn update_usage(output: &mut StreamOutput, value: &Value) {
    let previous = output.usage.take().unwrap_or(TokenUsage {
        input_tokens: None,
        output_tokens: None,
        cached_input_tokens: None,
    });
    output.usage = Some(TokenUsage {
        input_tokens: value
            .get("input_tokens")
            .and_then(Value::as_u64)
            .or(previous.input_tokens),
        output_tokens: value
            .get("output_tokens")
            .and_then(Value::as_u64)
            .or(previous.output_tokens),
        cached_input_tokens: value
            .get("cache_read_input_tokens")
            .and_then(Value::as_u64)
            .or(previous.cached_input_tokens),
    });
}

pub(super) fn stream<F>(
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
    F: FnMut(Delta) -> crate::backend::error::Result<()>,
{
    if profile.kind != "本地模型" && key.is_none() {
        return Err(ChatError::new(
            "credential_missing",
            "请先在系统密钥库中配置 API Key",
        ));
    }
    openai::validate_messages(messages, max_tokens).map_err(ChatError::from)?;
    validate_policy(&policy)?;
    let (body, names) =
        request_body(profile, messages, max_tokens, tools).map_err(ChatError::from)?;
    let suffix = if profile.base_url.trim_end_matches('/').ends_with("/v1") {
        "messages"
    } else {
        "v1/messages"
    };
    let url =
        openai::endpoint(&profile.base_url, &profile.kind, suffix).map_err(ChatError::from)?;
    for attempt in 1..=policy.max_attempts {
        let started = Instant::now();
        let mut done = false;
        let secret = key.as_ref().map(|key| key.as_str()).unwrap_or("");
        let result = tauri::async_runtime::block_on(stream_events(
            url.clone(),
            &profile.kind,
            key.clone(),
            false,
            &[("x-api-key", secret), ("anthropic-version", "2023-06-01")],
            body.clone(),
            &cancel,
            &parent_cancel,
            &policy,
            profile.default_model.clone(),
            |event, output| {
                let kind = event
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                match kind {
                    "message_start" => {
                        if let Some(message) = event.get("message") {
                            if let Some(model) = message.get("model").and_then(Value::as_str) {
                                output.model = model.into();
                            }
                            if let Some(usage) = message.get("usage") {
                                update_usage(output, usage);
                            }
                        }
                    }
                    "content_block_start" => {
                        let index = event
                            .get("index")
                            .and_then(Value::as_u64)
                            .ok_or_else(|| output.error("invalid_response", "内容块缺少索引"))?
                            as usize;
                        // 55#1 同根：distinct index 上限防稀疏 index 无限膨胀
                        if index >= openai::MAX_TOOL_CALL_ENTRIES {
                            return Err(output.error("invalid_response", "内容块索引超出上限"));
                        }
                        let block = event.get("content_block").cloned().unwrap_or(Value::Null);
                        match block.get("type").and_then(Value::as_str) {
                            Some("text") => {
                                if let Some(text) = block.get("text").and_then(Value::as_str) {
                                    emit(output, text, false, &mut on_delta)?;
                                }
                            }
                            Some("thinking") => {
                                if let Some(text) = block.get("thinking").and_then(Value::as_str) {
                                    emit(output, text, true, &mut on_delta)?;
                                }
                            }
                            Some("tool_use") => {
                                let id = block
                                    .get("id")
                                    .and_then(Value::as_str)
                                    .filter(|id| !id.is_empty())
                                    .ok_or_else(|| {
                                        output.error("invalid_tool_call", "工具调用缺少 ID")
                                    })?;
                                let name = block
                                    .get("name")
                                    .and_then(Value::as_str)
                                    .filter(|name| !name.is_empty())
                                    .ok_or_else(|| {
                                        output.error("invalid_tool_call", "工具调用缺少名称")
                                    })?;
                                let arguments = block
                                    .get("input")
                                    .filter(|input| {
                                        input.is_object()
                                            && !input
                                                .as_object()
                                                .is_some_and(|object| object.is_empty())
                                    })
                                    .map(|input| serde_json::to_string(input))
                                    .transpose()
                                    .map_err(|_| {
                                        output.error("invalid_tool_arguments", "工具参数序列化失败")
                                    })?
                                    .unwrap_or_default();
                                // 55#8：同 index 重发起始块（id 不同）直接覆盖会
                                // 静默丢弃已 emit 的 partial 参数——对齐 OpenAI
                                // 路径的同索引异 id fail-closed
                                if let Some(existing) = output.tool_calls.get(&index) {
                                    if existing.id != id {
                                        return Err(output.error(
                                            "invalid_tool_call",
                                            "同一索引出现了不同 ID 的工具调用",
                                        ));
                                    }
                                }
                                output.tool_calls.insert(
                                    index,
                                    ToolCall {
                                        id: id.into(),
                                        name: names.local(name),
                                        arguments,
                                    },
                                );
                            }
                            _ => {}
                        }
                    }
                    "content_block_delta" => {
                        let index =
                            event.get("index").and_then(Value::as_u64).ok_or_else(|| {
                                output.error("invalid_response", "内容增量缺少索引")
                            })? as usize;
                        let delta = event.get("delta").cloned().unwrap_or(Value::Null);
                        match delta.get("type").and_then(Value::as_str) {
                            Some("text_delta") => {
                                if let Some(text) = delta.get("text").and_then(Value::as_str) {
                                    emit(output, text, false, &mut on_delta)?;
                                }
                            }
                            Some("thinking_delta") => {
                                if let Some(text) = delta.get("thinking").and_then(Value::as_str) {
                                    emit(output, text, true, &mut on_delta)?;
                                }
                            }
                            Some("input_json_delta") => {
                                let Some(call) = output.tool_calls.get_mut(&index) else {
                                    return Err(
                                        output.error("invalid_tool_call", "工具参数缺少对应的调用")
                                    );
                                };
                                if let Some(arguments) =
                                    delta.get("partial_json").and_then(Value::as_str)
                                {
                                    // 25#17：对齐 openai.rs 的 256 KB 上限——自配的
                                    // 故障端点可用 endless delta 使内存无界增长
                                    if call.arguments.len().saturating_add(arguments.len())
                                        > 256 * 1024
                                    {
                                        return Err(output.error(
                                            "response_too_large",
                                            "单个工具参数超过 256 KB 上限",
                                        ));
                                    }
                                    call.arguments.push_str(arguments);
                                }
                            }
                            _ => {}
                        }
                    }
                    "message_delta" => {
                        if let Some(reason) =
                            event.pointer("/delta/stop_reason").and_then(Value::as_str)
                        {
                            output.finish_reason = Some(reason.into());
                        }
                        if let Some(usage) = event.get("usage") {
                            update_usage(output, usage);
                        }
                    }
                    "message_stop" => done = true,
                    "error" => {
                        let error = event.get("error").cloned().unwrap_or(Value::Null);
                        let message = error
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("供应商返回错误");
                        let code = if error.get("type").and_then(Value::as_str)
                            == Some("overloaded_error")
                        {
                            "provider_unavailable"
                        } else {
                            "provider_error"
                        };
                        // 66#4：上游 message 截断到 200 字符——对齐 openai/gemini
                        // 的防长技术细节/注入文案口径（原先整段直传）
                        return Err(
                            output.error(code, &message.chars().take(200).collect::<String>())
                        );
                    }
                    _ => {}
                }
                Ok(done)
            },
        ));
        match result {
            Ok(result) => return Ok(success(result, started, attempt)),
            Err(mut error) => {
                if retryable(&error, attempt, &policy) {
                    backoff(attempt);
                    continue;
                }
                error.attempts = attempt;
                return Err(error);
            }
        }
    }
    unreachable!()
}
