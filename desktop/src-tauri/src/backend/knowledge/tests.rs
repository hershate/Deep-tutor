use super::*;
use crate::backend::providers::test_support::{response, server, start_stream, TestServer};
use crate::backend::providers::{self, ProfileInput};
use crate::backend::BackendState;
use std::sync::Mutex;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, KnowledgeState) {
    let state = KnowledgeState::for_tests().unwrap();
    let path = state.database_path.clone();
    let providers = state.providers.shared();
    let materials = state.materials.shared();
    let learning = crate::backend::learning::LearningState::new(&path, &providers, &materials);
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: state.materials.shared(),
            providers: state.providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: state.shared(),
            community: crate::backend::community::CommunityState::new(&path),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::search_knowledge,
            super::commands::ask_knowledge,
            super::commands::list_knowledge_sessions,
            super::commands::get_knowledge_session,
            super::commands::cancel_knowledge_answer,
            super::commands::resume_knowledge_answer,
            super::commands::delete_knowledge_session,
            super::commands::clear_knowledge_history,
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

fn model_server(material_id: i64, valid: bool, gate: Option<Arc<AtomicBool>>) -> TestServer {
    server(move |_request, stream| {
        if let Some(gate) = &gate {
            start_stream(stream);
            while !gate.load(Ordering::Acquire) {
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        let body = json!({
            "answer": "先验概率是在看到新证据之前的判断。",
            "sufficient": true,
            "citations": [{"materialId": material_id, "quote": if valid { "先验概率" } else { "并不存在的资料原文" }}],
        });
        let event =
            serde_json::to_string(&json!({"choices": [{"delta": {"content": body.to_string()}}]}))
                .unwrap();
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":12,\"completion_tokens\":20}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"));
    })
}

fn prepare_with_gate(
    valid: bool,
    gate: Option<Arc<AtomicBool>>,
) -> (
    tauri::App<tauri::test::MockRuntime>,
    KnowledgeState,
    TestServer,
    i64,
    String,
) {
    let (app, state) = setup();
    let material = crate::backend::materials::import_text(
        app.handle(), &state.materials, "概率教材".into(),
        "概率是可能性的度量。\n先验概率是在看到证据前的判断，后验概率结合新证据更新。\n贝叶斯定理连接先验与后验。".into(),
    ).unwrap().material;
    let id = material.id;
    crate::backend::materials::enqueue(app.handle(), &state.materials, id).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let detail = crate::backend::materials::detail(&state.materials, id).unwrap();
        if detail.text.is_some()
            && ["parsed", "degraded"].contains(&detail.material.status.as_str())
        {
            break;
        }
        assert_ne!(detail.material.status, "failed");
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let server = model_server(id, valid, gate);
    let profile = providers::upsert(
        app.handle(),
        &state.providers,
        ProfileInput {
            id: None,
            name: "Wiki 测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: server.url(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("wiki-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &state.providers, &profile.id).unwrap();
    (app, state, server, id, profile.id)
}

fn prepare() -> (
    tauri::App<tauri::test::MockRuntime>,
    KnowledgeState,
    TestServer,
    i64,
    String,
) {
    prepare_with_gate(true, None)
}

fn wait(state: &KnowledgeState, id: i64) -> KnowledgeSession {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let session = get(state, id).unwrap();
        if !["running", "cancelling"].contains(&session.status.as_str()) {
            return session;
        }
        assert!(Instant::now() < deadline, "Wiki 问答未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn search_and_answer_use_real_notes_material_text_and_citation_locations() {
    let (app, state, server, material_id, _) = prepare();
    state.store().unwrap().replace_collection("notes", 0, &json!([{
        "id": 7, "title": "先验概率笔记", "excerpt": "先验概率", "content": "先验概率来自看到证据前的判断。", "tag": "复盘", "updated": "刚刚",
    }])).unwrap();
    let hits = search(&state, "先验概率").unwrap();
    assert_eq!(hits.len(), 2);
    let material_hit = hits.iter().find(|hit| hit.kind == "material").unwrap();
    assert_eq!(material_hit.material_id, Some(material_id));
    assert_eq!(material_hit.citation.as_ref().unwrap().quote, "先验概率");
    assert_eq!(material_hit.citation.as_ref().unwrap().line, 2);
    assert!(material_hit.citation.as_ref().unwrap().char_start > 0);
    assert!(hits
        .iter()
        .any(|hit| hit.kind == "note" && hit.note_id == Some(7)));
    assert!(search(&state, "不存在的概念").unwrap().is_empty());
    assert_eq!(search(&state, "").unwrap_err().code, "invalid_search_query");

    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let asked = ipc(
        &window,
        "ask_knowledge",
        json!({"question": "先验概率是什么？", "materialIds": [material_id]}),
    )
    .unwrap();
    assert_eq!(asked["status"], "running");
    let session = wait(&state, asked["id"].as_i64().unwrap());
    assert_eq!(session.status, "done");
    assert_eq!(
        session.answer.as_deref(),
        Some("先验概率是在看到新证据之前的判断。")
    );
    assert_eq!(session.sufficient, Some(true));
    assert_eq!(session.citations.len(), 1);
    assert_eq!(session.citations[0].material_id, material_id);
    assert_eq!(session.citations[0].line, 2);
    assert_eq!(session.citations[0].quote, "先验概率");
    assert_eq!(session.sources[0].material_id, material_id);
    assert_eq!(session.request_id.as_deref(), Some("wiki-1-1"));
    assert_eq!(server.request_count(), 1);
    assert_eq!(list(&state).unwrap().len(), 1);
    assert_eq!(
        ipc(&window, "get_knowledge_session", json!({"id": session.id})).unwrap()["answer"],
        "先验概率是在看到新证据之前的判断。"
    );
    assert_eq!(
        crate::backend::materials::delete(app.handle(), &state.materials, material_id)
            .unwrap_err()
            .code,
        "material_in_use"
    );
    ipc(
        &window,
        "delete_knowledge_session",
        json!({"id": session.id}),
    )
    .unwrap();
    assert!(list(&state).unwrap().is_empty());
    crate::backend::materials::delete(app.handle(), &state.materials, material_id).unwrap();
    let events = state.store().unwrap().events_since(0).unwrap().events;
    assert!(events
        .iter()
        .any(|event| event.kind == "knowledge.answer_completed"));
    assert!(events
        .iter()
        .any(|event| event.kind == "knowledge.session_deleted"));
}

#[test]
fn citations_outside_scope_fail_without_becoming_an_answer() {
    let (app, state, server, material_id, _) = prepare_with_gate(false, None);
    let started = ask(
        app.handle(),
        &state,
        "先验概率是什么？".into(),
        vec![material_id],
    )
    .unwrap();
    let failed = wait(&state, started.id);
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.error.unwrap().code, "citation_mismatch");
    assert_eq!(failed.answer, None);
    assert_eq!(failed.citations.len(), 0);
    resume(app.handle(), &state, started.id).unwrap();
    let failed = wait(&state, started.id);
    assert_eq!(failed.attempt, 2);
    assert_eq!(failed.request_id.as_deref(), Some("wiki-1-2"));
    assert_eq!(server.request_count(), 2);
}

#[test]
fn cancellation_resume_and_reset_preserve_real_history() {
    let gate = Arc::new(AtomicBool::new(false));
    let (app, state, server, material_id, _) = prepare_with_gate(true, Some(Arc::clone(&gate)));
    let started = ask(
        app.handle(),
        &state,
        "先验概率是什么？".into(),
        vec![material_id],
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while server.request_count() < 1 {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let cancelling = cancel(app.handle(), &state, started.id).unwrap();
    assert_eq!(cancelling.status, "cancelling");
    gate.store(true, Ordering::Release);
    assert_eq!(wait(&state, started.id).status, "cancelled");
    resume(app.handle(), &state, started.id).unwrap();
    let done = wait(&state, started.id);
    assert_eq!(done.status, "done");
    assert_eq!(done.attempt, 2);
    assert_eq!(done.request_id.as_deref(), Some("wiki-1-2"));
    assert_eq!(server.request_count(), 2);
    stop(&state).unwrap();
    clear_history(app.handle(), &state).unwrap();
    assert!(list(&state).unwrap().is_empty());
    assert!(crate::backend::materials::delete(app.handle(), &state.materials, material_id).is_ok());
}

#[test]
fn interrupted_answer_reuses_completed_provider_output() {
    let (app, state, server, material_id, provider_id) = prepare();
    let store = state.store().unwrap();
    store.conn.execute(
        "INSERT INTO knowledge_sessions (question, status, provider_id, request_id, created_at, updated_at) VALUES ('先验概率是什么？', 'running', ?1, 'wiki-1-1', 'now', 'now')",
        params![provider_id],
    ).unwrap();
    store
        .conn
        .execute(
            "INSERT INTO knowledge_session_materials (session_id, material_id) VALUES (1, ?1)",
            params![material_id],
        )
        .unwrap();
    drop(store);
    let result = providers::stream_chat(
        app.handle(),
        &state.providers,
        "wiki-1-1",
        &provider_id,
        &[ChatMessage {
            role: "user".into(),
            content: "先验概率是什么？".into(),
            ..Default::default()
        }],
        4096,
    )
    .unwrap();
    assert_eq!(result.status, "done");
    assert_eq!(server.request_count(), 1);
    recover(app.handle(), &state).unwrap();
    assert_eq!(get(&state, 1).unwrap().status, "interrupted");
    resume(app.handle(), &state, 1).unwrap();
    let restored = wait(&state, 1);
    assert_eq!(restored.status, "done");
    assert_eq!(restored.attempt, 1);
    assert_eq!(
        restored.answer.as_deref(),
        Some("先验概率是在看到新证据之前的判断。")
    );
    assert_eq!(server.request_count(), 1);
}
