use super::*;
use crate::backend::providers::test_support::{response, server, start_stream, TestServer};
use crate::backend::providers::{self, ChatMessage, ProfileInput};
use crate::backend::BackendState;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn setup() -> (tauri::App<tauri::test::MockRuntime>, ProviderState) {
    let state = ProviderState::for_tests().unwrap();
    // 12#18：filler 与主状态共享同一 database_path（生产同路径装配）
    let path = state.test_database_path().to_path_buf();
    let materials = crate::backend::materials::MaterialState::new(&path).unwrap();
    let learning = crate::backend::learning::LearningState::new(&path, &state, &materials);
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: materials.shared(),
            providers: state.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &state, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &state, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(&path, &state, &materials),
            community: crate::backend::community::CommunityState::new(&path),
            plugins: crate::backend::plugins::PluginState::new(&path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            super::super::commands::get_usage_overview,
            super::super::commands::save_usage_settings,
            super::super::commands::upsert_usage_rate,
            super::super::commands::export_usage_csv,
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

fn model_server(gate: Arc<AtomicBool>) -> TestServer {
    server(move |_request, stream| {
        if !gate.load(Ordering::Acquire) {
            start_stream(stream);
            let gate_deadline = Instant::now() + Duration::from_secs(5);
            while !gate.load(Ordering::Acquire) {
                assert!(
                    Instant::now() < gate_deadline,
                    "测试门未在 5 秒内释放，可能前置断言已失败"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"content\":\"真实回答\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":12,\"completion_tokens\":33},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
    })
}

fn prepare() -> (
    tauri::App<tauri::test::MockRuntime>,
    ProviderState,
    TestServer,
    Arc<AtomicBool>,
    String,
) {
    let (app, state) = setup();
    let gate = Arc::new(AtomicBool::new(true));
    let server = model_server(Arc::clone(&gate));
    let profile = providers::upsert(
        app.handle(),
        &state,
        ProfileInput {
            id: None,
            name: "用量测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: server.url(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("usage-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &state, &profile.id).unwrap();
    (app, state, server, gate, profile.id)
}

fn messages() -> Vec<ChatMessage> {
    vec![ChatMessage {
        role: "user".into(),
        content: "真实用量测试".into(),
        ..Default::default()
    }]
}

#[test]
fn real_usage_rates_cancelled_requests_csv_and_budget_are_consistent() {
    let (app, state, server, gate, provider_id) = prepare();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    assert_eq!(
        upsert_rate(
            app.handle(),
            &state,
            RateInput {
                provider_id: provider_id.clone(),
                model: "missing".into(),
                input_per_million: 1.0,
                output_per_million: 2.0
            }
        )
        .unwrap_err()
        .code,
        "invalid_rate_model"
    );
    assert_eq!(
        upsert_rate(
            app.handle(),
            &state,
            RateInput {
                provider_id: provider_id.clone(),
                model: "test-model".into(),
                input_per_million: -1.0,
                output_per_million: 2.0
            }
        )
        .unwrap_err()
        .code,
        "invalid_usage_rate"
    );
    let rate = ipc(&window, "upsert_usage_rate", json!({"input": {"providerId": provider_id, "model": "test-model", "inputPerMillion": 1.0, "outputPerMillion": 2.0}})).unwrap();
    assert_eq!(rate["providerId"], provider_id);
    assert_eq!(rate["inputPerMillion"], 1.0);
    let request = providers::routing::stream_chat(
        app.handle(),
        &state,
        "usage-1",
        "wiki",
        Some(&provider_id),
        &messages(),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(request.status, "done");
    assert_eq!(request.usage.as_ref().unwrap().input_tokens, Some(12));
    assert_eq!(request.usage.as_ref().unwrap().output_tokens, Some(33));
    let usage_json = ipc(&window, "get_usage_overview", json!({"days": 7})).unwrap();
    let today_cost = usage_json["summary"]["todayCost"].as_f64().unwrap();
    assert!((today_cost - 0.000078).abs() < 0.000000001);
    assert_eq!(usage_json["summary"]["inputTokens"], 12);
    assert_eq!(usage_json["summary"]["outputTokens"], 33);
    assert_eq!(usage_json["summary"]["missingUsage"], 0);
    assert_eq!(usage_json["models"][0]["share"], 100.0);
    assert_eq!(usage_json["models"][0]["tokens"], 45);
    assert_eq!(usage_json["ledger"][0]["status"], "done");
    assert_eq!(usage_json["ledger"][0]["task"], "wiki");
    assert_eq!(usage_json["rates"].as_array().unwrap().len(), 1);
    assert_eq!(usage_json["days"].as_array().unwrap().len(), 7);
    let csv = ipc(&window, "export_usage_csv", json!({}))
        .unwrap()
        .as_str()
        .unwrap()
        .to_string();
    assert!(csv.contains("usage-1"));
    assert!(csv.contains("0.0000780000"));
    assert!(!csv.contains("usage-test-token"));
    assert!(!csv.contains("真实回答"));

    gate.store(false, Ordering::Release);
    let worker_state = state.shared();
    let worker_app = app.handle().clone();
    let worker_provider = provider_id.clone();
    let worker = std::thread::spawn(move || {
        providers::stream_chat(
            &worker_app,
            &worker_state,
            "usage-cancel",
            &worker_provider,
            &messages(),
            64,
        )
    });
    let deadline = Instant::now() + Duration::from_secs(5);
    while server.request_count() < 2 {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    providers::cancel_chat(&state, "usage-cancel").unwrap();
    gate.store(true, Ordering::Release);
    let cancelled = worker.join().unwrap().unwrap();
    assert_eq!(cancelled.status, "cancelled");
    assert!(cancelled.usage.is_none());
    let after_cancel = overview(&state, Some(7)).unwrap();
    assert_eq!(after_cancel.summary.missing_usage, 1);
    assert_eq!(after_cancel.summary.cancelled_cost, 0.0);
    let cancelled_entry = after_cancel
        .ledger
        .iter()
        .find(|entry| entry.request_id == "usage-cancel")
        .unwrap();
    assert_eq!(cancelled_entry.status, "cancelled");
    assert_eq!(cancelled_entry.cost, None);

    save_settings(
        app.handle(),
        &state,
        UsageSettings {
            session_usd: 1.0,
            daily_usd: 0.0,
            regeneration_limit: 2,
        },
    )
    .unwrap();
    assert_eq!(
        providers::routing::stream_chat(
            app.handle(),
            &state,
            "usage-new",
            "wiki",
            Some(&provider_id),
            &messages(),
            64,
            None,
            None,
            false
        )
        .unwrap_err()
        .code,
        "daily_budget_exceeded"
    );
    assert_eq!(server.request_count(), 2);
    let replay = providers::routing::stream_chat(
        app.handle(),
        &state,
        "usage-1",
        "wiki",
        Some(&provider_id),
        &messages(),
        64,
        None,
        None,
        false,
    )
    .unwrap();
    assert_eq!(replay.status, "done");
    assert_eq!(server.request_count(), 2);
    save_settings(
        app.handle(),
        &state,
        UsageSettings {
            session_usd: 0.0,
            daily_usd: 10.0,
            regeneration_limit: 1,
        },
    )
    .unwrap();
    assert_eq!(
        ensure_budget(&state, Some(999)).unwrap_err().code,
        "session_budget_exceeded"
    );
    assert_eq!(settings(&state).unwrap().regeneration_limit, 1);
    assert_eq!(
        save_settings(
            app.handle(),
            &state,
            UsageSettings {
                session_usd: f64::NAN,
                daily_usd: 1.0,
                regeneration_limit: 2
            }
        )
        .unwrap_err()
        .code,
        "invalid_usage_settings"
    );
}
