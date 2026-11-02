use super::test_support::{response, server, start_stream};
use super::*;
use crate::backend::BackendState;
use std::time::Duration;
use tauri::{Listener, WebviewWindowBuilder};

fn setup() -> (tauri::App<tauri::test::MockRuntime>, ProviderState) {
    let state = ProviderState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: crate::backend::materials::MaterialState::for_tests().unwrap(),
            providers: state.shared(),
            learning: crate::backend::learning::LearningState::for_tests().unwrap(),
            agent: crate::backend::agent::AgentState::for_tests().unwrap(),
        })
        .invoke_handler(tauri::generate_handler![
            crate::backend::commands::reset_application,
            crate::backend::providers::commands::list_provider_profiles,
            crate::backend::providers::commands::get_active_provider,
            crate::backend::providers::commands::upsert_provider_profile,
            crate::backend::providers::commands::set_active_provider,
            crate::backend::providers::commands::delete_provider_profile,
            crate::backend::providers::commands::export_provider_config,
            crate::backend::providers::commands::import_provider_deep_link,
            crate::backend::providers::commands::test_provider,
            crate::backend::providers::commands::stream_provider_chat,
            crate::backend::providers::commands::stream_provider_chat_with_tools,
            crate::backend::providers::commands::cancel_provider_chat,
            crate::backend::providers::commands::get_provider_chat,
            crate::backend::providers::commands::get_routing_settings,
            crate::backend::providers::commands::save_routing_settings,
            crate::backend::providers::commands::list_provider_route_logs,
            crate::backend::providers::commands::route_provider_chat,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
}

fn input(url: &str) -> ProfileInput {
    ProfileInput {
        id: None,
        name: "本地测试".into(),
        preset: Some("custom".into()),
        kind: "本地模型".into(),
        protocol: "OpenAI Chat".into(),
        base_url: url.into(),
        default_model: "test-model".into(),
        models: vec!["test-model".into()],
        enabled: Some(true),
        note: None,
        api_key: Some("unit-test-token".into()),
    }
}

fn messages(content: &str) -> Vec<ChatMessage> {
    vec![ChatMessage {
        role: "user".into(),
        content: content.into(),
        ..Default::default()
    }]
}

fn wait_for_output(state: &ProviderState, request_id: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let request = match get_chat(state, request_id) {
            Ok(request) => request,
            Err(error) if error.code == "not_found" => {
                std::thread::sleep(Duration::from_millis(5));
                continue;
            }
            Err(error) => panic!("{error}"),
        };
        if !request.output.is_empty() {
            assert_eq!(request.status, "running");
            return;
        }
        assert!(Instant::now() < deadline, "没有收到真实模型增量");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn profiles_events_and_exports_never_contain_credentials() {
    let (app, state) = setup();
    let saved = upsert(app.handle(), &state, input("http://127.0.0.1:8000/v1")).unwrap();
    assert!(saved.has_key);
    assert_eq!(
        state.credentials.get(&saved.id).unwrap().unwrap().as_str(),
        "unit-test-token"
    );
    set_active(app.handle(), &state, &saved.id).unwrap();
    assert_eq!(
        upsert(
            app.handle(),
            &state,
            ProfileInput {
                id: Some(saved.id.clone()),
                enabled: Some(false),
                api_key: None,
                ..input("http://127.0.0.1:8000/v1")
            },
        )
        .unwrap_err()
        .code,
        "provider_in_use"
    );
    let retained = upsert(
        app.handle(),
        &state,
        ProfileInput {
            id: Some(saved.id.clone()),
            api_key: None,
            ..input("http://127.0.0.1:8000/v1")
        },
    )
    .unwrap();
    assert!(retained.has_key);
    let store = state.store().unwrap();
    let events = serde_json::to_string(&store.events_since(0).unwrap()).unwrap();
    for serialized in [
        serde_json::to_string(&list(&state).unwrap()).unwrap(),
        serde_json::to_string(&export_config(&state).unwrap()).unwrap(),
        events,
    ] {
        assert!(!serialized.contains("unit-test-token"));
        assert!(!serialized.contains("api_key"));
        assert!(!serialized.contains("apiKey"));
    }
    let columns = store
        .conn
        .prepare("PRAGMA table_info(provider_profiles)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert!(!columns.contains(&"api_key".into()));
    assert!(!columns.contains(&"apiKey".into()));
    let other = upsert(
        app.handle(),
        &state,
        ProfileInput {
            name: "另一个".into(),
            ..input("http://127.0.0.1:8001/v1")
        },
    )
    .unwrap();
    assert_eq!(
        delete(app.handle(), &state, saved.id.trim_start_matches('p'))
            .unwrap_err()
            .code,
        "provider_in_use"
    );
    set_active(app.handle(), &state, &other.id).unwrap();
    delete(app.handle(), &state, &saved.id).unwrap();
    assert!(state.credentials.get(&saved.id).unwrap().is_none());
    let cleared = upsert(
        app.handle(),
        &state,
        ProfileInput {
            id: Some(other.id.clone()),
            api_key: Some(String::new()),
            ..input("http://127.0.0.1:8001/v1")
        },
    )
    .unwrap();
    assert!(!cleared.has_key);
}

#[test]
fn failed_database_writes_restore_the_previous_credential() {
    let (app, state) = setup();
    let saved = upsert(app.handle(), &state, input("http://127.0.0.1:8000/v1")).unwrap();
    let store = state.store().unwrap();
    store
        .conn
        .execute_batch(
            "CREATE TRIGGER reject_profile_event BEFORE INSERT ON events
             WHEN NEW.kind = 'provider.profile_saved' BEGIN SELECT RAISE(ABORT, 'rejected'); END;",
        )
        .unwrap();
    let error = upsert(
        app.handle(),
        &state,
        ProfileInput {
            id: Some(saved.id.clone()),
            name: "不应提交".into(),
            api_key: Some("replacement-token".into()),
            ..input("http://127.0.0.1:8000/v1")
        },
    )
    .unwrap_err();
    assert_eq!(error.code, "storage_failed");
    assert_eq!(
        state.credentials.get(&saved.id).unwrap().unwrap().as_str(),
        "unit-test-token"
    );
    assert_eq!(get(&state, &saved.id).unwrap().name, "本地测试");
    store
        .conn
        .execute_batch(
            "DROP TRIGGER reject_profile_event;
             CREATE TRIGGER reject_delete_event BEFORE INSERT ON events
             WHEN NEW.kind = 'provider.deleted' BEGIN SELECT RAISE(ABORT, 'rejected'); END;",
        )
        .unwrap();
    assert!(delete(app.handle(), &state, &saved.id).is_err());
    assert_eq!(
        state.credentials.get(&saved.id).unwrap().unwrap().as_str(),
        "unit-test-token"
    );
    assert!(get(&state, &saved.id).is_ok());
    store
        .conn
        .execute("DROP TRIGGER reject_delete_event", [])
        .unwrap();
    delete(app.handle(), &state, &saved.id).unwrap();
    assert!(state.credentials.get(&saved.id).unwrap().is_none());
}

#[test]
fn deep_link_discards_secrets_and_unsupported_protocol_is_explicit() {
    let (app, state) = setup();
    let profile = deep_link(app.handle(), &state, "deeptutor://provider?name=Test&baseUrl=http%3A%2F%2F127.0.0.1%3A8000%2Fv1&model=test-model&api_key=not-imported").unwrap();
    assert!(!profile.has_key);
    assert!(deep_link(app.handle(), &state, "https://provider?name=bad").is_err());
    assert!(upsert(
        app.handle(),
        &state,
        ProfileInput {
            base_url: "https://127.0.0.1/v1".into(),
            kind: "兼容服务".into(),
            ..input("http://127.0.0.1/v1")
        }
    )
    .is_err());
    let unsupported = upsert(
        app.handle(),
        &state,
        ProfileInput {
            protocol: "Anthropic Messages".into(),
            ..input("http://127.0.0.1:8000")
        },
    )
    .unwrap();
    assert_eq!(
        test(app.handle(), &state, &unsupported.id, false)
            .unwrap_err()
            .code,
        "unsupported_protocol"
    );
}

#[test]
fn model_tests_and_sse_chat_use_real_responses_without_duplicate_billing() {
    let (app, state) = setup();
    let server = server(|request, stream| {
        assert!(request
            .to_ascii_lowercase()
            .contains("authorization: bearer unit-test-token"));
        if request.starts_with("GET /models ") {
            std::thread::sleep(Duration::from_millis(20));
            response(stream, "200 OK", r#"{"data":[{"id":"real-model"}]}"#);
        } else if request.contains(r#""stream":false"#) {
            response(
                stream,
                "200 OK",
                r#"{"model":"test-model","choices":[{"message":{"content":"actual answer"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}"#,
            );
        } else {
            response(stream, "200 OK", "data: {\"model\":\"test-model\",\"choices\":[{\"delta\":{\"reasoning_content\":\"分析\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"先验\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\" 后验\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":8,\"completion_tokens\":2},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
        }
    });
    let saved = upsert(app.handle(), &state, input(&server.url())).unwrap();
    let models = test(app.handle(), &state, &saved.id, false).unwrap();
    assert_eq!(models.kind, "models");
    assert_eq!(models.models.unwrap(), vec!["real-model"]);
    assert!(models.latency_ms >= 10);
    let actual = test(app.handle(), &state, &saved.id, true).unwrap();
    assert_eq!(actual.kind, "chat");
    assert_eq!(actual.result.unwrap().text, "actual answer");
    let first = stream_chat(
        app.handle(),
        &state,
        "chat-1",
        &saved.id,
        &messages("hello"),
        8,
    )
    .unwrap();
    assert_eq!(first.status, "done");
    assert_eq!(first.output, "先验 后验");
    assert_eq!(first.reasoning, "分析");
    assert_eq!(first.usage.unwrap().input_tokens, Some(8));
    assert_eq!(first.attempts, 1);
    assert_eq!(server.request_count(), 3);
    let duplicate = stream_chat(
        app.handle(),
        &state,
        "chat-1",
        &saved.id,
        &messages("hello"),
        8,
    )
    .unwrap();
    assert_eq!(duplicate.output, "先验 后验");
    assert_eq!(server.request_count(), 3);
    let events = serde_json::to_string(&state.store().unwrap().events_since(0).unwrap()).unwrap();
    assert!(events.contains("先验"));
    assert!(events.contains(" 后验"));
    assert!(!events.contains("unit-test-token"));
    assert!(!events.contains("apiKey"));
}

#[test]
fn interrupted_stream_preserves_partial_text_and_does_not_retry_output() {
    let (app, state) = setup();
    let server = server(|_, stream| {
        response(
            stream,
            "200 OK",
            "data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n",
        );
    });
    let saved = upsert(app.handle(), &state, input(&server.url())).unwrap();
    let request = stream_chat(
        app.handle(),
        &state,
        "chat-partial",
        &saved.id,
        &messages("hello"),
        8,
    )
    .unwrap();
    assert_eq!(request.status, "failed");
    assert_eq!(request.error.unwrap().code, "incomplete_stream");
    assert_eq!(request.output, "partial");
    assert!(request.usage.is_none());
    assert_eq!(request.attempts, 1);
    assert_eq!(server.request_count(), 1);
}

#[test]
fn cancellation_and_concurrent_replays_never_start_another_request() {
    let (app, state) = setup();
    let release = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let server_release = Arc::clone(&release);
    let server = server(move |_, stream| {
        start_stream(stream);
        let _ = std::io::Write::write_all(
            stream,
            b"data: {\"choices\":[{\"delta\":{\"content\":\"observed\"}}]}\n\n",
        );
        let _ = std::io::Write::flush(stream);
        let deadline = Instant::now() + Duration::from_secs(5);
        while !server_release.load(std::sync::atomic::Ordering::Acquire) {
            assert!(Instant::now() < deadline, "测试未收到取消释放信号");
            std::thread::sleep(Duration::from_millis(5));
        }
        let _ = std::io::Write::write_all(stream, b"data: [DONE]\n\n");
    });
    let saved = upsert(app.handle(), &state, input(&server.url())).unwrap();
    let worker_app = app.handle().clone();
    let worker_state = state.shared();
    let provider_id = saved.id.clone();
    let worker = std::thread::spawn(move || {
        stream_chat(
            &worker_app,
            &worker_state,
            "chat-cancel",
            &provider_id,
            &messages("hello"),
            8,
        )
    });
    wait_for_output(&state, "chat-cancel");
    assert_eq!(
        stream_chat(
            app.handle(),
            &state,
            "chat-cancel",
            &saved.id,
            &messages("hello"),
            8,
        )
        .unwrap_err()
        .code,
        "request_active"
    );
    cancel_chat(&state, "chat-cancel").unwrap();
    release.store(true, std::sync::atomic::Ordering::Release);
    let request = worker.join().unwrap().unwrap();
    assert_eq!(request.status, "cancelled");
    assert_eq!(request.output, "observed");
    assert!(request.usage.is_none());
    assert_eq!(server.request_count(), 1);
    assert_eq!(
        cancel_chat(&state, "chat-cancel").unwrap_err().code,
        "no_active_request"
    );
}

#[test]
fn recovery_restores_outbox_output_without_calling_the_provider() {
    let (app, state) = setup();
    let server = server(|_, stream| response(stream, "200 OK", "{}"));
    let saved = upsert(app.handle(), &state, input(&server.url())).unwrap();
    let (_, guard) = begin_chat(app.handle(), &state, "chat-recover", &saved.id, None).unwrap();
    let store = state.store().unwrap();
    Store::event(
        &store.conn,
        "provider.chat_delta",
        json!({"requestId": "chat-recover", "text": "已恢复的部分输出", "reasoning": false}),
    )
    .unwrap();
    Store::event(
        &store.conn,
        "provider.chat_delta",
        json!({"requestId": "chat-recover", "text": "独立推理片段", "reasoning": true}),
    )
    .unwrap();
    drop(guard);
    recover_chats(app.handle(), &state).unwrap();
    let recovered = get_chat(&state, "chat-recover").unwrap();
    assert_eq!(recovered.status, "interrupted");
    assert_eq!(recovered.output, "已恢复的部分输出");
    assert_eq!(recovered.reasoning, "独立推理片段");
    assert_eq!(recovered.error.unwrap().code, "request_interrupted");
    let replay = stream_chat(
        app.handle(),
        &state,
        "chat-recover",
        &saved.id,
        &messages("hello"),
        8,
    )
    .unwrap();
    assert_eq!(replay.status, "interrupted");
    assert_eq!(server.request_count(), 0);
}

fn ipc(
    window: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    command: &str,
    arguments: Value,
) -> std::result::Result<Value, Value> {
    tauri::test::get_ipc_response(
        window,
        tauri::webview::InvokeRequest {
            cmd: command.to_string(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(arguments),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_string(),
        },
    )
    .map(|body| body.deserialize::<Value>().unwrap())
}

#[test]
fn ipc_provider_commands_emit_real_stream_and_reset_credentials() {
    let (app, state) = setup();
    let window = WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let server = server(|request, stream| {
        if request.starts_with("GET /models ") {
            response(stream, "200 OK", r#"{"data":[{"id":"ipc-model"}]}"#);
        } else {
            response(stream, "200 OK", "data: {\"model\":\"ipc-model\",\"choices\":[{\"delta\":{\"content\":\"原生\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\" IPC\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":4,\"completion_tokens\":2},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
        }
    });
    let created = ipc(
        &window,
        "upsert_provider_profile",
        json!({"input": {
            "name": "IPC 本地模型", "kind": "本地模型", "protocol": "OpenAI Chat",
            "baseUrl": server.url(), "defaultModel": "test-model", "models": ["test-model"],
            "note": "IPC 个人备注", "apiKey": "ipc-test-token"
        }}),
    )
    .unwrap();
    let provider_id = created["id"].as_str().unwrap().to_string();
    assert_eq!(created["hasKey"], json!(true));
    assert!(!serde_json::to_string(&created)
        .unwrap()
        .contains("ipc-test-token"));
    ipc(&window, "set_active_provider", json!({"id": provider_id})).unwrap();
    assert_eq!(
        ipc(&window, "get_active_provider", json!({})).unwrap(),
        json!(provider_id)
    );
    let models = ipc(
        &window,
        "test_provider",
        json!({"id": provider_id, "realRequest": false}),
    )
    .unwrap();
    assert_eq!(models["kind"], json!("models"));
    assert_eq!(models["models"], json!(["ipc-model"]));
    let profiles = ipc(&window, "list_provider_profiles", json!({})).unwrap();
    assert_eq!(profiles[0]["note"], json!("IPC 个人备注"));

    let received = Arc::new(Mutex::new(Vec::new()));
    let listener_events = Arc::clone(&received);
    app.listen("backend://event", move |message| {
        let event: Value = serde_json::from_str(message.payload()).unwrap();
        if event["kind"] == "provider.chat_delta" {
            listener_events.lock().unwrap().push(event);
        }
    });
    let arguments = json!({
        "requestId": "ipc-chat-1", "profileId": provider_id,
        "messages": [{"role": "user", "content": "hello"}], "maxTokens": 64
    });
    let completed = ipc(&window, "stream_provider_chat", arguments.clone()).unwrap();
    assert_eq!(completed["status"], json!("done"));
    assert_eq!(completed["output"], json!("原生 IPC"));
    assert_eq!(completed["usage"]["inputTokens"], json!(4));
    assert_eq!(completed["attempts"], json!(1));
    let deadline = Instant::now() + Duration::from_secs(5);
    while received.lock().unwrap().len() < 2 {
        assert!(Instant::now() < deadline, "IPC 没有收到两个原生增量");
        std::thread::sleep(Duration::from_millis(5));
    }
    let deltas = received.lock().unwrap();
    assert_eq!(deltas[0]["payload"]["text"], json!("原生"));
    assert_eq!(deltas[1]["payload"]["text"], json!(" IPC"));
    drop(deltas);
    let replay = ipc(&window, "stream_provider_chat", arguments).unwrap();
    assert_eq!(replay["output"], json!("原生 IPC"));
    assert_eq!(server.request_count(), 2);
    let exported = ipc(&window, "export_provider_config", json!({})).unwrap();
    let events = state.store().unwrap().events_since(0).unwrap();
    for serialized in [
        serde_json::to_string(&exported).unwrap(),
        serde_json::to_string(&events).unwrap(),
    ] {
        assert!(!serialized.contains("ipc-test-token"));
        assert!(!serialized.contains("apiKey"));
        assert!(!serialized.contains("api_key"));
    }
    ipc(&window, "reset_application", json!({})).unwrap();
    assert_eq!(
        ipc(&window, "list_provider_profiles", json!({})).unwrap(),
        json!([])
    );
    assert!(state.credentials.get(&provider_id).unwrap().is_none());
    assert_eq!(
        ipc(
            &window,
            "get_provider_chat",
            json!({"requestId": "ipc-chat-1"})
        )
        .unwrap_err()["code"],
        json!("not_found")
    );
}

#[test]
fn provider_request_migration_adds_reasoning_without_losing_output() {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE provider_requests (
            request_id TEXT PRIMARY KEY,
            provider_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            usage_json TEXT,
            finish_reason TEXT,
            error_code TEXT,
            error_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 1,
            latency_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO provider_requests (request_id, provider_id, model, status, output, created_at, updated_at)
        VALUES ('old-request', 1, 'old-model', 'done', '{\"ok\":true}', 'now', 'now');",
    )
    .unwrap();
    migrate(&conn).unwrap();
    let (output, reasoning) = conn
        .query_row(
            "SELECT output, reasoning FROM provider_requests WHERE request_id = 'old-request'",
            [],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )
        .unwrap();
    assert_eq!(output, "{\"ok\":true}");
    assert!(reasoning.is_empty());
}

#[test]
fn ipc_tool_calls_are_persisted_and_can_be_read_back() {
    let (app, state) = setup();
    let server = server(|request, stream| {
        assert!(request.contains("\"tools\""));
        assert!(request.contains("memory.recall"));
        response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call-1\",\"type\":\"function\",\"function\":{\"name\":\"memory.recall\",\"arguments\":\"{\\\"key\\\":\"}}]}}]}\n\ndata: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"topic\\\"}\"}}]}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":3},\"choices\":[{\"delta\":{},\"finish_reason\":\"tool_calls\"}]}\n\ndata: [DONE]\n\n");
    });
    let profile = upsert(app.handle(), &state, input(&server.url())).unwrap();
    let window = WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let result = ipc(
        &window,
        "stream_provider_chat_with_tools",
        json!({
            "requestId": "tool-call-1", "profileId": profile.id, "maxTokens": 64,
            "messages": [{"role": "user", "content": "读取学习偏好"}],
            "tools": [{"type": "function", "function": {"name": "memory.recall", "description": "读取已批准记忆", "parameters": {"type": "object"}}}],
        }),
    )
    .unwrap();
    assert_eq!(result["status"], json!("done"));
    assert_eq!(result["output"], json!(""));
    assert_eq!(result["toolCalls"][0]["id"], json!("call-1"));
    assert_eq!(result["toolCalls"][0]["name"], json!("memory.recall"));
    assert_eq!(
        result["toolCalls"][0]["arguments"],
        json!(r#"{"key":"topic"}"#)
    );
    assert_eq!(result["usage"]["inputTokens"], json!(7));
    let stored = ipc(
        &window,
        "get_provider_chat",
        json!({"requestId": "tool-call-1"}),
    )
    .unwrap();
    assert_eq!(stored["toolCalls"], result["toolCalls"]);
    assert_eq!(server.request_count(), 1);
}

fn routed_profile(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &ProviderState,
    name: &str,
    url: &str,
    default_model: &str,
    models: &[&str],
) -> ProviderProfile {
    upsert(
        app.handle(),
        state,
        ProfileInput {
            name: name.into(),
            default_model: default_model.into(),
            models: models.iter().map(|model| model.to_string()).collect(),
            ..input(url)
        },
    )
    .unwrap()
}

fn settings_with_mode(mode: &str) -> routing::RoutingSettings {
    routing::RoutingSettings {
        mode: mode.into(),
        routes: std::collections::BTreeMap::new(),
        aggregate_provider_ids: vec![],
        aggregate_default: None,
    }
}

#[test]
fn task_routing_uses_actual_models_and_protects_referenced_profiles() {
    let (app, state) = setup();
    let server_a = server(|request, stream| {
        assert!(
            request.contains("\"model\":\"a-model\"")
                || request.contains("\"model\":\"test-model\"")
        );
        response(
            stream,
            "200 OK",
            "data: {\"choices\":[{\"delta\":{\"content\":\"A 真实输出\"}}]}\n\ndata: [DONE]\n\n",
        );
    });
    let server_b = server(|request, stream| {
        assert!(request.contains("\"model\":\"b-model\""));
        response(
            stream,
            "200 OK",
            "data: {\"choices\":[{\"delta\":{\"content\":\"B 真实输出\"}}]}\n\ndata: [DONE]\n\n",
        );
    });
    let a = routed_profile(&app, &state, "A", &server_a.url(), "a-model", &["a-model"]);
    let b = routed_profile(
        &app,
        &state,
        "B",
        &server_b.url(),
        "b-model",
        &["b-model", "shared"],
    );
    set_active(app.handle(), &state, &a.id).unwrap();
    let mut settings = settings_with_mode("routing");
    settings.routes.insert(
        "agent".into(),
        routing::RouteAssignment {
            provider_id: Some(b.id.clone()),
            model: Some("b-model".into()),
        },
    );
    settings.routes.insert(
        "plan".into(),
        routing::RouteAssignment {
            provider_id: Some(a.id.clone()),
            model: Some("a-model".into()),
        },
    );
    routing::save_settings(app.handle(), &state, settings.clone()).unwrap();
    let reopened = ProviderState::for_tests_at(&state.database_path);
    assert_eq!(routing::settings(&reopened).unwrap().mode, "routing");
    assert_eq!(routing::provider_for_task(&state, "agent").unwrap(), b.id);
    let agent = routing::stream_chat(
        app.handle(),
        &state,
        "route-agent-1",
        "agent",
        Some(&a.id),
        &messages("Agent"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(agent.provider_id, b.id);
    assert_eq!(agent.model, "b-model");
    assert_eq!(agent.output, "B 真实输出");
    let replay = routing::stream_chat(
        app.handle(),
        &state,
        "route-agent-1",
        "agent",
        Some(&a.id),
        &messages("Agent"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(replay.output, agent.output);
    assert_eq!(server_b.request_count(), 1);
    let plan = routing::stream_chat(
        app.handle(),
        &state,
        "route-plan-1",
        "plan",
        Some(&b.id),
        &messages("方案"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(plan.provider_id, a.id);
    assert_eq!(plan.model, "a-model");
    assert_eq!(server_a.request_count(), 1);
    let mut invalid = settings.clone();
    invalid.routes.insert(
        "agent".into(),
        routing::RouteAssignment {
            provider_id: Some(b.id.clone()),
            model: Some("missing".into()),
        },
    );
    assert_eq!(
        routing::save_settings(app.handle(), &state, invalid)
            .unwrap_err()
            .code,
        "invalid_route_model"
    );
    assert_eq!(
        upsert(
            app.handle(),
            &state,
            ProfileInput {
                id: Some(b.id.clone()),
                enabled: Some(false),
                api_key: None,
                ..input(&server_b.url())
            },
        )
        .unwrap_err()
        .code,
        "provider_in_use"
    );
    assert_eq!(
        delete(app.handle(), &state, &b.id).unwrap_err().code,
        "provider_in_use"
    );
    routing::save_settings(app.handle(), &state, settings_with_mode("direct")).unwrap();
    let direct = routing::stream_chat(
        app.handle(),
        &state,
        "route-direct-1",
        "agent",
        None,
        &messages("直连"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(direct.provider_id, a.id);
    assert_eq!(direct.model, "a-model");
    assert_eq!(server_a.request_count(), 2);
    let logs = routing::logs(&state).unwrap();
    assert_eq!(logs.len(), 3);
    assert!(logs.iter().any(|log| log.request_id == "route-agent-1"
        && log.provider_id.as_deref() == Some(b.id.as_str())));
    assert_eq!(export_config(&state).unwrap()["routing"]["mode"], "direct");
}

#[test]
fn aggregate_routes_by_model_without_failover() {
    let (app, state) = setup();
    let failing = server(|_request, stream| response(stream, "503 Service Unavailable", "{}"));
    let healthy = server(|request, stream| {
        assert!(request.contains("\"model\":\"b-model\""));
        response(
            stream,
            "200 OK",
            "data: {\"choices\":[{\"delta\":{\"content\":\"聚合 B 输出\"}}]}\n\ndata: [DONE]\n\n",
        );
    });
    let a = routed_profile(
        &app,
        &state,
        "故障 A",
        &failing.url(),
        "shared",
        &["shared"],
    );
    let b = routed_profile(
        &app,
        &state,
        "健康 B",
        &healthy.url(),
        "b-model",
        &["b-model"],
    );
    let mut settings = settings_with_mode("aggregate");
    settings.aggregate_provider_ids = vec![a.id.clone(), b.id.clone()];
    settings.aggregate_default = Some(a.id.clone());
    routing::save_settings(app.handle(), &state, settings).unwrap();
    let selected = routing::stream_chat(
        app.handle(),
        &state,
        "aggregate-b",
        "agent",
        None,
        &messages("选择模型"),
        64,
        Some("b-model"),
        None,
        false,
    )
    .unwrap();
    assert_eq!(selected.provider_id, b.id);
    assert_eq!(selected.output, "聚合 B 输出");
    assert_eq!(healthy.request_count(), 1);
    let failed = routing::stream_chat(
        app.handle(),
        &state,
        "aggregate-default",
        "agent",
        None,
        &messages("默认模型"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.provider_id, a.id);
    assert_eq!(failing.request_count(), 2);
    assert_eq!(healthy.request_count(), 1);
    assert_eq!(
        routing::stream_chat(
            app.handle(),
            &state,
            "aggregate-missing",
            "agent",
            None,
            &messages("未知模型"),
            64,
            Some("missing"),
            None,
            false,
        )
        .unwrap_err()
        .code,
        "aggregate_model_unavailable"
    );
    let mut invalid = settings_with_mode("aggregate");
    invalid.aggregate_provider_ids = vec![a.id.clone(), b.id.clone()];
    assert_eq!(
        routing::save_settings(app.handle(), &state, invalid)
            .unwrap_err()
            .code,
        "aggregate_default_required"
    );
    assert!(routing::provider_in_use(&state, &b.id).unwrap());
    assert_eq!(
        delete(app.handle(), &state, &b.id).unwrap_err().code,
        "provider_in_use"
    );
    assert_eq!(routing::logs(&state).unwrap().len(), 2);
}

#[test]
fn ipc_routing_commands_save_configuration_and_return_real_output() {
    let (app, _state) = setup();
    let server = server(|request, stream| {
        assert!(request.contains("\"model\":\"ipc-route-model\""));
        response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"content\":\"路由 IPC 输出\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":3},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
    });
    let window = WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let created = ipc(
        &window,
        "upsert_provider_profile",
        json!({"input": {"name": "路由 IPC", "kind": "本地模型", "protocol": "OpenAI Chat", "baseUrl": server.url(), "defaultModel": "ipc-route-model", "models": ["ipc-route-model"], "apiKey": "route-ipc-token"}}),
    )
    .unwrap();
    let id = created["id"].as_str().unwrap().to_string();
    ipc(&window, "set_active_provider", json!({"id": id})).unwrap();
    let saved = ipc(
        &window,
        "save_routing_settings",
        json!({"settings": {"mode": "routing", "routes": {"agent": {"providerId": id, "model": "ipc-route-model"}}, "aggregateProviderIds": [], "aggregateDefault": null}}),
    )
    .unwrap();
    assert_eq!(saved["mode"], "routing");
    let received = Arc::new(Mutex::new(Vec::new()));
    let listener_events = Arc::clone(&received);
    app.listen("backend://event", move |message| {
        let event: Value = serde_json::from_str(message.payload()).unwrap();
        if event["kind"] == "provider.route_started" {
            listener_events.lock().unwrap().push(event);
        }
    });
    let result = ipc(
        &window,
        "route_provider_chat",
        json!({"requestId": "ipc-route-1", "task": "agent", "profileId": null, "model": null, "messages": [{"role": "user", "content": "验证真实路由"}], "maxTokens": 64}),
    )
    .unwrap();
    assert_eq!(result["providerId"], id);
    assert_eq!(result["model"], "ipc-route-model");
    assert_eq!(result["output"], "路由 IPC 输出");
    assert_eq!(result["usage"]["outputTokens"], 3);
    let settings = ipc(&window, "get_routing_settings", json!({})).unwrap();
    assert_eq!(settings["routes"]["agent"]["model"], "ipc-route-model");
    let logs = ipc(&window, "list_provider_route_logs", json!({})).unwrap();
    assert_eq!(logs[0]["requestId"], "ipc-route-1");
    assert_eq!(logs[0]["status"], "done");
    let deadline = Instant::now() + Duration::from_secs(5);
    while received.lock().unwrap().is_empty() {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(received.lock().unwrap()[0]["payload"]["providerId"], id);
    assert!(!serde_json::to_string(&logs)
        .unwrap()
        .contains("route-ipc-token"));
    ipc(&window, "reset_application", json!({})).unwrap();
    assert_eq!(
        ipc(&window, "list_provider_route_logs", json!({})).unwrap(),
        json!([])
    );
    assert_eq!(
        ipc(&window, "get_routing_settings", json!({})).unwrap()["mode"],
        "direct"
    );
}

#[test]
fn route_recovery_preserves_an_already_completed_upstream_result() {
    let (app, state) = setup();
    let server = server(|_request, stream| {
        response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"content\":\"已完成的路由输出\"}}]}\n\ndata: [DONE]\n\n");
    });
    let profile = routed_profile(
        &app,
        &state,
        "恢复测试",
        &server.url(),
        "test-model",
        &["test-model"],
    );
    set_active(app.handle(), &state, &profile.id).unwrap();
    let completed = routing::stream_chat(
        app.handle(),
        &state,
        "route-recovery-1",
        "agent",
        None,
        &messages("恢复"),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(completed.status, "done");
    let store = state.store().unwrap();
    store
        .conn
        .execute(
            "UPDATE provider_route_runs SET status = 'running' WHERE request_id = 'route-recovery-1'",
            [],
        )
        .unwrap();
    store
        .conn
        .execute(
            "UPDATE provider_route_attempts SET status = 'running' WHERE request_id = 'route-recovery-1'",
            [],
        )
        .unwrap();
    drop(store);
    routing::recover(app.handle(), &state).unwrap();
    let logs = routing::logs(&state).unwrap();
    assert_eq!(logs[0].status, "done");
    assert!(logs[0].error.is_none());
    assert_eq!(server.request_count(), 1);
}
