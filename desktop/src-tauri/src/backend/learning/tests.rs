use super::super::providers::test_support::{response, server, start_stream, TestServer};
use super::super::providers::{self, ChatMessage, ProfileInput};
use super::super::BackendState;
use super::*;
use serde_json::json;
use std::time::Duration;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, LearningState) {
    let learning = LearningState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.materials.shared(),
            providers: learning.providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::for_tests().unwrap(),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::start_learning_plan,
            super::commands::list_learning_plans,
            super::commands::get_learning_plan,
            super::commands::cancel_learning_plan,
            super::commands::resume_learning_plan,
            super::commands::adopt_learning_plan,
            super::commands::regenerate_learning_plan,
            super::commands::get_knowledge_overview,
            super::commands::get_knowledge_path,
            super::commands::add_knowledge_task,
            super::commands::get_reading_progress,
            super::commands::set_reading_progress,
            super::commands::get_lecture,
            super::commands::generate_lecture,
            super::commands::cancel_lecture,
            super::commands::save_study_note,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, learning)
}

fn input(material_id: i64) -> PlanInput {
    let mut deadline = validate::today();
    for _ in 0..5 {
        deadline = deadline.next_day().unwrap();
    }
    PlanInput {
        goal: "掌握贝叶斯定理并应用于分类".into(),
        level: "了解基础概率，不熟悉贝叶斯应用".into(),
        daily_minutes: 45,
        deadline: deadline.to_string(),
        material_ids: vec![material_id],
        provider_id: None,
        milestone: Some("完成一次分类练习".into()),
        milestone_date: None,
    }
}

fn add_material(app: &tauri::App<tauri::test::MockRuntime>, state: &LearningState) -> i64 {
    let path = state._guard.as_ref().unwrap().path().join("chapter.txt");
    std::fs::write(
        &path,
        "概率 条件概率 独立性 贝叶斯定理 先验 似然 后验 分类 模型评估。每个概念都有教材原文。",
    )
    .unwrap();
    let imported = super::super::materials::import_files(
        app.handle(),
        &state.materials,
        vec![path.to_string_lossy().into_owned()],
    );
    let id = imported[0].material.as_ref().unwrap().id;
    super::super::materials::enqueue(app.handle(), &state.materials, id).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let detail = super::super::materials::detail(&state.materials, id).unwrap();
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
    state: &LearningState,
    url: &str,
) -> String {
    let profile = providers::upsert(
        app.handle(),
        &state.providers,
        ProfileInput {
            id: None,
            name: "学习生成测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: url.into(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("learning-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &state.providers, &profile.id).unwrap();
    profile.id
}

fn model_server(material_id: i64, cyclic: bool) -> TestServer {
    server(move |request, stream| {
        let body = if request.contains("自学讲义") {
            json!({
                "title": "原生讲义",
                "overview": "由真实协议响应返回的概念概述",
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
            let mut edges = vec![
                ("prob", "bayes"),
                ("cond", "bayes"),
                ("indep", "likelihood"),
                ("bayes", "posterior"),
                ("prior", "classify"),
                ("likelihood", "classify"),
            ];
            if cyclic {
                edges.push(("evaluation", "prob"));
                edges.push(("posterior", "evaluation"));
            }
            json!({
                "nodes": nodes,
                "edges": edges.into_iter().map(|(from, to)| json!({"from": from, "to": to})).collect::<Vec<_>>(),
            })
        } else if request.contains("设计渐进学习阶段") {
            json!({"stages": [
                {"id": "stage-1", "title": "概率基础", "nodeIds": ["prob", "cond", "indep"], "difficulty": 1},
                {"id": "stage-2", "title": "贝叶斯更新", "nodeIds": ["bayes", "prior", "likelihood"], "difficulty": 2},
                {"id": "stage-3", "title": "分类与评估", "nodeIds": ["posterior", "classify", "evaluation"], "difficulty": 3},
            ]})
        } else {
            let check = || json!({"status": "pass", "details": ""});
            json!({
                "report": {
                    "dependency_closure": check(), "difficulty_step": check(), "cognitive_load": check(),
                    "milestone_feasibility": check(), "gap_analysis": check(),
                },
                "passed": true,
                "fixes": [],
            })
        };
        let event =
            serde_json::to_string(&json!({"choices": [{"delta": {"content": body.to_string()}}]}))
                .unwrap();
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":12,\"completion_tokens\":33}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"));
    })
}

fn wait_terminal(state: &LearningState, id: &str) -> Plan {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let plan = get(state, id).unwrap();
        if !["running", "cancelling"].contains(&plan.status.as_str()) {
            return plan;
        }
        assert!(Instant::now() < deadline, "学习生成未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn eight_steps_use_real_model_results_and_actual_usage() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let plan = wait_terminal(&state, &started.id);
    assert_eq!(plan.status, "degraded", "{:?}", plan.error);
    assert_eq!(plan.progress, 100);
    assert!(plan.steps.iter().all(|step| step.status == "done"));
    assert_eq!(server.request_count(), 3, "{:?}", plan.error);
    let result = plan.result.unwrap();
    assert_eq!(result.graph.nodes.len(), 9);
    assert_eq!(result.stages.len(), 3);
    assert_eq!(result.schedule.total_minutes, 135);
    assert!(result
        .schedule
        .entries
        .iter()
        .all(|entry| entry.minutes <= 45));
    assert!(result.validation.passed);
    assert!(result
        .warnings
        .iter()
        .any(|warning| warning.contains("外部资源检索")));
    assert_eq!(result.resources["items"].as_array().unwrap().len(), 1);
    assert_eq!(
        result.resources["items"][0]["materialId"],
        json!(material_id)
    );
    for step in plan.steps.iter().filter(|step| step.request_id.is_some()) {
        assert_eq!(step.usage.as_ref().unwrap().output_tokens, Some(33));
        assert_eq!(step.upstream_attempts, Some(1));
    }
    assert_eq!(plan.steps[2].request_id.as_deref(), Some("plan-1-3-1"));
    assert_eq!(plan.steps[3].request_id.as_deref(), Some("plan-1-4-1"));
    assert_eq!(plan.steps[6].request_id.as_deref(), Some("plan-1-7-1"));
    let resumed = resume(app.handle(), &state, &started.id).unwrap();
    assert_eq!(resumed.status, "degraded");
    assert_eq!(server.request_count(), 3);
    let events = state.store().unwrap().events_since(0).unwrap();
    for index in 0..STEP_NAMES.len() {
        for kind in ["plan.step_started", "plan.step_completed"] {
            assert!(
                events.events.iter().any(|event| {
                    event.kind == kind && event.payload["stepIndex"] == json!(index)
                }),
                "缺少 {kind} / step {index}"
            );
        }
    }
    assert_eq!(events.events.last().unwrap().kind, "plan.degraded");
    assert_eq!(
        super::super::materials::delete(app.handle(), &state.materials, material_id)
            .unwrap_err()
            .code,
        "material_in_use"
    );
}

#[test]
fn dependency_cycles_fail_instead_of_fabricating_a_valid_plan() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, true);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let plan = wait_terminal(&state, &started.id);
    assert_eq!(plan.status, "failed");
    assert_eq!(
        plan.error.as_ref().unwrap().code,
        "dependency_cycle",
        "{:?}",
        plan.steps[2].error
    );
    assert!(plan.result.is_none());
    assert_eq!(plan.steps[2].status, "failed");
    assert_eq!(plan.steps[3].status, "pending");
    assert_eq!(server.request_count(), 2);
}

#[test]
fn deterministic_validation_reports_real_load_and_deadline_problems() {
    let graph: Graph = serde_json::from_value(json!({
        "nodes": [{"id": "prob", "title": "概率", "summary": "基础", "difficulty": 1, "required": true, "estimatedMinutes": 45, "citations": []}],
        "edges": [],
    }))
    .unwrap();
    let stages = vec![Stage {
        id: "stage-1".into(),
        title: "超负荷阶段".into(),
        node_ids: vec!["prob".into()],
        difficulty: 1,
    }];
    let deadline = validate::today();
    let (schedule, _) = validate::schedule(&graph, &stages, 10, deadline, deadline, None).unwrap();
    let model = validate::unavailable_model();
    let report = validate::validation_report(&graph, &stages, &schedule, &model);
    assert!(!schedule.within_deadline);
    assert_eq!(report.cognitive_load.status, "fail");
    assert_eq!(report.milestone_feasibility.status, "warn");
    assert_eq!(report.gap_analysis.status, "warn");
    assert!(!report.passed);
    let (hard, _) =
        validate::schedule(&graph, &stages, 10, deadline, deadline, Some(deadline)).unwrap();
    let hard_report = validate::validation_report(&graph, &stages, &hard, &model);
    assert_eq!(hard.within_milestone, Some(false));
    assert_eq!(hard_report.milestone_feasibility.status, "fail");
    assert!(hard_report
        .milestone_feasibility
        .details
        .contains("硬里程碑"));
}

#[test]
fn graph_cycles_and_stage_dependencies_are_checked_independently() {
    let source = Source {
        id: 1,
        title: "资料".into(),
        sha256: "hash".into(),
        text: "概率 贝叶斯 后验".into(),
        warning: None,
    };
    let node = |id: &str| KnowledgeNode {
        id: id.into(),
        title: id.into(),
        summary: id.into(),
        difficulty: 1,
        required: true,
        estimated_minutes: 15,
        citations: vec![Citation {
            material_id: 1,
            quote: match id {
                "prob" => "概率".into(),
                "bayes" => "贝叶斯".into(),
                "posterior" => "后验".into(),
                _ => unreachable!(),
            },
        }],
    };
    let cyclic = Graph {
        nodes: vec![node("prob"), node("bayes")],
        edges: vec![
            KnowledgeEdge {
                from: "prob".into(),
                to: "bayes".into(),
            },
            KnowledgeEdge {
                from: "bayes".into(),
                to: "prob".into(),
            },
        ],
    };
    assert_eq!(
        validate::validate_graph(&cyclic, &[source.clone()])
            .unwrap_err()
            .code,
        "dependency_cycle"
    );
    let valid = Graph {
        nodes: vec![node("prob"), node("bayes"), node("posterior")],
        edges: vec![
            KnowledgeEdge {
                from: "prob".into(),
                to: "bayes".into(),
            },
            KnowledgeEdge {
                from: "bayes".into(),
                to: "posterior".into(),
            },
        ],
    };
    validate::validate_graph(&valid, &[source]).unwrap();
    let stages = vec![
        Stage {
            id: "one".into(),
            title: "一".into(),
            node_ids: vec!["prob".into()],
            difficulty: 1,
        },
        Stage {
            id: "two".into(),
            title: "二".into(),
            node_ids: vec!["bayes".into()],
            difficulty: 2,
        },
        Stage {
            id: "three".into(),
            title: "三".into(),
            node_ids: vec!["posterior".into()],
            difficulty: 3,
        },
    ];
    validate::validate_stages(&valid, &stages).unwrap();
}

#[test]
fn cancellation_stops_the_pipeline_and_keeps_the_actual_partial_request() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = server(|_, stream| {
        start_stream(stream);
        let event =
            serde_json::to_string(&json!({"choices": [{"delta": {"content": "{\"nodes\":"}}]}))
                .unwrap();
        let _ = std::io::Write::write_all(stream, format!("data: {event}\n\n").as_bytes());
        let _ = std::io::Write::flush(stream);
        std::thread::sleep(Duration::from_millis(500));
    });
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if providers::get_chat(&state.providers, "plan-1-3-1")
            .map(|request| request.output.contains("nodes"))
            .unwrap_or(false)
        {
            break;
        }
        assert!(Instant::now() < deadline, "图谱请求没有产生部分输出");
        std::thread::sleep(Duration::from_millis(5));
    }
    cancel(app.handle(), &state, &started.id).unwrap();
    let plan = wait_terminal(&state, &started.id);
    assert_eq!(plan.status, "cancelled");
    assert_eq!(plan.steps[2].status, "cancelled");
    assert_eq!(plan.steps[3].status, "pending");
    let request = providers::get_chat(&state.providers, "plan-1-3-1").unwrap();
    assert_eq!(request.status, "cancelled");
    assert!(request.output.contains("nodes"));
    assert!(request.usage.is_none());
    assert_eq!(server.request_count(), 1);
    stop(&state).unwrap();
}

#[test]
fn recovery_reuses_a_completed_graph_without_another_upstream_call() {
    let (app, state) = setup();
    state.defer.store(true, Ordering::Release);
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let detail = super::super::materials::detail(&state.materials, material_id).unwrap();
    let source = Source {
        id: detail.material.id,
        title: detail.material.title,
        sha256: detail.material.sha256,
        text: detail.text.unwrap(),
        warning: detail.material.warning,
    };
    complete_step(app.handle(), &state, 1, 0, json!({"snapshot": [source]})).unwrap();
    let persisted_input = super::input(&state, 1).unwrap();
    complete_step(
        app.handle(),
        &state,
        1,
        1,
        json!({"input": persisted_input}),
    )
    .unwrap();
    let (_, request_id) = begin_step(app.handle(), &state, 1, 2).unwrap();
    let request_id = request_id.unwrap();
    let graph = providers::stream_chat(
        app.handle(),
        &state.providers,
        &request_id,
        &started.provider_id,
        &[ChatMessage {
            role: "user".into(),
            content: "构建知识图谱".into(),
            ..Default::default()
        }],
        8192,
    )
    .unwrap();
    assert_eq!(graph.status, "done");
    assert_eq!(server.request_count(), 1);
    state.active.lock().unwrap().clear();
    recover(app.handle(), &state).unwrap();
    let interrupted = get(&state, &started.id).unwrap();
    assert_eq!(interrupted.status, "interrupted");
    assert_eq!(interrupted.steps[2].status, "interrupted");
    assert_eq!(server.request_count(), 1);
    state.defer.store(false, Ordering::Release);
    resume(app.handle(), &state, &started.id).unwrap();
    let recovered = wait_terminal(&state, &started.id);
    assert_eq!(recovered.status, "degraded");
    assert_eq!(recovered.steps[2].status, "done");
    assert_eq!(recovered.steps[2].attempt, 1);
    assert_eq!(
        recovered.steps[2].request_id.as_deref(),
        Some(request_id.as_str())
    );
    assert_eq!(recovered.result.unwrap().graph.nodes.len(), 9);
    assert_eq!(server.request_count(), 3);
    assert_eq!(list(&state).unwrap().len(), 1);
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

#[test]
fn ipc_commands_start_read_and_resume_a_real_plan() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let started = ipc(
        &window,
        "start_learning_plan",
        json!({"input": input(material_id)}),
    )
    .unwrap();
    let id = started["id"].as_str().unwrap();
    let completed = wait_terminal(&state, id);
    assert_eq!(completed.status, "degraded");
    assert_eq!(completed.steps.len(), STEP_NAMES.len());
    assert_eq!(server.request_count(), 3);
    let fetched = ipc(&window, "get_learning_plan", json!({"id": id})).unwrap();
    assert_eq!(
        fetched["result"]["graph"]["nodes"]
            .as_array()
            .unwrap()
            .len(),
        9
    );
    assert_eq!(fetched["result"]["schedule"]["totalMinutes"], json!(135));
    assert!(fetched["result"]["resources"]["externalSearch"]["reason"]
        .as_str()
        .unwrap()
        .contains("尚未配置"));
    let plans = ipc(&window, "list_learning_plans", json!({})).unwrap();
    assert_eq!(plans.as_array().unwrap().len(), 1);
    let resumed = ipc(&window, "resume_learning_plan", json!({"id": id})).unwrap();
    assert_eq!(resumed["status"], json!("degraded"));
    assert_eq!(server.request_count(), 3);
    let regenerated = ipc(
        &window,
        "regenerate_learning_plan",
        json!({"id": id, "scope": 3}),
    )
    .unwrap();
    assert!(["running", "degraded"].contains(&regenerated["status"].as_str().unwrap()));
    assert_eq!(wait_terminal(&state, id).version, 2);
    assert_eq!(server.request_count(), 5);
    let adopted = ipc(&window, "adopt_learning_plan", json!({"id": id})).unwrap();
    assert_eq!(adopted["tasks"].as_array().unwrap().len(), 3);
    assert_eq!(adopted["projects"].as_array().unwrap().len(), 1);
    let adopted_again = ipc(&window, "adopt_learning_plan", json!({"id": id})).unwrap();
    assert_eq!(adopted, adopted_again);
    assert_eq!(
        ipc(
            &window,
            "regenerate_learning_plan",
            json!({"id": id, "scope": 3})
        )
        .unwrap_err()["code"],
        json!("plan_adopted")
    );
    let error = ipc(&window, "get_learning_plan", json!({"id": "plan-999"})).unwrap_err();
    assert_eq!(error["code"], json!("not_found"));
}

#[test]
fn regeneration_preserves_history_and_adoption_is_atomic_and_idempotent() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    assert_eq!(server.request_count(), 3);
    actions::regenerate(app.handle(), &state, &started.id, 3).unwrap();
    let second = wait_terminal(&state, &started.id);
    assert_eq!(second.version, 2);
    assert_eq!(second.steps[2].attempt, 1);
    assert_eq!(second.steps[3].attempt, 2);
    assert_eq!(second.steps[6].attempt, 2);
    assert_eq!(server.request_count(), 5);
    actions::regenerate(app.handle(), &state, &started.id, 3).unwrap();
    let third = wait_terminal(&state, &started.id);
    assert_eq!(third.version, 3);
    assert_eq!(server.request_count(), 7);
    assert_eq!(
        actions::regenerate(app.handle(), &state, &started.id, 3)
            .unwrap_err()
            .code,
        "regeneration_limit"
    );
    let snapshot = actions::adopt(app.handle(), &state, &started.id).unwrap();
    assert_eq!(snapshot["tasks"].as_array().unwrap().len(), 3);
    assert_eq!(snapshot["projects"].as_array().unwrap().len(), 1);
    assert_eq!(
        snapshot["projects"][0]["milestones"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    assert!(snapshot["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .all(|task| task["duration"] == json!(45)));
    let repeated = actions::adopt(app.handle(), &state, &started.id).unwrap();
    assert_eq!(snapshot, repeated);
    let adopted = get(&state, &started.id).unwrap();
    assert!(adopted.adopted_project_id.is_some());
    assert_eq!(
        actions::regenerate(app.handle(), &state, &started.id, 3)
            .unwrap_err()
            .code,
        "plan_adopted"
    );
    let store = state.store().unwrap();
    let versions: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_plan_versions WHERE plan_id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(versions, 2);
    let scheduled: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_plan_schedule_tasks WHERE plan_id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(scheduled, 3);
}

#[test]
fn knowledge_map_uses_real_paths_tasks_reading_and_evidence() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let path = ipc(
        &window,
        "get_knowledge_path",
        json!({"id": started.id, "from": "prob", "to": "posterior"}),
    )
    .unwrap();
    assert_eq!(path, json!(["prob", "bayes", "posterior"]));
    assert_eq!(
        knowledge::shortest_path(&state, &started.id, "prob", "posterior").unwrap(),
        vec!["prob", "bayes", "posterior"]
    );
    assert_eq!(
        knowledge::shortest_path(&state, &started.id, "posterior", "prob")
            .unwrap_err()
            .code,
        "path_not_found"
    );
    let initial = knowledge::progress(&state, &started.id).unwrap();
    assert_eq!(initial.overall_mastery, None);
    assert_eq!(initial.evaluated_nodes, 0);
    assert_eq!(initial.missing_evidence, 9);
    let first = knowledge::add_task(app.handle(), &state, &started.id, "prob").unwrap();
    assert_eq!(first["tasks"].as_array().unwrap().len(), 1);
    assert_eq!(first["tasks"][0]["title"], json!("补学 · 概率"));
    assert_eq!(
        knowledge::add_task(app.handle(), &state, &started.id, "prob").unwrap(),
        first
    );
    let from_ipc = ipc(
        &window,
        "add_knowledge_task",
        json!({"id": started.id, "nodeId": "prob"}),
    )
    .unwrap();
    assert_eq!(from_ipc, first);
    let reading = knowledge::set_reading(
        app.handle(),
        &state,
        &started.id,
        "stage-1",
        true,
        Some(true),
    )
    .unwrap();
    assert!(reading.completed && reading.bookmarked);
    let store = state.store().unwrap();
    store.conn.execute("INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at) VALUES (1, 'prob', 'quiz', 8, 10, '{}', 'now')", []).unwrap();
    store.conn.execute("INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at) VALUES (1, 'prob', 'review', 4, 5, '{}', 'now')", []).unwrap();
    let overview = knowledge::progress(&state, &started.id).unwrap();
    assert_eq!(overview.overall_mastery, Some(80));
    assert_eq!(overview.evaluated_nodes, 1);
    assert_eq!(overview.missing_evidence, 8);
    let node = overview
        .nodes
        .iter()
        .find(|node| node.node_id == "prob")
        .unwrap();
    assert_eq!(node.mastery, Some(80));
    assert_eq!(node.evidence_count, 2);
    assert!(node.reading_completed);
    assert!(node.evidence.contains("12/15"));
    let reading =
        knowledge::set_reading(app.handle(), &state, &started.id, "stage-1", false, None).unwrap();
    assert!(!reading.completed && reading.bookmarked);
    let progress = knowledge::reading(&state, &started.id, "stage-1").unwrap();
    assert!(!progress.completed && progress.bookmarked);
}

fn wait_lecture(state: &LearningState, id: &str, stage_id: &str) -> lecture::Lecture {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let lecture = lecture::get(state, id, stage_id, None).unwrap();
        if !["running", "cancelling"].contains(&lecture.status.as_str()) {
            return lecture;
        }
        assert!(Instant::now() < deadline, "讲义生成未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn lecture_generation_uses_real_units_and_preserves_versions() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    let initial_calls = server.request_count();
    actions::adopt(app.handle(), &state, &started.id).unwrap();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    ipc(
        &window,
        "generate_lecture",
        json!({"id": started.id, "stageId": "stage-1", "force": false}),
    )
    .unwrap();
    let lecture = wait_lecture(&state, &started.id, "stage-1");
    assert_eq!(lecture.status, "done");
    assert_eq!(lecture.version, 1);
    assert_eq!(lecture.completed_units, 3);
    assert_eq!(lecture.units.len(), 3);
    assert_eq!(server.request_count(), initial_calls + 3);
    assert_eq!(
        lecture.units[0].content.as_ref().unwrap().sections[0].content,
        "依据资料生成的讲义正文"
    );
    assert_eq!(
        lecture.units[0].content.as_ref().unwrap().citations[0].quote,
        "概率"
    );
    assert_eq!(
        lecture.units[0].usage.as_ref().unwrap().output_tokens,
        Some(33)
    );
    let fetched = ipc(
        &window,
        "get_lecture",
        json!({"id": started.id, "stageId": "stage-1"}),
    )
    .unwrap();
    assert_eq!(fetched["completedUnits"], json!(3));
    ipc(
        &window,
        "generate_lecture",
        json!({"id": started.id, "stageId": "stage-1", "force": false}),
    )
    .unwrap();
    assert_eq!(server.request_count(), initial_calls + 3);
    ipc(
        &window,
        "generate_lecture",
        json!({"id": started.id, "stageId": "stage-1", "force": true}),
    )
    .unwrap();
    let second = wait_lecture(&state, &started.id, "stage-1");
    assert_eq!(second.version, 2);
    assert_eq!(second.previous_version, Some(1));
    assert_eq!(server.request_count(), initial_calls + 6);
    let old = lecture::get(&state, &started.id, "stage-1", Some(1)).unwrap();
    assert_eq!(old.status, "done");
    assert_eq!(old.units.len(), 3);
    let versions: i64 = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_lecture_versions WHERE plan_id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(versions, 2);
}

#[test]
fn lecture_cancellation_stops_before_the_next_unit() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let plan_server = model_server(material_id, false);
    configure_provider(&app, &state, &plan_server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    actions::adopt(app.handle(), &state, &started.id).unwrap();
    let server = server(|_, stream| {
        start_stream(stream);
        let event =
            serde_json::to_string(&json!({"choices": [{"delta": {"content": "{\"title\":"}}]}))
                .unwrap();
        let _ = std::io::Write::write_all(stream, format!("data: {event}\n\n").as_bytes());
        let _ = std::io::Write::flush(stream);
        std::thread::sleep(Duration::from_millis(400));
    });
    let profile = providers::get(&state.providers, &started.provider_id).unwrap();
    providers::upsert(
        app.handle(),
        &state.providers,
        ProfileInput {
            id: Some(profile.id),
            name: profile.name,
            preset: Some(profile.preset),
            kind: profile.kind,
            protocol: profile.protocol,
            base_url: server.url(),
            default_model: profile.default_model,
            models: profile.models,
            enabled: Some(true),
            note: profile.note,
            api_key: None,
        },
    )
    .unwrap();
    lecture::generate(app.handle(), &state, &started.id, "stage-1", false).unwrap();
    let request_id = lecture::request_id(1, "stage-1", 1, 0, 1);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if providers::get_chat(&state.providers, &request_id)
            .map(|request| request.output.contains("title"))
            .unwrap_or(false)
        {
            break;
        }
        assert!(Instant::now() < deadline, "讲义没有产生可取消的部分输出");
        std::thread::sleep(Duration::from_millis(5));
    }
    lecture::cancel(app.handle(), &state, &started.id, "stage-1").unwrap();
    let lecture = wait_lecture(&state, &started.id, "stage-1");
    assert_eq!(lecture.status, "cancelled");
    assert_eq!(lecture.units[0].status, "cancelled");
    assert_eq!(lecture.units[1].status, "pending");
    assert_eq!(server.request_count(), 1);
    stop(&state).unwrap();
}

#[test]
fn lecture_recovery_reuses_completed_unit_output() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    let initial_calls = server.request_count();
    actions::adopt(app.handle(), &state, &started.id).unwrap();
    state.defer.store(true, Ordering::Release);
    lecture::generate(app.handle(), &state, &started.id, "stage-1", false).unwrap();
    let (_, request_id) = lecture::begin_unit(app.handle(), &state, 1, "stage-1", 1, 0).unwrap();
    let request = providers::stream_chat(
        app.handle(),
        &state.providers,
        &request_id,
        &started.provider_id,
        &[ChatMessage {
            role: "user".into(),
            content: "自学讲义".into(),
            ..Default::default()
        }],
        8192,
    )
    .unwrap();
    assert_eq!(request.status, "done");
    assert_eq!(server.request_count(), initial_calls + 1);
    state.lecture_active.lock().unwrap().clear();
    recover(app.handle(), &state).unwrap();
    let interrupted = lecture::get(&state, &started.id, "stage-1", None).unwrap();
    assert_eq!(interrupted.status, "interrupted");
    assert_eq!(interrupted.units[0].status, "interrupted");
    state.defer.store(false, Ordering::Release);
    lecture::generate(app.handle(), &state, &started.id, "stage-1", false).unwrap();
    let recovered = wait_lecture(&state, &started.id, "stage-1");
    assert_eq!(recovered.status, "done");
    assert_eq!(recovered.units[0].attempt, 1);
    assert_eq!(recovered.completed_units, 3);
    assert_eq!(server.request_count(), initial_calls + 3);
}

#[test]
fn lecture_rejects_citations_outside_the_original_text() {
    let sources = vec![Source {
        id: 1,
        title: "资料".into(),
        sha256: "hash".into(),
        text: "真实原文".into(),
        warning: None,
    }];
    let content = lecture::LectureContent {
        title: "标题".into(),
        overview: "概述".into(),
        sections: vec![lecture::LectureSection {
            heading: "概念".into(),
            content: "正文".into(),
        }],
        examples: vec![],
        checkpoints: vec![lecture::LectureCheckpoint {
            question: "问题".into(),
            answer: "答案".into(),
        }],
        citations: vec![Citation {
            material_id: 1,
            quote: "不存在的原文".into(),
        }],
    };
    assert_eq!(
        lecture::validate_content(&content, &sources)
            .unwrap_err()
            .code,
        "citation_mismatch"
    );
}

#[test]
fn study_notes_are_persisted_with_the_actual_stage() {
    let (app, state) = setup();
    let material_id = add_material(&app, &state);
    let server = model_server(material_id, false);
    configure_provider(&app, &state, &server.url());
    let started = start(app.handle(), &state, input(material_id)).unwrap();
    let _ = wait_terminal(&state, &started.id);
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let snapshot = ipc(
        &window,
        "save_study_note",
        json!({"id": started.id, "stageId": "stage-1", "content": "记录真实学习批注"}),
    )
    .unwrap();
    assert_eq!(snapshot["notes"].as_array().unwrap().len(), 1);
    assert_eq!(snapshot["notes"][0]["title"], json!("概率基础 · 学习批注"));
    assert_eq!(snapshot["notes"][0]["content"], json!("记录真实学习批注"));
    let count: i64 = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_annotations WHERE plan_id = 1 AND stage_id = 'stage-1'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 1);
}
