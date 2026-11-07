use crate::backend::providers::openai::{
    self, ChatError, ChatResult, Delta, StreamOutput, StreamPolicy, TokenUsage,
};
use crate::backend::providers::protocol::{
    argument_object, emit, gemini_schema, retryable, stream_events, success, validate_policy,
    ToolNames,
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
    output.usage = Some(TokenUsage {
        input_tokens: usage.get("promptTokenCount").and_then(Value::as_u64),
        output_tokens: candidates
            .zip(thoughts)
            .map(|(candidates, thoughts)| candidates.saturating_add(thoughts))
            .or(candidates)
            .or(thoughts),
        cached_input_tokens: usage.get("cachedContentTokenCount").and_then(Value::as_u64),
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
                        output.error("content_blocked", &format!("模型服务阻止了请求：{reason}"))
                    );
                }
                let candidates = event
                    .get("candidates")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                for candidate in candidates {
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
                                output.error("content_blocked", "模型服务因安全策略停止生成")
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
                    continue;
                }
                error.attempts = attempt;
                return Err(error);
            }
        }
    }
    unreachable!()
}
