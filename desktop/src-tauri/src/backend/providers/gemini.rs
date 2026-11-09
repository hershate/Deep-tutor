use crate::backend::providers::openai::{
    self, ChatError, ChatResult, Delta, StreamOutput, StreamPolicy, TokenUsage,
};
use crate::backend::providers::protocol::{
    argument_object, backoff, emit, gemini_schema, retryable, stream_events, success,
    validate_policy, ToolNames,
};
use crate::backend::providers::tools::{ToolCall, ToolDefinition};
use crate::backend::providers::{ChatMessage, ProviderProfile};
use serde_json::{json, Value};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Instant;
use zeroize::Zeroizing;

fn function_response(name: &str, content: &str) -> Value {
    let content = serde_json::from_str::<Value>(content)
        .map(|value| {
            if value.is_object() {
                value
            } else {
                json!({"result": value})
            }
        })
        .unwrap_or_else(|_| json!({"result": content}));
    json!({"name": name, "content": content})
}

fn request_body(
    _profile: &ProviderProfile,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
) -> crate::backend::error::Result<(Value, ToolNames)> {
    let names = ToolNames::new(tools);
    let mut instructions = Vec::new();
    let mut contents = Vec::new();
    for message in messages {
        match message.role.as_str() {
            "system" => instructions.push(message.content.clone()),
            "user" => contents.push(json!({"role": "user", "parts": [{"text": message.content}]})),
            "assistant" => {
                let mut parts = Vec::new();
                if !message.content.is_empty() {
                    parts.push(json!({"text": message.content}));
                }
                for call in message.tool_calls.as_deref().unwrap_or(&[]) {
                    parts.push(json!({"functionCall": {
                        "name": names.remote(&call.function.name),
                        "args": argument_object(&call.function.arguments)?,
                    }}));
                }
                contents.push(json!({"role": "model", "parts": parts}));
            }
            "tool" => {
                let name = message.name.as_deref().unwrap_or_default();
                let remote = names.remote(name);
                contents.push(json!({
                    "role": "function",
                    "parts": [{"functionResponse": {"name": remote, "response": function_response(&remote, &message.content)}}],
                }));
            }
            _ => {}
        }
    }
    let mut body = json!({
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    });
    if !instructions.is_empty() {
        body["system_instruction"] = json!({"parts": [{"text": instructions.join("\n\n")}]});
    }
    if !names.definitions.is_empty() {
        body["tools"] = json!([{
            "functionDeclarations": names.definitions.iter().map(|(name, definition)| {
                json!({"name": name, "description": definition.description, "parameters": gemini_schema(&definition.parameters)})
            }).collect::<Vec<_>>(),
        }]);
    }
    Ok((body, names))
}

fn update_usage(output: &mut StreamOutput, usage: &Value) {
    let candidates = usage.get("candidatesTokenCount").and_then(Value::as_u64);
    let thoughts = usage.get("thoughtsTokenCount").and_then(Value::as_u64);
    // 31#39：空字段不覆盖已记录值（与 anthropic.rs 同口径）——个别网关中间
    // 事件携带空 usageMetadata 时，整体覆盖会丢掉已累计的计数
    let previous = output.usage.take().unwrap_or(TokenUsage {
        input_tokens: None,
        output_tokens: None,
        cached_input_tokens: None,
    });
    // 34#17：完整新值 > 旧完整值 > 局部新值——后续事件只带 thoughts 缺
    // candidates 时（个别网关），不得以局部值回退已累计的完整 output_tokens
    let complete_output = candidates
        .zip(thoughts)
        .map(|(candidates, thoughts)| candidates.saturating_add(thoughts));
    let partial_output = candidates.or(thoughts);
    output.usage = Some(TokenUsage {
        input_tokens: usage
            .get("promptTokenCount")
            .and_then(Value::as_u64)
            .or(previous.input_tokens),
        output_tokens: complete_output
            .or(previous.output_tokens)
            .or(partial_output),
        cached_input_tokens: usage
            .get("cachedContentTokenCount")
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
    request_id: &str,
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
    let suffix = format!("models/{}:streamGenerateContent", profile.default_model);
    let mut url =
        openai::endpoint(&profile.base_url, &profile.kind, &suffix).map_err(ChatError::from)?;
    url.query_pairs_mut().append_pair("alt", "sse");
    for attempt in 1..=policy.max_attempts {
        let started = Instant::now();
        let mut done = false;
        let mut tool_index = 0usize;
        let secret = key.as_ref().map(|key| key.as_str()).unwrap_or("");
        let result = tauri::async_runtime::block_on(stream_events(
            url.clone(),
            &profile.kind,
            key.clone(),
            false,
            &[("x-goog-api-key", secret)],
            body.clone(),
            &cancel,
            &parent_cancel,
            &policy,
            profile.default_model.clone(),
            |event, output| {
                // Gemini 以流内 error 事件返回真实错误（配额/安全等，10#5）：
                // 不处理会退化为泛化 incomplete_stream，丢失诊断信息
                if let Some(error) = event.get("error").filter(|error| !error.is_null()) {
                    let status = error.get("status").and_then(Value::as_str).unwrap_or("");
                    let message = error
                        .get("message")
                        .and_then(Value::as_str)
                        .unwrap_or("供应商在流内返回错误");
                    let code = if status.contains("RESOURCE_EXHAUSTED") {
                        "rate_limited"
                    } else {
                        "provider_error"
                    };
                    // 29#3：上游 message 原文可能携带长技术细节/注入文本，
                    // 用户可见文案截断到 200 字符（状态码本身有界）
                    return Err(output.error(
                        code,
                        &format!(
                            "{status}：{}",
                            message.chars().take(200).collect::<String>()
                        ),
                    ));
                }
                if let Some(usage) = event.get("usageMetadata") {
                    update_usage(output, usage);
                }
                if let Some(model) = event.get("modelVersion").and_then(Value::as_str) {
                    output.model = model.into();
                }
                if let Some(reason) = event
                    .pointer("/promptFeedback/blockReason")
                    .and_then(Value::as_str)
                {
                    return Err(
                        output.error("content_blocked", &format!("供应商阻止了请求：{reason}"))
                    );
                }
                let candidates = event
                    .get("candidates")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                // 55#9：candidateCount>1 或网关回显多候选时全量 emit 会使输出
                // 成倍重复——与 OpenAI 路径（choices[0]）同口径只取首个候选
                for candidate in candidates.into_iter().take(1) {
                    if let Some(parts) = candidate
                        .pointer("/content/parts")
                        .and_then(Value::as_array)
                    {
                        for part in parts {
                            if let Some(text) = part.get("text").and_then(Value::as_str) {
                                let reasoning = part
                                    .get("thought")
                                    .and_then(Value::as_bool)
                                    .unwrap_or(false);
                                emit(output, text, reasoning, &mut on_delta)?;
                            }
                            if let Some(call) = part.get("functionCall") {
                                let remote = call
                                    .get("name")
                                    .and_then(Value::as_str)
                                    .filter(|name| !name.is_empty())
                                    .ok_or_else(|| {
                                        output.error("invalid_tool_call", "工具调用缺少函数名")
                                    })?;
                                let args = call.get("args").cloned().unwrap_or_else(|| json!({}));
                                if !args.is_object() {
                                    return Err(output.error(
                                        "invalid_tool_arguments",
                                        "工具参数必须是 JSON 对象",
                                    ));
                                }
                                let id = call
                                    .get("id")
                                    .and_then(Value::as_str)
                                    .filter(|id| !id.is_empty())
                                    .map(str::to_string)
                                    .unwrap_or_else(|| format!("gemini-{request_id}-{tool_index}"));
                                output.tool_calls.insert(
                                    tool_index,
                                    ToolCall {
                                        id,
                                        name: names.local(remote),
                                        arguments: serde_json::to_string(&args).map_err(|_| {
                                            output.error(
                                                "invalid_tool_arguments",
                                                "工具参数序列化失败",
                                            )
                                        })?,
                                    },
                                );
                                tool_index += 1;
                            }
                        }
                    }
                    if let Some(reason) = candidate.get("finishReason").and_then(Value::as_str) {
                        output.finish_reason = Some(reason.into());
                        done = true;
                        if reason == "SAFETY" {
                            return Err(
                                output.error("content_blocked", "供应商因安全策略停止生成")
                            );
                        }
                    }
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
