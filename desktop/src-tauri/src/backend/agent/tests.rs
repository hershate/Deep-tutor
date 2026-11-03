use super::super::learning::{self, PlanInput};
use super::super::materials;
use super::super::providers::test_support::{response, server, TestServer};
use super::super::providers::{self, ProfileInput};
use super::super::BackendState;
use super::*;
use serde_json::json;
use std::time::Duration;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, AgentState) {
    let state = AgentState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: state.learning.material_state().shared(),
            providers: state.providers.shared(),
            learning: state.learning.shared(),
            agent: state.shared(),
            practice: crate::backend::practice::PracticeState::for_tests().unwrap(),
            review: crate::backend::review::ReviewState::for_tests().unwrap(),
            knowledge: crate::backend::knowledge::KnowledgeState::for_tests().unwrap(),
            community: crate::backend::community::CommunityState::for_tests().unwrap(),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::create_agent_session,
            super::commands::list_agent_sessions,
            super::commands::get_agent_session,
            super::commands::send_agent_message,
            super::commands::get_agent_run,
            super::commands::cancel_agent_run,
            super::commands::resume_agent_run,
            super::commands::delete_agent_session,
            super::commands::approve_agent_request,
            super::commands::cancel_agent_approval,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
}

fn add_material(app: &tauri::App<tauri::test::MockRuntime>, state: &AgentState) -> i64 {
    let path = state.database_path.parent().unwrap().join("chapter.txt");
    std::fs::write(
        &path,
        "概率 条件概率 独立性 贝叶斯定理 先验 似然 后验 分类 模型评估。每个概念都有教材原文。",
    )
    .unwrap();
    let imported = materials::import_files(
        app.handle(),
        &state.learning.material_state(),
        vec![path.to_string_lossy().into_owned()],
    );
    let id = imported[0].material.as_ref().unwrap().id;
    materials::enqueue(app.handle(), &state.learning.material_state(), id).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let detail = materials::detail(&state.learning.material_state(), id).unwrap();
        if detail.material.status == "parsed" {
            return id;
        }
        assert_ne!(detail.material.status, "failed");
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn configure_provider(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &AgentState,
    url: &str,
) -> String {
    let profile = providers::upsert(
        app.handle(),
        &state.providers,
        ProfileInput {
            id: None,
            name: "Agent 测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: url.into(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("agent-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &state.providers, &profile.id).unwrap();
    profile.id
}

fn model_server(material_id: i64) -> TestServer {
    server(move |request, stream| {
        let body = if request.contains("\"role\":\"tool\"") {
            json!("工具结果已确认")
        } else if request.contains("自学讲义") {
            json!({
                "title": "原生讲义", "overview": "真实讲义概述",
                "sections": [{"heading": "真实概念", "content": "依据资料生成的讲义正文"}],
                "examples": [{"title": "真实例题", "content": "例题与解答"}],
                "checkpoints": [{"question": "检查问题", "answer": "检查答案"}],
                "citations": [{"materialId": material_id, "quote": "概率"}],
            })
        } else if request.contains("构建知识图谱") {
            let concepts = [
                ("prob", "概率"),
                ("cond", "条件概率"),
                ("indep", "独立性"),
                ("bayes", "贝叶斯定理"),
                ("prior", "先验"),
                ("likelihood", "似然"),
                ("posterior", "后验"),
                ("classify", "分类"),
                ("evaluation", "模型评估"),
            ];
            let nodes = concepts
                .iter()
                .enumerate()
                .map(|(index, (id, title))| {
                    json!({
                        "id": id, "title": title, "summary": format!("依据教材学习{title}"),
                        "difficulty": index / 3 + 1, "required": true, "estimatedMinutes": 15,
                        "citations": [{"materialId": material_id, "quote": title}],
                    })
                })
                .collect::<Vec<_>>();
            json!({
                "nodes": nodes,
                "edges": [
                    {"from": "prob", "to": "bayes"}, {"from": "cond", "to": "bayes"},
                    {"from": "indep", "to": "likelihood"}, {"from": "bayes", "to": "posterior"},
                    {"from": "prior", "to": "classify"}, {"from": "likelihood", "to": "classify"},
                ],
            })
        } else if request.contains("设计渐进学习阶段") {
            json!({"stages": [
                {"id": "stage-1", "title": "概率基础", "nodeIds": ["prob", "cond", "indep"], "difficulty": 1},
                {"id": "stage-2", "title": "贝叶斯更新", "nodeIds": ["bayes", "prior", "likelihood"], "difficulty": 2},
                {"id": "stage-3", "title": "分类与评估", "nodeIds": ["posterior", "classify", "evaluation"], "difficulty": 3},
            ]})
        } else if request.contains("校验这份学习方案") {
            let check = || json!({"status": "pass", "details": ""});
            json!({"report": {
                "dependency_closure": check(), "difficulty_step": check(), "cognitive_load": check(),
                "milestone_feasibility": check(), "gap_analysis": check(),
            }, "passed": true, "fixes": []})
        } else {
            json!({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call-read", "type": "function",
                "function": {"name": "workspace.read", "arguments": "{\"reference\":\"current\"}"},
            }]}}]})
        };
        let event = if body.pointer("/choices/0/delta").is_some() {
            body.to_string()
        } else {
            serde_json::to_string(&json!({"choices": [{"delta": {"content": body.as_str().map(str::to_owned).unwrap_or_else(|| body.to_string())}}]}))
                .unwrap()
        };
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":12,\"completion_tokens\":33}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"));
    })
}

fn wait_run(state: &AgentState, id: &str) -> AgentRun {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let run = get_run(state, numeric_id(id).unwrap()).unwrap();
        if !["running", "cancelling"].contains(&run.status.as_str()) {
            return run;
        }
        assert!(Instant::now() < deadline, "Agent 运行未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn prepare() -> (
    tauri::App<tauri::test::MockRuntime>,
    AgentState,
    TestServer,
    AgentSession,
) {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id);
    configure_provider(&app, &state, &server.url());
    let started = learning::start(
        app.handle(),
        &state.learning,
        PlanInput {
            goal: "掌握贝叶斯定理并应用于分类".into(),
            level: "了解基础概率，不熟悉贝叶斯应用".into(),
            daily_minutes: 45,
            deadline: "2099-01-01".into(),
            material_ids: vec![material_id],
            provider_id: None,
            milestone: None,
            milestone_date: None,
        },
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let plan = learning::get(&state.learning, &started.id).unwrap();
        if !["running", "cancelling"].contains(&plan.status.as_str()) {
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    learning::actions::adopt(app.handle(), &state.learning, &started.id).unwrap();
    let session = new_session(app.handle(), &state, None, Some(started.id), Some(16_000)).unwrap();
    (app, state, server, session)
}

#[test]
fn agent_executes_tools_and_returns_real_model_output() {
    let (app, state, server, session) = prepare();
    let run = send(
        app.handle(),
        &state,
        &session.id.to_string(),
        "读取当前方案".into(),
    )
    .unwrap();
    let completed = wait_run(&state, &run.id.to_string());
    assert_eq!(completed.status, "done");
    assert_eq!(completed.steps, 2);
    assert_eq!(completed.input_tokens, 24);
    assert_eq!(completed.output_tokens, 66);
    let session = get(&state, &session.id.to_string()).unwrap();
    assert_eq!(session.messages.len(), 4);
    assert_eq!(session.messages[0].role, "user");
    assert_eq!(session.messages[1].tool_calls[0].name, "workspace.read");
    assert_eq!(session.messages[2].role, "tool");
    assert_eq!(session.messages[3].content, "工具结果已确认");
    assert!(session.messages[2].content.contains("plan-1"));
    let calls: i64 = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM agent_tool_calls WHERE id = 'call-read' AND status = 'done'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(calls, 1);
    assert_eq!(server.request_count(), 5);
    let sessions = list(&state).unwrap();
    assert_eq!(sessions.len(), 1);
    assert_eq!(sessions[0].title, "读取当前方案");
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

fn call(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &AgentState,
    run_id: i64,
    session: &AgentSession,
    id: &str,
    name: &str,
    arguments: Value,
) -> Value {
    tools::execute(
        app.handle(),
        state,
        run_id,
        session,
        &ToolCall {
            id: id.into(),
            name: name.into(),
            arguments: arguments.to_string(),
        },
        1,
    )
    .unwrap_or_else(|error| panic!("工具 {name} 执行失败：{error}"))
}

fn deferred_run(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &AgentState,
    session: &AgentSession,
) -> AgentRun {
    state.defer.store(true, Ordering::Release);
    send(
        app.handle(),
        state,
        &session.id.to_string(),
        "执行真实工具测试".into(),
    )
    .unwrap()
}

#[test]
fn ipc_commands_enforce_single_run_and_recover_a_session() {
    let (app, state, _server, session) = prepare();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    state.defer.store(true, Ordering::Release);
    let started = ipc(
        &window,
        "send_agent_message",
        json!({"sessionId": session.id.to_string(), "content": "从 IPC 读取方案"}),
    )
    .unwrap();
    let run_id = started["id"].as_i64().unwrap();
    let duplicate = ipc(
        &window,
        "send_agent_message",
        json!({"sessionId": session.id.to_string(), "content": "不能并发执行"}),
    )
    .unwrap_err();
    assert_eq!(duplicate["code"], "run_active");
    ipc(
        &window,
        "cancel_agent_run",
        json!({"id": run_id.to_string()}),
    )
    .unwrap();
    recover(app.handle(), &state).unwrap();
    state.active.lock().unwrap().clear();
    state.defer.store(false, Ordering::Release);
    ipc(
        &window,
        "resume_agent_run",
        json!({"id": run_id.to_string()}),
    )
    .unwrap();
    assert_eq!(wait_run(&state, &run_id.to_string()).status, "done");
    let fetched = ipc(
        &window,
        "get_agent_session",
        json!({"id": session.id.to_string()}),
    )
    .unwrap();
    assert_eq!(fetched["messages"].as_array().unwrap().len(), 4);
    assert_eq!(fetched["messages"][2]["role"], "tool");
    let run = ipc(&window, "get_agent_run", json!({"id": run_id.to_string()})).unwrap();
    assert_eq!(run["inputTokens"], 24);
    assert_eq!(run["outputTokens"], 66);
    let sessions = ipc(&window, "list_agent_sessions", json!({})).unwrap();
    assert_eq!(sessions.as_array().unwrap().len(), 1);
    ipc(
        &window,
        "delete_agent_session",
        json!({"id": session.id.to_string()}),
    )
    .unwrap();
    assert_eq!(
        ipc(
            &window,
            "get_agent_session",
            json!({"id": session.id.to_string()}),
        )
        .unwrap_err()["code"],
        "not_found"
    );
}

#[test]
fn memory_requires_approval_and_supports_recall_delete_and_cancel() {
    let (app, state, _server, session) = prepare();
    let run = deferred_run(&app, &state, &session);
    let saved = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-save-cancelled",
        "memory.save",
        json!({"key": "learning-style", "value": "先提示再作答"}),
    );
    assert_eq!(saved["status"], "approval_required");
    tools::cancel_approval(
        app.handle(),
        &state,
        &saved["approvalId"].as_i64().unwrap().to_string(),
    )
    .unwrap();
    assert!(get(&state, &session.id.to_string())
        .unwrap()
        .memory
        .is_empty());
    let saved = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-save",
        "memory.save",
        json!({"key": "learning-style", "value": "先提示再作答"}),
    );
    let repeated = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-save",
        "memory.save",
        json!({"key": "learning-style", "value": "先提示再作答"}),
    );
    assert_eq!(repeated, saved);
    let approval_id = saved["approvalId"].as_i64().unwrap().to_string();
    assert!(get(&state, &session.id.to_string())
        .unwrap()
        .memory
        .is_empty());
    tools::approve(app.handle(), &state, &approval_id).unwrap();
    assert_eq!(
        tools::approve(app.handle(), &state, &approval_id)
            .unwrap_err()
            .code,
        "approval_resolved"
    );
    let recalled = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-recall",
        "memory.recall",
        json!({"query": "提示"}),
    );
    assert_eq!(recalled["entries"][0]["value"], "先提示再作答");
    let literal_wildcard = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-recall-wildcard",
        "memory.recall",
        json!({"query": "%"}),
    );
    assert_eq!(literal_wildcard["entries"], json!([]));
    let deleted = call(
        &app,
        &state,
        run.id,
        &session,
        "memory-delete",
        "memory.delete",
        json!({"key": "learning-style"}),
    );
    tools::approve(
        app.handle(),
        &state,
        &deleted["approvalId"].as_i64().unwrap().to_string(),
    )
    .unwrap();
    assert!(get(&state, &session.id.to_string())
        .unwrap()
        .memory
        .is_empty());
}

#[test]
fn scratch_edits_preserve_lecture_versions_and_block_unsafe_paths() {
    let (app, state, _server, session) = prepare();
    learning::lecture::generate(
        app.handle(),
        &state.learning,
        &session.plan_id,
        "stage-1",
        false,
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    let original = loop {
        let lecture =
            learning::lecture::get(&state.learning, &session.plan_id, "stage-1", None).unwrap();
        if !["running", "cancelling"].contains(&lecture.status.as_str()) {
            break lecture;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    };
    assert_eq!(original.status, "done");
    let run = deferred_run(&app, &state, &session);
    let unit = serde_json::to_value(original.units[0].content.as_ref().unwrap()).unwrap();
    let written = call(
        &app,
        &state,
        run.id,
        &session,
        "artifact-write",
        "workspace.write",
        json!({"path": "notes/unit.json", "stageId": "stage-1", "unitIndex": 0, "content": unit.clone()}),
    );
    assert_eq!(written["status"], "committed");
    let artifact_id = written["artifactId"].as_i64().unwrap();
    let saved = get(&state, &session.id.to_string()).unwrap();
    assert_eq!(saved.artifacts[0].content["title"], "原生讲义");
    assert!(saved.artifacts[0].created_at.contains('T'));
    assert!(saved.artifacts[0].expires_at.contains('T'));
    let edited = call(
        &app,
        &state,
        run.id,
        &session,
        "artifact-edit",
        "workspace.edit",
        json!({"artifactId": artifact_id, "oldText": "依据资料生成的讲义正文", "newText": "Agent 修改后的讲义正文"}),
    );
    assert_eq!(edited["status"], "committed");
    let replayed = call(
        &app,
        &state,
        run.id,
        &session,
        "artifact-edit",
        "workspace.edit",
        json!({"artifactId": artifact_id, "oldText": "依据资料生成的讲义正文", "newText": "Agent 修改后的讲义正文"}),
    );
    assert_eq!(replayed, edited);
    let expected: String = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT sha256 FROM agent_artifacts WHERE id = ?1",
            params![artifact_id],
            |row| row.get(0),
        )
        .unwrap();
    let bytes = std::fs::read(
        state
            .database_path
            .parent()
            .unwrap()
            .join("agent-workspaces")
            .join(session.id.to_string())
            .join("scratch")
            .join("notes/unit.json"),
    )
    .unwrap();
    assert_eq!(
        format!("{:x}", <sha2::Sha256 as sha2::Digest>::digest(&bytes)),
        expected
    );
    let promotion = call(
        &app,
        &state,
        run.id,
        &session,
        "artifact-promote",
        "artifact.promote",
        json!({"artifactId": artifact_id}),
    );
    tools::approve(
        app.handle(),
        &state,
        &promotion["approvalId"].as_i64().unwrap().to_string(),
    )
    .unwrap();
    let updated =
        learning::lecture::get(&state.learning, &session.plan_id, "stage-1", None).unwrap();
    assert_eq!(updated.version, 2);
    assert_eq!(
        updated.units[0].content.as_ref().unwrap().sections[0].content,
        "Agent 修改后的讲义正文"
    );
    let old =
        learning::lecture::get(&state.learning, &session.plan_id, "stage-1", Some(1)).unwrap();
    assert_eq!(
        old.units[0].content.as_ref().unwrap().sections[0].content,
        "依据资料生成的讲义正文"
    );
    for (index, path) in ["../escape.json", "C:/escape.json"].into_iter().enumerate() {
        let rejected = call(
            &app,
            &state,
            run.id,
            &session,
            &format!("unsafe-path-{index}"),
            "workspace.write",
            json!({"path": path, "stageId": "stage-1", "unitIndex": 0, "content": unit.clone()}),
        );
        assert_eq!(rejected["error"]["code"], "invalid_artifact_path");
    }
    let external = call(
        &app,
        &state,
        run.id,
        &session,
        "external-artifact",
        "workspace.write",
        json!({"path": "external.json", "stageId": "stage-1", "unitIndex": 1, "content": serde_json::to_value(original.units[1].content.as_ref().unwrap()).unwrap()}),
    );
    let root = state
        .database_path
        .parent()
        .unwrap()
        .join("agent-workspaces")
        .join(session.id.to_string())
        .join("scratch");
    std::fs::write(root.join("external.json"), "{}").unwrap();
    let modified = call(
        &app,
        &state,
        run.id,
        &session,
        "external-modified",
        "artifact.promote",
        json!({"artifactId": external["artifactId"]}),
    );
    assert_eq!(modified["error"]["code"], "artifact_modified_externally");
    let late = call(
        &app,
        &state,
        run.id,
        &session,
        "late-artifact",
        "workspace.write",
        json!({"path": "late.json", "stageId": "stage-1", "unitIndex": 2, "content": serde_json::to_value(original.units[2].content.as_ref().unwrap()).unwrap()}),
    );
    let pending = call(
        &app,
        &state,
        run.id,
        &session,
        "late-promote",
        "artifact.promote",
        json!({"artifactId": late["artifactId"]}),
    );
    let pending_id = pending["approvalId"].as_i64().unwrap().to_string();
    let approval = get(&state, &session.id.to_string())
        .unwrap()
        .approvals
        .into_iter()
        .find(|approval| approval.id.to_string() == pending_id)
        .unwrap();
    assert_eq!(approval.payload["sha256"].as_str().unwrap().len(), 64);
    std::fs::write(root.join("late.json"), "{}").unwrap();
    assert_eq!(
        tools::approve(app.handle(), &state, &pending_id)
            .unwrap_err()
            .code,
        "artifact_modified_externally"
    );
    tools::cancel_approval(app.handle(), &state, &pending_id).unwrap();
    let expired = call(
        &app,
        &state,
        run.id,
        &session,
        "expired-artifact",
        "workspace.write",
        json!({"path": "expired.json", "stageId": "stage-1", "unitIndex": 0, "content": unit.clone()}),
    );
    state
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE agent_artifacts SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?1",
            params![expired["artifactId"].as_i64().unwrap()],
        )
        .unwrap();
    let expired_approval = call(
        &app,
        &state,
        run.id,
        &session,
        "expired-promote",
        "artifact.promote",
        json!({"artifactId": expired["artifactId"]}),
    );
    let expired_id = expired_approval["approvalId"].as_i64().unwrap().to_string();
    assert_eq!(
        tools::approve(app.handle(), &state, &expired_id)
            .unwrap_err()
            .code,
        "approval_expired"
    );
    assert_eq!(
        tools::approve(app.handle(), &state, &expired_id)
            .unwrap_err()
            .code,
        "approval_resolved"
    );
    assert_eq!(
        learning::lecture::get(&state.learning, &session.plan_id, "stage-1", None)
            .unwrap()
            .version,
        2
    );
    #[cfg(windows)]
    {
        let outside = root.join("outside.json");
        let link = root.join("linked.json");
        std::fs::write(&outside, serde_json::to_vec_pretty(&unit).unwrap()).unwrap();
        if std::os::windows::fs::symlink_file(&outside, &link).is_ok() {
            let rejected = call(
                &app,
                &state,
                run.id,
                &session,
                "symlink-artifact",
                "workspace.write",
                json!({"path": "linked.json", "stageId": "stage-1", "unitIndex": 0, "content": unit, "overwrite": true}),
            );
            assert_eq!(rejected["error"]["code"], "artifact_path_blocked");
        }
    }
}

#[test]
fn cancelling_a_stream_preserves_partial_output_and_retries_with_a_new_request() {
    let (app, state, _baseline, session) = prepare();
    let attempts = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let server_attempts = Arc::clone(&attempts);
    let agent_server = server(move |request, stream| {
        if !request.contains("\"tools\"") {
            return response(stream, "400 Bad Request", "unexpected plan request");
        }
        if server_attempts.fetch_add(1, Ordering::AcqRel) == 0 {
            super::super::providers::test_support::start_stream(stream);
            std::io::Write::write_all(
                stream,
                "data: {\"choices\":[{\"delta\":{\"content\":\"部分回答\"}}]}\n\n".as_bytes(),
            )
            .unwrap();
            std::io::Write::flush(stream).unwrap();
            std::thread::sleep(Duration::from_millis(300));
            let _ = std::io::Write::write_all(stream, b"data: [DONE]\n\n");
        } else {
            response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"content\":\"恢复后回答\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":12,\"completion_tokens\":33},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
        }
    });
    let provider_id = configure_provider(&app, &state, &agent_server.url());
    state
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE agent_sessions SET provider_id = ?1 WHERE id = ?2",
            params![provider_id, session.id],
        )
        .unwrap();
    let session = get(&state, &session.id.to_string()).unwrap();
    let run = send(
        app.handle(),
        &state,
        &session.id.to_string(),
        "测试真实取消".into(),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    let request_id = loop {
        let current = get_run(&state, run.id).unwrap();
        if let Some(request_id) = current.current_request_id {
            if providers::get_chat(&state.providers, &request_id)
                .is_ok_and(|request| request.status == "running")
            {
                break request_id;
            }
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    };
    loop {
        let received: bool = state
            .store()
            .unwrap()
            .conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM events WHERE kind = 'provider.chat_delta' AND json_extract(payload, '$.requestId') = ?1)",
                params![request_id],
                |row| row.get(0),
            )
            .unwrap();
        if received {
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    cancel(app.handle(), &state, &run.id.to_string()).unwrap();
    let cancelled = wait_run(&state, &run.id.to_string());
    assert_eq!(cancelled.status, "cancelled");
    assert_eq!(
        providers::get_chat(&state.providers, &request_id)
            .unwrap()
            .output,
        "部分回答"
    );
    resume(app.handle(), &state, &run.id.to_string()).unwrap();
    let completed = wait_run(&state, &run.id.to_string());
    assert_eq!(completed.status, "done");
    assert_ne!(completed.current_request_id.unwrap(), request_id);
    assert_eq!(completed.input_tokens, 12);
    assert_eq!(completed.output_tokens, 33);
    assert_eq!(attempts.load(Ordering::Acquire), 2);
    let messages = get(&state, &session.id.to_string()).unwrap().messages;
    assert!(messages
        .iter()
        .any(|message| message.partial && message.content == "部分回答"));
    assert_eq!(messages.last().unwrap().content, "恢复后回答");
}

#[test]
fn completed_provider_output_is_reused_without_repeating_a_tool_or_request() {
    let (app, state, server, session) = prepare();
    let run = deferred_run(&app, &state, &session);
    let request_id = format!("agent-{}-1-1", run.id);
    let result = providers::stream_chat_with_tools(
        app.handle(),
        &state.providers,
        &request_id,
        &session.provider_id,
        &[ChatMessage {
            role: "user".into(),
            content: "读取当前方案".into(),
            ..Default::default()
        }],
        4096,
        Some(&tools::catalog()),
    )
    .unwrap();
    assert_eq!(result.status, "done");
    let store = state.store().unwrap();
    store
        .conn
        .execute(
            "UPDATE agent_runs SET status = 'interrupted', steps = 1, current_request_id = ?1 WHERE id = ?2",
            params![request_id, run.id],
        )
        .unwrap();
    drop(store);
    state.active.lock().unwrap().clear();
    state.defer.store(false, Ordering::Release);
    resume(app.handle(), &state, &run.id.to_string()).unwrap();
    let completed = wait_run(&state, &run.id.to_string());
    assert_eq!(completed.status, "done");
    assert_eq!(completed.steps, 2);
    assert_eq!(completed.input_tokens, 24);
    assert_eq!(completed.output_tokens, 66);
    assert_eq!(server.request_count(), 5);
    let calls: i64 = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM agent_tool_calls WHERE id = 'call-read' AND status = 'done'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(calls, 1);
}

#[test]
fn token_budget_stops_a_completed_response_before_another_request() {
    let (app, state, _baseline, session) = prepare();
    let agent_server = server(|_request, stream| {
        response(stream, "200 OK", "data: {\"choices\":[{\"delta\":{\"content\":\"预算输出\"}}]}\n\ndata: {\"usage\":{\"prompt_tokens\":12,\"completion_tokens\":1024},\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
    });
    configure_provider(&app, &state, &agent_server.url());
    let session = new_session(
        app.handle(),
        &state,
        Some("预算测试".into()),
        Some(session.plan_id),
        Some(1024),
    )
    .unwrap();
    let run = send(
        app.handle(),
        &state,
        &session.id.to_string(),
        "触发预算上限".into(),
    )
    .unwrap();
    let completed = wait_run(&state, &run.id.to_string());
    assert_eq!(completed.status, "failed");
    assert_eq!(completed.error.unwrap().code, "budget_exceeded");
    assert_eq!(completed.output_tokens, 1024);
    assert_eq!(agent_server.request_count(), 1);
    assert_eq!(
        get(&state, &session.id.to_string())
            .unwrap()
            .messages
            .last()
            .unwrap()
            .content,
        "预算输出"
    );
    assert_eq!(
        get(&state, &session.id.to_string())
            .unwrap()
            .output_tokens_used,
        1024
    );
    assert_eq!(
        send(
            app.handle(),
            &state,
            &session.id.to_string(),
            "不能绕过会话预算".into(),
        )
        .unwrap_err()
        .code,
        "budget_exceeded"
    );
    assert_eq!(agent_server.request_count(), 1);
    assert_eq!(get(&state, &session.id.to_string()).unwrap().runs.len(), 1);
}

#[test]
fn tool_loop_stops_at_twenty_five_real_model_steps() {
    let (app, state, _baseline, session) = prepare();
    let attempts = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let server_attempts = Arc::clone(&attempts);
    let agent_server = server(move |_request, stream| {
        let step = server_attempts.fetch_add(1, Ordering::AcqRel);
        let event = json!({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": format!("history-{step}"), "type": "function",
            "function": {"name": "history.load", "arguments": "{\"limit\":1}"},
        }]}}]});
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":12,\"completion_tokens\":33}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"tool_calls\"}}]}}\n\ndata: [DONE]\n\n"));
    });
    configure_provider(&app, &state, &agent_server.url());
    let session = new_session(
        app.handle(),
        &state,
        Some("步数测试".into()),
        Some(session.plan_id),
        Some(16_000),
    )
    .unwrap();
    let run = send(
        app.handle(),
        &state,
        &session.id.to_string(),
        "持续读取历史".into(),
    )
    .unwrap();
    let completed = wait_run(&state, &run.id.to_string());
    assert_eq!(completed.status, "failed");
    assert_eq!(completed.error.unwrap().code, "step_limit");
    assert_eq!(completed.steps, 25);
    assert_eq!(agent_server.request_count(), 25);
    let saved = get(&state, &session.id.to_string()).unwrap();
    assert_eq!(saved.messages.len(), 51);
    assert_eq!(completed.input_tokens, 300);
    assert_eq!(completed.output_tokens, 825);
}
