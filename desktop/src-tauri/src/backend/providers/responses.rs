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
        // 102#5：按 output item 粒度跟踪是否收到过 delta——多文本段
        // （文本与工具调用交错）场景下后续段只发 done 不发 delta 时，
        // 原先的整体 is_empty 判定被第一段挡住，该段全文静默丢失
        let mut text_delta_items = std::collections::HashSet::<u64>::new();
        let mut unindexed_deltas = false;
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
                            match event.get("output_index").and_then(Value::as_u64) {
                                Some(index) => {
                                    text_delta_items.insert(index);
                                }
                                None => unindexed_deltas = true,
                            }
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
                    // 102#5：判定下沉到 output item 粒度（无索引的退化代理
                    // 沿用整体空判 + 无索引 delta 视为已见，防全文重复拼接）
                    "response.output_text.done" => {
                        let seen_delta = match event.get("output_index").and_then(Value::as_u64) {
                            Some(index) => text_delta_items.contains(&index),
                            None => !output.text.is_empty(),
                        } || unindexed_deltas;
                        if !seen_delta {
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
                            // 153#6：个别代理只发 done（携带完整参数）不发
                            // added——未命中索引时以 done 携带的完整字段兜底
                            // 登记，原先静默丢弃且无诊断（对照 delta/done 路径
                            // 对缺失索引显式报错的口径，此处数据完整可自愈）
                            if !output.tool_calls.contains_key(&index)
                                && item.get("arguments").and_then(Value::as_str).is_some()
                            {
                                let call = ToolCall {
                                    id: item
                                        .get("call_id")
                                        .and_then(Value::as_str)
                                        .or_else(|| item.get("id").and_then(Value::as_str))
                                        .unwrap_or_default()
                                        .to_string(),
                                    // 153#6 兜底登记同样要回译本地名——执行层
                                    // 按本地名精确匹配（agent/tools.rs 的
                                    // workspace.read 等），远端消毒名直传
                                    // 会让该场景的工具调用全部落入
                                    // 「未注册」分支
                                    name: names.local(
                                        item.get("name")
                                            .and_then(Value::as_str)
                                            .unwrap_or_default(),
                                    ),
                                    arguments: String::new(),
                                };
                                eprintln!(
                                    "responses stream: output_item.done without added (index {index})——补登记 {}",
                                    call.name
                                );
                                output.tool_calls.insert(index, call);
                            }
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
                        // 153#5：流内错误细分 error.code——对齐 Gemini
                        // （RESOURCE_EXHAUSTED→rate_limited）与 Anthropic
                        // （overloaded_error→provider_unavailable）的口径；
                        // 原先一律 provider_error，本应重试/转移的瞬态故障
                        // （rate_limit_exceeded/server_error/timeout）被判终态
                        let upstream_code = event
                            .get("code")
                            .and_then(Value::as_str)
                            .or_else(|| {
                                event
                                    .pointer("/response/error/code")
                                    .and_then(Value::as_str)
                            })
                            .or_else(|| event.pointer("/error/code").and_then(Value::as_str))
                            .unwrap_or_default();
                        let code = match upstream_code {
                            "rate_limit_exceeded" => "rate_limited",
                            "server_error" | "internal_error" | "overloaded_error" => {
                                "provider_unavailable"
                            }
                            "timeout" | "request_timeout" => "provider_timeout",
                            _ => "provider_error",
                        };
                        // 66#4：上游 message 截断到 200 字符——对齐 openai/gemini
                        // 的防长技术细节/注入文案口径（原先整段直传）
                        return Err(
                            output.error(code, &message.chars().take(200).collect::<String>())
                        );
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
                    // P-018：退避期间收到取消即放弃本次重试——否则会再向上游
                    // 发一次用户已确认放弃的请求
                    if backoff(attempt, &cancel, &parent_cancel) {
                        return Err(ChatError::new("cancelled", "模型请求已取消"));
                    }
                    continue;
                }
                error.attempts = attempt;
                return Err(error);
            }
        }
    }
    unreachable!()
}
