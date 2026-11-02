use super::openai::{ChatError, ChatResult};
use super::test_support::{response, server, start_stream};
use super::*;
use crate::backend::BackendState;
use std::time::Duration;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, ProviderState) {
    let state = ProviderState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: crate::backend::materials::MaterialState::for_tests().unwrap(),
            providers: state.shared(),
            learning: crate::backend::learning::LearningState::for_tests().unwrap(),
            agent: crate::backend::agent::AgentState::for_tests().unwrap(),
            review: crate::backend::review::ReviewState::for_tests().unwrap(),
        })
        .invoke_handler(tauri::generate_handler![
            crate::backend::providers::commands::upsert_provider_profile,
            crate::backend::providers::commands::set_active_provider,
            crate::backend::providers::commands::test_provider,
            crate::backend::providers::commands::stream_provider_chat,
            crate::backend::providers::commands::cancel_provider_chat,
            crate::backend::providers::commands::get_provider_chat,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
}

fn ipc(
    window: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    command: &str,
    arguments: Value,
) -> std::result::Result<Value, Value> {
    tauri::test::get_ipc_response(
        window,
        tauri::webview::InvokeRequest {
            cmd: command.into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(arguments),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .map(|body| body.deserialize::<Value>().unwrap())
}

fn make_profile(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &ProviderState,
    protocol: &str,
    url: &str,
) -> ProviderProfile {
    upsert(
        app.handle(),
        state,
        ProfileInput {
            id: None,
            name: protocol.into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: protocol.into(),
            base_url: url.into(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("protocol-test-token".into()),
        },
    )
    .unwrap()
}

fn tool_definitions() -> Vec<tools::ToolDefinition> {
    vec![tools::ToolDefinition {
        kind: "function".into(),
        function: tools::ToolDefinitionFunction {
            name: "workspace.read".into(),
            description: "读取当前工作区".into(),
            parameters: json!({"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}),
        },
    }]
}

fn history() -> Vec<ChatMessage> {
    vec![
        ChatMessage {
            role: "system".into(),
            content: "依据真实工具回答".into(),
            ..Default::default()
        },
        ChatMessage {
            role: "user".into(),
            content: "读取当前工作区".into(),
            ..Default::default()
        },
        ChatMessage {
            role: "assistant".into(),
            content: String::new(),
            tool_calls: Some(vec![tools::ToolCallRequest {
                id: "call-1".into(),
                kind: "function".into(),
                function: tools::ToolCallFunction {
                    name: "workspace.read".into(),
                    arguments: "{\"reference\":\"current\"}".into(),
                },
            }]),
            ..Default::default()
        },
        ChatMessage {
            role: "tool".into(),
            content: "{\"status\":\"done\"}".into(),
            tool_call_id: Some("call-1".into()),
            name: Some("workspace.read".into()),
            tool_calls: None,
        },
    ]
}

fn data(event: Value) -> String {
    format!("data: {event}\n\n")
}

fn messages() -> Vec<ChatMessage> {
    vec![ChatMessage {
        role: "user".into(),
        content: "hello".into(),
        ..Default::default()
    }]
}

fn chat_with_tools(profile: &ProviderProfile) -> std::result::Result<ChatResult, ChatError> {
    protocol::stream_chat_with_policy(
        profile,
        Some(Zeroizing::new("protocol-test-token".into())),
        &history(),
        64,
        Some(&tool_definitions()),
        Arc::new(std::sync::atomic::AtomicBool::new(false)),
        None,
        openai::StreamPolicy::default(),
        |_| Ok(()),
        "provider-test",
    )
}

#[test]
fn responses_streams_tools_usage_and_cancels_real_transport() {
    let (app, state) = setup();
    let endpoint = server(|request, stream| {
        if request.starts_with("GET /models ") {
            return response(stream, "200 OK", "{\"data\":[{\"id\":\"response-model\"}]}");
        }
        assert!(request.starts_with("POST /responses "));
        assert!(request.contains("\"input_text\""));
        assert!(request.contains("\"function_call\""));
        assert!(request.contains("\"function_call_output\""));
        assert!(request.contains("\"instructions\""));
        let body = format!(
            "{}{}{}{}{}",
            data(
                json!({"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "call_id": "call-responses", "name": "workspace_read", "arguments": ""}})
            ),
            data(
                json!({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "{\"reference\":"})
            ),
            data(
                json!({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "\"current\"}"})
            ),
            data(
                json!({"type": "response.function_call_arguments.done", "output_index": 0, "arguments": "{\"reference\":\"current\"}"})
            ),
            data(
                json!({"type": "response.completed", "response": {"model": "test-model", "usage": {"input_tokens": 9, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 2}}}})
            ),
        );
        response(stream, "200 OK", &body);
    });
    let profile = make_profile(&app, &state, "OpenAI Responses", &endpoint.url());
    assert_eq!(
        protocol::list_models(&profile, None).unwrap(),
        vec!["response-model"]
    );
    let result = chat_with_tools(&profile).unwrap();
    assert_eq!(result.tool_calls.len(), 1);
    assert_eq!(result.tool_calls[0].id, "call-responses");
    assert_eq!(result.tool_calls[0].name, "workspace.read");
    assert_eq!(
        result.tool_calls[0].arguments,
        "{\"reference\":\"current\"}"
    );
    assert_eq!(result.usage.as_ref().unwrap().input_tokens, Some(9));
    assert_eq!(result.usage.as_ref().unwrap().output_tokens, Some(7));
    assert_eq!(result.usage.as_ref().unwrap().cached_input_tokens, Some(2));
    assert!(result.text.is_empty());

    let release = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let server_release = Arc::clone(&release);
    let waiting = server(move |_request, stream| {
        start_stream(stream);
        std::io::Write::write_all(
            stream,
            "data: {\"type\":\"response.output_text.delta\",\"delta\":\"部分输出\"}\n\n".as_bytes(),
        )
        .unwrap();
        std::io::Write::flush(stream).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !server_release.load(std::sync::atomic::Ordering::Acquire) {
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(5));
        }
        let _ = std::io::Write::write_all(stream, b"data: [DONE]\n\n");
    });
    let profile = make_profile(&app, &state, "OpenAI Responses", &waiting.url());
    let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let worker_cancel = Arc::clone(&cancel);
    let worker_profile = profile.clone();
    let (sender, receiver) = std::sync::mpsc::channel();
    let worker = std::thread::spawn(move || {
        protocol::stream_chat_with_policy(
            &worker_profile,
            None,
            &messages(),
            64,
            None,
            worker_cancel,
            None,
            openai::StreamPolicy::default(),
            move |delta| {
                let _ = sender.send(delta.text);
                Ok(())
            },
            "responses-cancel",
        )
    });
    assert_eq!(
        receiver.recv_timeout(Duration::from_secs(5)).unwrap(),
        "部分输出"
    );
    cancel.store(true, std::sync::atomic::Ordering::Release);
    release.store(true, std::sync::atomic::Ordering::Release);
    let error = worker.join().unwrap().unwrap_err();
    assert_eq!(error.error.code, "cancelled");
    assert_eq!(error.partial_text, "部分输出");
}

#[test]
fn tool_calls_do_not_retry_an_incomplete_response() {
    let (app, state) = setup();
    let endpoint = server(|_request, stream| {
        let body = [
            data(json!({"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "call_id": "call-partial", "name": "workspace_read", "arguments": ""}})),
            data(json!({"type": "response.function_call_arguments.done", "output_index": 0, "arguments": "{\"reference\":\"current\"}"})),
        ]
        .join("");
        response(stream, "200 OK", &body);
    });
    let profile = make_profile(&app, &state, "OpenAI Responses", &endpoint.url());
    let error = protocol::stream_chat_with_policy(
        &profile,
        Some(Zeroizing::new("protocol-test-token".into())),
        &history(),
        64,
        Some(&tool_definitions()),
        Arc::new(std::sync::atomic::AtomicBool::new(false)),
        None,
        openai::StreamPolicy::default(),
        |_| Ok(()),
        "partial-tool-call",
    )
    .unwrap_err();
    assert_eq!(error.error.code, "incomplete_stream");
    assert_eq!(error.partial_tool_calls.len(), 1);
    assert_eq!(endpoint.request_count(), 1);
}

#[test]
fn anthropic_streams_reasoning_tools_and_usage() {
    let (app, state) = setup();
    let endpoint = server(|request, stream| {
        if request.starts_with("GET /v1/models ") {
            return response(stream, "200 OK", "{\"data\":[{\"id\":\"claude-test\"}]}");
        }
        assert!(request.starts_with("POST /v1/messages "));
        assert!(request.contains("x-api-key: protocol-test-token"));
        assert!(request.contains("anthropic-version: 2023-06-01"));
        assert!(request.contains("\"input_schema\""));
        assert!(request.contains("\"system\""));
        let body = [
            data(
                json!({"type": "message_start", "message": {"model": "test-model", "usage": {"input_tokens": 20, "output_tokens": 1}}})
            ),
            data(
                json!({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
            ),
            data(
                json!({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先思考"}})
            ),
            data(json!({"type": "content_block_stop", "index": 0})),
            data(
                json!({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu-1", "name": "workspace_read", "input": {}}})
            ),
            data(
                json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"reference\":"}})
            ),
            data(
                json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"current\"}"}})
            ),
            data(
                json!({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}})
            ),
            data(json!({"type": "message_stop"})),
        ]
        .join("");
        response(stream, "200 OK", &body);
    });
    let profile = make_profile(&app, &state, "Anthropic Messages", &endpoint.url());
    assert_eq!(
        protocol::list_models(&profile, None).unwrap(),
        vec!["claude-test"]
    );
    let result = chat_with_tools(&profile).unwrap();
    assert_eq!(result.reasoning, "先思考");
    assert_eq!(result.tool_calls[0].name, "workspace.read");
    assert_eq!(result.tool_calls[0].id, "toolu-1");
    assert_eq!(
        result.tool_calls[0].arguments,
        "{\"reference\":\"current\"}"
    );
    assert_eq!(result.usage.as_ref().unwrap().input_tokens, Some(20));
    assert_eq!(result.usage.as_ref().unwrap().output_tokens, Some(8));
    assert_eq!(result.finish_reason.as_deref(), Some("tool_use"));
}

#[test]
fn gemini_streams_thought_tools_and_usage() {
    let (app, state) = setup();
    let endpoint = server(|request, stream| {
        if request.starts_with("GET /v1beta/models ") {
            return response(
                stream,
                "200 OK",
                "{\"models\":[{\"name\":\"models/gemini-test\"}]}",
            );
        }
        assert!(request.starts_with("POST /v1beta/models/test-model:streamGenerateContent?alt=sse"));
        assert!(request.contains("\"system_instruction\""));
        assert!(request.contains("\"generationConfig\""));
        assert!(request.contains("\"maxOutputTokens\":64"));
        assert!(request.contains("\"functionDeclarations\""));
        assert!(request.contains("\"type\":\"OBJECT\""));
        assert!(request.contains("\"functionResponse\""));
        let body = data(json!({
            "modelVersion": "test-model",
            "candidates": [{"content": {"parts": [
                {"text": "先想", "thought": true},
                {"functionCall": {"name": "workspace_read", "args": {"reference": "current"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 4, "thoughtsTokenCount": 3, "cachedContentTokenCount": 1},
        }));
        response(stream, "200 OK", &body);
    });
    let base_url = format!("{}/v1beta", endpoint.url());
    let profile = make_profile(&app, &state, "Gemini Native", &base_url);
    assert_eq!(
        protocol::list_models(&profile, None).unwrap(),
        vec!["gemini-test"]
    );
    let result = chat_with_tools(&profile).unwrap();
    assert_eq!(result.reasoning, "先想");
    assert_eq!(result.tool_calls[0].name, "workspace.read");
    assert!(result.tool_calls[0].id.starts_with("gemini-provider-test-"));
    assert_eq!(
        result.tool_calls[0].arguments,
        "{\"reference\":\"current\"}"
    );
    assert_eq!(result.usage.as_ref().unwrap().input_tokens, Some(6));
    assert_eq!(result.usage.as_ref().unwrap().output_tokens, Some(7));
    assert_eq!(result.usage.as_ref().unwrap().cached_input_tokens, Some(1));
    assert_eq!(result.finish_reason.as_deref(), Some("STOP"));
}

#[test]
fn ipc_dispatches_each_native_protocol_with_real_persistent_output() {
    let (app, _state) = setup();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    for (protocol_name, expected, models_body, stream_body) in [
        (
            "OpenAI Responses",
            "IPC Responses",
            "{\"data\":[{\"id\":\"test-model\"}]}",
            data(json!({"type": "response.output_text.delta", "delta": "IPC Responses"}))
                + &data(
                    json!({"type": "response.completed", "response": {"usage": {"input_tokens": 4, "output_tokens": 2}}}),
                ),
        ),
        (
            "Anthropic Messages",
            "IPC Anthropic",
            "{\"data\":[{\"id\":\"test-model\"}]}",
            data(
                json!({"type": "message_start", "message": {"usage": {"input_tokens": 4, "output_tokens": 1}}}),
            ) + &data(
                json!({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "IPC Anthropic"}}),
            ) + &data(
                json!({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}),
            ) + &data(json!({"type": "message_stop"})),
        ),
        (
            "Gemini Native",
            "IPC Gemini",
            "{\"models\":[{\"name\":\"models/test-model\"}]}",
            data(
                json!({"candidates": [{"content": {"parts": [{"text": "IPC Gemini"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}}),
            ),
        ),
    ] {
        let expected = expected.to_string();
        let stream_body = stream_body.clone();
        let server = server(move |request, stream| {
            if request.starts_with("GET ") {
                return response(stream, "200 OK", &models_body);
            }
            response(stream, "200 OK", &stream_body);
        });
        let created = ipc(
            &window,
            "upsert_provider_profile",
            json!({"input": {"name": protocol_name, "kind": "本地模型", "protocol": protocol_name, "baseUrl": server.url(), "defaultModel": "test-model", "models": ["test-model"], "apiKey": "protocol-ipc-token"}}),
        )
        .unwrap();
        let id = created["id"].as_str().unwrap().to_string();
        let models = ipc(
            &window,
            "test_provider",
            json!({"id": id, "realRequest": false}),
        )
        .unwrap();
        assert_eq!(models["models"], json!(["test-model"]));
        let chat = ipc(
            &window,
            "test_provider",
            json!({"id": id, "realRequest": true}),
        )
        .unwrap();
        assert_eq!(chat["result"]["text"], expected);
        let request_id = format!("ipc-protocol-{}", protocol_name.replace(' ', "-"));
        let result = ipc(
            &window,
            "stream_provider_chat",
            json!({"requestId": request_id, "profileId": id, "messages": [{"role": "user", "content": "hello"}], "maxTokens": 64}),
        )
        .unwrap();
        assert_eq!(result["status"], "done");
        assert_eq!(result["output"], expected);
        assert_eq!(result["requestId"], request_id);
        let stored = ipc(
            &window,
            "get_provider_chat",
            json!({"requestId": request_id}),
        )
        .unwrap();
        assert_eq!(stored["output"], expected);
        assert_eq!(server.request_count(), 3);
    }
}
