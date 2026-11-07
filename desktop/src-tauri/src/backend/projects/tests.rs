use super::*;
use crate::backend::providers::test_support::{response, server, start_stream, TestServer};
use crate::backend::providers::{self, ProfileInput};
use crate::backend::{security::SecurityState, BackendState};
use serde_json::{json, Value};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::{Duration, Instant};
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};

fn setup(
    gate: Option<Arc<AtomicBool>>,
    invalid: bool,
) -> (
    tauri::App<tauri::test::MockRuntime>,
    ProjectState,
    LearningState,
    TestServer,
) {
    let learning = LearningState::for_tests().unwrap();
    let path = learning.database_path().to_path_buf();
    let providers = learning.provider_state();
    let model = server(move |_request, stream| {
        if let Some(gate) = &gate {
            start_stream(stream);
            let gate_deadline = Instant::now() + Duration::from_secs(5);
            while !gate.load(Ordering::Acquire) {
                assert!(Instant::now() < gate_deadline, "测试门未在 5 秒内释放，可能前置断言已失败");
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        let body = if invalid {
            json!({"concept":200,"implementation":76,"verification":64,"summary":"超出范围的分数","suggestions":["不能采用"]})
        } else {
            json!({"concept":83,"implementation":76,"verification":64,"summary":"基于真实提交的模型评价","suggestions":["补充对照实验","记录误判样本"]})
        };
        let event =
            serde_json::to_string(&json!({"choices":[{"delta":{"content":body.to_string()}}]}))
                .unwrap();
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":21,\"completion_tokens\":34}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"));
    });
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                &path,
                &providers,
                &learning.material_state(),
            ),
            community: crate::backend::community::CommunityState::new(&path),
            plugins: crate::backend::plugins::PluginState::new(&path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .manage(SecurityState::for_tests_at(&path))
        .invoke_handler(tauri::generate_handler![
            commands::get_project_workspace,
            commands::submit_project_version,
            commands::cancel_project_feedback,
            commands::resume_project_feedback,
            commands::save_project_iteration_note,
            crate::backend::commands::events_since,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let profile = providers::upsert(
        app.handle(),
        &providers,
        ProfileInput {
            id: None,
            name: "项目反馈测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: model.url(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("project-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &providers, &profile.id).unwrap();
    let state = ProjectState::for_tests(&path, &providers);
    (app, state, learning, model)
}

fn ipc(
    app: &tauri::App<tauri::test::MockRuntime>,
    command: &str,
    arguments: Value,
) -> std::result::Result<Value, Value> {
    tauri::test::get_ipc_response(
        &app.get_webview_window("main").unwrap(),
        InvokeRequest {
            cmd: command.into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: InvokeBody::Json(arguments),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .map(|body| body.deserialize::<Value>().unwrap())
}

fn seed_project(state: &ProjectState) {
    state.store().unwrap().replace_collection("projects", 0, &json!([{
        "id":1,"title":"真实项目","description":"验证概率应用与证据","due":"2099-01-01","progress":50,"tag":"实践",
        "milestones":[{"id":1,"title":"实现原型","done":true},{"id":2,"title":"验证与复盘","done":false}]
    }])).unwrap();
}

fn submission(material_id: Option<i64>) -> SubmissionInput {
    SubmissionInput {
        project_id: 1,
        content: "真实原型、测试记录与误差分析".into(),
        milestone_id: Some(2),
        material_id,
    }
}

fn wait_version(state: &ProjectState, id: i64) -> ProjectVersion {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let current = version(&state.store().unwrap().conn, id).unwrap();
        if !["running", "cancelling"].contains(&current.status.as_str()) {
            return current;
        }
        assert!(Instant::now() < deadline, "项目反馈未在限时内进入终态");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn real_feedback_versions_notes_replay_and_backup_use_actual_results() {
    let (app, state, learning, model) = setup(None, false);
    seed_project(&state);
    let material = crate::backend::materials::import_text(
        app.handle(),
        &learning.material_state(),
        "真实项目资料".into(),
        "项目评估的真实原文".into(),
    )
    .unwrap();
    let material_id = material.material.id;
    crate::backend::materials::enqueue(app.handle(), &learning.material_state(), material_id)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while crate::backend::materials::detail(&learning.material_state(), material_id)
        .unwrap()
        .material
        .status
        != "parsed"
    {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let started = ipc(
        &app,
        "submit_project_version",
        json!({"input":submission(Some(material_id))}),
    )
    .unwrap();
    let id = started["versions"][0]["id"].as_i64().unwrap();
    let completed = wait_version(&state, id);
    assert_eq!(completed.status, "done");
    assert_eq!(completed.version_number, 1);
    assert_eq!(completed.milestone_title.as_deref(), Some("验证与复盘"));
    assert_eq!(completed.material_id, Some(material_id));
    let feedback = completed.feedback.unwrap();
    assert_eq!(
        (
            feedback.concept,
            feedback.implementation,
            feedback.verification
        ),
        (83, 76, 64)
    );
    assert_eq!(feedback.summary, "基于真实提交的模型评价");
    assert_eq!(feedback.suggestions.len(), 2);
    assert_eq!(model.request_count(), 1);
    let note = ipc(
        &app,
        "save_project_iteration_note",
        json!({"input":{"versionId":id,"content":"下一版补充真实对照实验"}}),
    )
    .unwrap();
    assert_eq!(note["notes"][0]["title"], "真实项目 · v1 迭代笔记");
    assert_eq!(note["notes"][0]["content"], "下一版补充真实对照实验");
    let events = ipc(&app, "events_since", json!({"lastId":0})).unwrap()["events"]
        .as_array()
        .unwrap()
        .clone();
    assert_eq!(events.last().unwrap()["kind"], "state.changed");
    assert!(events
        .iter()
        .any(|event| event["kind"] == "project.feedback_completed"
            && event["payload"]["feedback"]["concept"] == 83));
    let replayed = ipc(&app, "resume_project_feedback", json!({"id":id})).unwrap();
    assert_eq!(replayed["versions"][0]["status"], "done");
    assert_eq!(model.request_count(), 1);
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("project.dtbackup");
    let source_store = learning.store().unwrap();
    crate::backend::backup::export(app.handle(), &source_store, &learning, &path).unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_store = target.store().unwrap();
    crate::backend::backup::import(app.handle(), &target_store, &target, &path).unwrap();
    let restored = workspace(&ProjectState::new(&target)).unwrap();
    assert_eq!(restored.versions.len(), 1);
    assert_eq!(restored.versions[0].content, "真实原型、测试记录与误差分析");
    assert_eq!(restored.versions[0].feedback.as_ref().unwrap().concept, 83);
    assert_eq!(restored.versions[0].material_id, Some(material_id));
}

#[test]
fn cancellation_resume_and_invalid_feedback_never_fabricate_evaluation() {
    let gate = Arc::new(AtomicBool::new(false));
    let (app, state, _learning, model) = setup(Some(gate.clone()), false);
    seed_project(&state);
    let started = ipc(
        &app,
        "submit_project_version",
        json!({"input":submission(None)}),
    )
    .unwrap();
    let id = started["versions"][0]["id"].as_i64().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while model.request_count() == 0 {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    ipc(&app, "cancel_project_feedback", json!({"id":id})).unwrap();
    gate.store(true, Ordering::Release);
    let cancelled = wait_version(&state, id);
    assert_eq!(cancelled.status, "cancelled");
    assert!(cancelled.feedback.is_none());
    let resumed = ipc(&app, "resume_project_feedback", json!({"id":id})).unwrap();
    assert_eq!(resumed["versions"][0]["attempt"], 2);
    assert_ne!(
        resumed["versions"][0]["requestId"].as_str(),
        cancelled.request_id.as_deref()
    );
    let completed = wait_version(&state, id);
    assert_eq!(completed.status, "done");
    assert_eq!(model.request_count(), 2);
    assert_eq!(
        ipc(&app, "cancel_project_feedback", json!({"id":id})).unwrap_err()["code"],
        "project_feedback_not_active"
    );
    let (app, state, _learning, _model) = setup(None, true);
    seed_project(&state);
    let started = ipc(
        &app,
        "submit_project_version",
        json!({"input":submission(None)}),
    )
    .unwrap();
    let id = started["versions"][0]["id"].as_i64().unwrap();
    let failed = wait_version(&state, id);
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.error.unwrap().code, "invalid_model_output");
    assert!(failed.feedback.is_none());
    assert_eq!(
        ipc(
            &app,
            "save_project_iteration_note",
            json!({"input":{"versionId":id,"content":"不能伪造评价"}})
        )
        .unwrap_err()["code"],
        "project_feedback_not_ready"
    );
    state.store().unwrap().conn.execute("UPDATE project_versions SET status = 'running', request_id = NULL, completed_at = NULL WHERE id = ?1", params![id]).unwrap();
    recover(app.handle(), &state).unwrap();
    assert_eq!(
        version(&state.store().unwrap().conn, id).unwrap().status,
        "interrupted"
    );
}
