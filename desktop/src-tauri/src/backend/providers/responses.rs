use crate::backend::providers::openai::{self, ChatError, ChatResult, Delta, StreamPolicy};
use crate::backend::providers::protocol::{
    backoff, emit, response_usage, retryable, stream_events, success, validate_policy, ToolNames,
};
use crate::backend::providers::tools::{ToolCall, ToolDefinition};
use crate::backend::providers::{ChatMessage, ProviderProfile};
use serde_json::{json, Value};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Instant;
use zeroize::Zeroizing;

/// Responses API 的 incomplete_details.reason（null/缺省视为完整）
fn incomplete_reason(response: &Value) -> Option<String> {
    response
        .get("incomplete_details")
        .filter(|details| !details.is_null())?
        .get("reason")
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn request_body(
    profile: &ProviderProfile,
    messages: &[ChatMessage],
    max_tokens: u32,
    tools: Option<&[ToolDefinition]>,
) -> crate::backend::error::Result<(Value, ToolNames)> {
    let names = ToolNames::new(tools);
    let mut instructions = Vec::new();
    let mut input = Vec::new();
    for message in messages {
        match message.role.as_str() {
            "system" => instructions.push(message.content.clone()),
            "user" => input.push(json!({
                "role": "user",
                "content": [{"type": "input_text", "text": message.content}],
            })),
            "assistant" => {
                if !message.content.is_empty() {
                    input.push(json!({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": message.content}],
                    }));
                }
                for call in message.tool_calls.as_deref().unwrap_or(&[]) {
                    let name = names.remote(&call.function.name);
                    input.push(json!({
                        "type": "function_call",
                        "call_id": call.id,
                        "name": name,
                        "arguments": call.function.arguments,
                    }));
                }
            }
            "tool" => input.push(json!({
                "type": "function_call_output",
                "call_id": message.tool_call_id,
                "output": message.content,
            })),
            _ => {}
        }
    }
    let mut body = json!({
        "model": profile.default_model,
        "input": input,
        "max_output_tokens": max_tokens,
        "stream": true,
    });
    if !instructions.is_empty() {
        body["instructions"] = json!(instructions.join("\n\n"));
    }
    if !names.definitions.is_empty() {
        body["tools"] = names
            .definitions
            .iter()
            .map(|(name, definition)| {
                json!({"type": "function", "name": name, "description": definition.description, "parameters": definition.parameters})
            })
            .collect();
        body["tool_choice"] = json!("auto");
    }
    Ok((body, names))
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
    let url =
        openai::endpoint(&profile.base_url, &profile.kind, "responses").map_err(ChatError::from)?;
    for attempt in 1..=policy.max_attempts {
        let started = Instant::now();
        let mut done = false;
        // 99#9：拒答标记——refusal delta 与正文混排原先无任何区分
        let mut saw_refusal = false;
        let result = tauri::async_runtime::block_on(stream_events(
            url.clone(),
            &profile.kind,
            key.clone(),
            true,
            &[],
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
                    "response.output_text.delta" => {
                        if let Some(text) = event.get("delta").and_then(Value::as_str) {
                            emit(output, text, false, &mut on_delta)?;
                        }
                    }
                    // 99#9：拒答内容单独标记——与正文混排原先无区分，终态的
                    // finish_reason 记 refusal（展示层可辨，内容保留不失败）
                    "response.refusal.delta" => {
                        saw_refusal = true;
                        if let Some(text) = event.get("delta").and_then(Value::as_str) {
                            emit(output, text, false, &mut on_delta)?;
                        }
                    }
                    // 99#9：个别代理只发 output_text.done（携带全文）不发
                    // delta——原先被 `_ => {}` 丢弃、text 为空、finish 报泛化
                    // empty_completion，真实全文丢失；空文本时兜底接收
                    "response.output_text.done" => {
                        if output.text.is_empty() {
                            if let Some(text) = event.get("text").and_then(Value::as_str) {
                                emit(output, text, false, &mut on_delta)?;
                            }
                        }
                    }
                    "response.reasoning_summary_text.delta" | "response.reasoning_text.delta" => {
                        if let Some(text) = event.get("delta").and_then(Value::as_str) {
                            emit(output, text, true, &mut on_delta)?;
                        }
                    }
                    "response.output_item.added" => {
                        let item = event.get("item").cloned().unwrap_or(Value::Null);
                        if item.get("type").and_then(Value::as_str) == Some("function_call") {
                            let index = event
                                .get("output_index")
                                .and_then(Value::as_u64)
                                .ok_or_else(|| {
                                    output.error("invalid_tool_call", "工具调用缺少 output_index")
                                })? as usize;
                            // 55#1 同根：distinct index 上限防稀疏 index 无限膨胀
                            if index >= openai::MAX_TOOL_CALL_ENTRIES {
                                return Err(
                                    output.error("invalid_tool_call", "工具调用索引超出上限")
                                );
                            }
                            let id = item
                                .get("call_id")
                                .and_then(Value::as_str)
                                .or_else(|| item.get("id").and_then(Value::as_str))
                                .filter(|id| !id.is_empty())
                                .ok_or_else(|| {
                                    output.error("invalid_tool_call", "工具调用缺少 call_id")
                                })?;
                            let name = item
                                .get("name")
                                .and_then(Value::as_str)
                                .filter(|name| !name.is_empty())
                                .ok_or_else(|| {
                                    output.error("invalid_tool_call", "工具调用缺少函数名")
                                })?;
                            // 55#8：同 index 重发条目（id 不同）直接覆盖会静默丢弃
                            // 已 emit 的 partial 参数——对齐 OpenAI 的 fail-closed
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
                                    arguments: item
                                        .get("arguments")
                                        .and_then(Value::as_str)
                                        .unwrap_or_default()
                                        .into(),
                                },
                            );
                        }
                    }
                    "response.function_call_arguments.delta" => {
                        let index = event
                            .get("output_index")
                            .and_then(Value::as_u64)
                            .ok_or_else(|| {
                                output.error("invalid_tool_call", "工具参数缺少 output_index")
                            })? as usize;
                        let Some(call) = output.tool_calls.get_mut(&index) else {
                            return Err(output.error("invalid_tool_call", "工具参数缺少对应的调用"));
                        };
                        if let Some(delta) = event.get("delta").and_then(Value::as_str) {
                            // 25#17：对齐 openai.rs 的 256 KB 上限——自配的故障
                            // 端点可用 endless delta 使内存无界增长
                            if call.arguments.len().saturating_add(delta.len()) > 256 * 1024 {
                                return Err(output
                                    .error("response_too_large", "单个工具参数超过 256 KB 上限"));
                            }
                            call.arguments.push_str(delta);
                        }
                    }
                    "response.function_call_arguments.done" => {
                        let index = event
                            .get("output_index")
                            .and_then(Value::as_u64)
                            .ok_or_else(|| {
                                output.error("invalid_tool_call", "工具参数缺少 output_index")
                            })? as usize;
                        if let Some(arguments) = event.get("arguments").and_then(Value::as_str) {
                            let Some(call) = output.tool_calls.get_mut(&index) else {
                                return Err(
                                    output.error("invalid_tool_call", "工具参数缺少对应的调用")
                                );
                            };
                            if !arguments.is_empty() {
                                // 25#17：done 事件整段替换同样受 256 KB 上限约束
                                if arguments.len() > 256 * 1024 {
                                    return Err(output.error(
                                        "response_too_large",
                                        "单个工具参数超过 256 KB 上限",
                                    ));
                                }
                                call.arguments = arguments.into();
                            }
                        }
                    }
                    "response.output_item.done" => {
                        let item = event.get("item").cloned().unwrap_or(Value::Null);
                        if item.get("type").and_then(Value::as_str) == Some("function_call") {
                            let index = event
                                .get("output_index")
                                .and_then(Value::as_u64)
                                .ok_or_else(|| {
                                    output.error("invalid_tool_call", "工具调用缺少 output_index")
                                })? as usize;
                            if let Some(call) = output.tool_calls.get_mut(&index) {
                                if let Some(arguments) =
                                    item.get("arguments").and_then(Value::as_str)
                                {
                                    if !arguments.is_empty() {
                                        // 31#37：与 delta/done 路径同一 25#17 上限
                                        // ——output_item.done 整段替换原先无检查
                                        if arguments.len() > 256 * 1024 {
                                            return Err(output.error(
                                                "response_too_large",
                                                "单个工具参数超过 256 KB 上限",
                                            ));
                                        }
                                        call.arguments = arguments.into();
                                    }
                                }
                            }
                        }
                    }
                    "response.completed" => {
                        let response = event.get("response").cloned().unwrap_or(Value::Null);
                        if let Some(reason) = incomplete_reason(&response) {
                            if reason == "max_output_tokens" {
                                // 输出触及上限：与 OpenAI Chat 的 finish_reason=length
                                // 同语义（10#4）——保留已生成内容正常完成，不整步失败
                                output.finish_reason = Some("length".into());
                            } else {
                                return Err(output.error("incomplete_stream", "模型响应未完整完成"));
                            }
                        }
                        output.usage = response_usage(&response);
                        if let Some(model) = response.get("model").and_then(Value::as_str) {
                            output.model = model.into();
                        }
                        // 99#9：拒答标记优先于普通完成
                        if saw_refusal && output.finish_reason.is_none() {
                            output.finish_reason = Some("refusal".into());
                        }
                        done = true;
                    }
                    "response.incomplete" => {
                        let response = event.get("response").cloned().unwrap_or(Value::Null);
                        output.usage = response_usage(&response);
                        if incomplete_reason(&response).as_deref() == Some("max_output_tokens") {
                            // 触及输出上限但已有内容：按截断完成处理（10#4）
                            output.finish_reason = Some("length".into());
                            done = true;
                        } else {
                            return Err(output.error("incomplete_stream", "模型响应未完整完成"));
                        }
                    }
                    "response.failed" | "error" => {
                        let message = event
                            .get("message")
                            .and_then(Value::as_str)
                            .or_else(|| {
                                event
                                    .pointer("/response/error/message")
                                    .and_then(Value::as_str)
                            })
                            .or_else(|| event.pointer("/error/message").and_then(Value::as_str))
                            .unwrap_or("模型响应失败");
                        // 66#4：上游 message 截断到 200 字符——对齐 openai/gemini
                        // 的防长技术细节/注入文案口径（原先整段直传）
                        return Err(output.error(
                            "provider_error",
                            &message.chars().take(200).collect::<String>(),
                        ));
                    }
                    _ => {
                        // 99#9：未覆盖事件不再完全静默——stderr 留痕供代理网关排障
                        if !kind.is_empty() {
                            eprintln!("responses stream: unhandled event kind {kind}");
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
