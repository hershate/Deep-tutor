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
            crate::backend::providers::commands::cancel_provider_chat,
            crate::backend::providers::commands::get_provider_chat,
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
            response(stream, "200 OK", "data: {\"model\":\"test-model\",\"choices\":[{\"delta\":{\"content\":\"先验\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\" 后验\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":8,\"completion_tokens\":2},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
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
    let server = server(|_, stream| {
        start_stream(stream);
        let _ = std::io::Write::write_all(
            stream,
            b"data: {\"choices\":[{\"delta\":{\"content\":\"observed\"}}]}\n\n",
        );
        let _ = std::io::Write::flush(stream);
        std::thread::sleep(Duration::from_millis(200));
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
    let (_, guard) = begin_chat(app.handle(), &state, "chat-recover", &saved.id).unwrap();
    let store = state.store().unwrap();
    Store::event(
        &store.conn,
        "provider.chat_delta",
        json!({"requestId": "chat-recover", "text": "已恢复的部分输出", "reasoning": false}),
    )
    .unwrap();
    drop(guard);
    recover_chats(app.handle(), &state).unwrap();
    let recovered = get_chat(&state, "chat-recover").unwrap();
    assert_eq!(recovered.status, "interrupted");
    assert_eq!(recovered.output, "已恢复的部分输出");
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
