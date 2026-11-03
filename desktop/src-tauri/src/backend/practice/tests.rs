use super::super::learning::{self, PlanInput};
use super::super::materials;
use super::super::providers::test_support::{response, server, start_stream, TestServer};
use super::super::providers::{self, ChatMessage, ProfileInput};
use super::super::BackendState;
use super::*;
use serde_json::json;
use std::sync::Mutex;

mod exam_tests;
mod subjective_tests;
mod variant_tests;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, PracticeState) {
    let state = PracticeState::for_tests().unwrap();
    let learning = state.learning.shared();
    let path = learning.database_path().to_path_buf();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: learning.material_state(),
            providers: learning.provider_state(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::for_tests().unwrap(),
            practice: state.shared(),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::for_tests().unwrap(),
            community: crate::backend::community::CommunityState::for_tests().unwrap(),
            plugins: crate::backend::plugins::PluginState::for_tests().unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::generate_practice,
            super::commands::get_practice_set,
            super::commands::list_practice_history,
            super::commands::cancel_practice_generation,
            super::commands::resume_practice_generation,
            super::commands::submit_practice_answer,
            super::commands::submit_exam,
            super::commands::list_practice_mistakes,
            super::commands::export_practice_mistakes,
            super::commands::set_practice_bookmark,
            super::commands::set_practice_mistake_mastered,
            super::commands::cancel_practice_answer,
            super::commands::resume_practice_answer,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
}

fn ipc(
    window: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    command: &str,
    arguments: serde_json::Value,
) -> std::result::Result<serde_json::Value, serde_json::Value> {
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
    .map(|body| body.deserialize::<serde_json::Value>().unwrap())
}

fn input(plan_id: &str, count: u32, kinds: Vec<&str>, feedback: &str) -> GenerateInput {
    GenerateInput {
        plan_id: plan_id.into(),
        stage_id: None,
        node_id: None,
        count: Some(count),
        difficulty: Some("adaptive".into()),
        feedback_mode: Some(feedback.into()),
        kinds: Some(kinds.into_iter().map(str::to_owned).collect()),
        mode: None,
        variant_of: None,
        time_limit_minutes: None,
        pass_score_percent: None,
    }
}

fn model_server(
    material_id: i64,
    gate: Option<Arc<AtomicBool>>,
    invalid_citation: bool,
) -> TestServer {
    server(move |request, stream| {
        let body = if request.contains("学习测评评分器") {
            if let Some(gate) = &gate {
                start_stream(stream);
                while !gate.load(Ordering::Acquire) {
                    std::thread::sleep(Duration::from_millis(5));
                }
            }
            let grasp = if request.contains("评分溢出") {
                1.5
            } else if request.contains("不知道") {
                0.0
            } else {
                1.0
            };
            json!({
                "criteria": [
                    {"id": "concept", "score": grasp, "evidence": if grasp == 1.0 { "学生说明了资料中的核心概念" } else { "学生没有给出核心概念解释" }, "feedback": "检查是否准确解释先验概率"},
                    {"id": "evidence", "score": 0.5, "evidence": "论证联系了部分资料依据", "feedback": "进一步结合原文说明证据如何改变判断"},
                ],
                "overallFeedback": if grasp == 1.0 { "概念解释完整，但论证依据还可更具体。" } else { "未说明先验概率与资料依据，需补充核心概念。" },
                "citations": [{"materialId": material_id, "quote": "先验概率"}],
            })
        } else if request.contains("学习测评命题器") {
            if let Some(gate) = &gate {
                start_stream(stream);
                while !gate.load(Ordering::Acquire) {
                    std::thread::sleep(Duration::from_millis(5));
                }
            }
            if request.contains("变式练习") {
                if request.contains("原题型：short") {
                    json!({"questions": [{
                        "kind": "short", "prompt": "在新情境中说明为什么先验概率会改变证据更新后的判断。", "options": [],
                        "answer": "先验概率决定判断起点，新证据在此基础上更新为后验概率。",
                        "hint": "说明判断起点和更新过程", "explanation": "回答应准确联系先验、证据与后验。",
                        "citations": [{"materialId": material_id, "quote": "先验概率"}], "nodeIds": ["prob"], "difficulty": 2,
                        "rubric": [
                            {"id": "concept", "description": "准确解释资料中的核心概念", "points": 0.6},
                            {"id": "evidence", "description": "结合资料说明判断依据", "points": 0.4},
                        ],
                    }]})
                } else {
                    let questions = (0..3).map(|index| json!({
                        "kind": "choice", "prompt": format!("变式情境 {}：在证据出现前应先确定什么？", index + 1),
                        "options": ["先验概率", "与证据无关的信息"], "answer": 0,
                        "hint": "考虑证据出现前的判断", "explanation": "新情境仍然检查先验概率的含义。",
                        "citations": [{"materialId": material_id, "quote": "先验概率"}], "nodeIds": ["prob"], "difficulty": 2,
                    })).collect::<Vec<_>>();
                    json!({"questions": questions})
                }
            } else if request.contains("题目类别：subjective") {
                let rubric = json!([
                    {"id": "concept", "description": "准确解释资料中的核心概念", "points": 0.6},
                    {"id": "evidence", "description": "结合资料说明判断依据", "points": 0.4},
                ]);
                let short = json!({
                    "kind": "short", "prompt": "为什么看到新证据前需要先确定先验概率？", "options": [],
                    "answer": "先验概率是引入新证据前的判断，贝叶斯更新以此为起点。",
                    "hint": "比较引入证据前后的判断", "explanation": "回答应说明先验概率与证据更新的关系。",
                    "citations": [{"materialId": material_id, "quote": "先验概率"}], "nodeIds": ["prob"], "difficulty": 2, "rubric": rubric,
                });
                let essay = json!({
                    "kind": "essay", "prompt": "论述先验概率如何影响新证据后的判断。", "options": [],
                    "answer": "应论述先验概率、证据与后验概率之间的关系，并结合资料说明。",
                    "hint": "从判断的起点和更新过程展开", "explanation": "回答应形成完整论证并联系资料依据。",
                    "citations": [{"materialId": material_id, "quote": "后验概率"}], "nodeIds": ["bayes"], "difficulty": 3, "rubric": rubric,
                });
                json!({"questions": if request.contains("生成 1 道") { vec![short] } else { vec![short, essay] }})
            } else {
                let quote = if invalid_citation {
                    "并非资料中的引用"
                } else {
                    "先验概率"
                };
                let choice = json!({
                    "kind": "choice", "prompt": "看到新证据之前的概率称为什么？",
                    "options": ["先验概率", "条件概率"], "answer": 0,
                    "hint": "注意证据出现之前的时间点", "explanation": "资料将先验概率与后验概率区分开。",
                    "citations": [{"materialId": material_id, "quote": quote}], "nodeIds": ["prob"], "difficulty": 2,
                });
                let fill = json!({
                    "kind": "fill", "prompt": "结合新证据后更新的概率称为什么？", "options": [],
                    "answer": "后验概率", "hint": "它是更新后的判断", "explanation": "资料中的后验概率对应更新后的判断。",
                    "citations": [{"materialId": material_id, "quote": "后验概率"}], "nodeIds": ["bayes"], "difficulty": 2,
                });
                json!({"questions": if request.contains("生成 1 道") { vec![choice] } else { vec![choice, fill] }})
            }
        } else if request.contains("构建知识图谱") {
            json!({"nodes": [
                {"id": "prob", "title": "概率", "summary": "真实概率节点", "difficulty": 1, "required": true, "estimatedMinutes": 15, "citations": [{"materialId": material_id, "quote": "概率"}]},
                {"id": "bayes", "title": "贝叶斯定理", "summary": "真实贝叶斯节点", "difficulty": 2, "required": true, "estimatedMinutes": 15, "citations": [{"materialId": material_id, "quote": "贝叶斯定理"}]},
            ], "edges": [{"from": "prob", "to": "bayes"}]})
        } else if request.contains("设计渐进学习阶段") {
            json!({"stages": [
                {"id": "stage-1", "title": "概率基础", "nodeIds": ["prob"], "difficulty": 1},
                {"id": "stage-2", "title": "贝叶斯更新", "nodeIds": ["bayes"], "difficulty": 2},
            ]})
        } else {
            let check = || json!({"status": "pass", "details": ""});
            json!({"report": {
                "dependency_closure": check(), "difficulty_step": check(), "cognitive_load": check(),
                "milestone_feasibility": check(), "gap_analysis": check(),
            }, "passed": true, "fixes": []})
        };
        let event =
            serde_json::to_string(&json!({"choices": [{"delta": {"content": body.to_string()}}]}))
                .unwrap();
        response(stream, "200 OK", &format!("data: {event}\n\ndata: {{\"usage\":{{\"prompt_tokens\":12,\"completion_tokens\":33}},\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"));
    })
}

fn prepare_with_gate(
    gate: Option<Arc<AtomicBool>>,
    invalid_citation: bool,
) -> (
    tauri::App<tauri::test::MockRuntime>,
    PracticeState,
    TestServer,
    String,
) {
    let (app, state) = setup();
    let path = state.database_path.parent().unwrap().join("chapter.txt");
    std::fs::write(&path, "概率是可能性的度量。先验概率是在看到证据前的判断，后验概率结合新证据更新。贝叶斯定理连接先验与后验。").unwrap();
    let imported = materials::import_files(
        app.handle(),
        &state.learning.material_state(),
        vec![path.to_string_lossy().into_owned()],
    );
    let material_id = imported[0].material.as_ref().unwrap().id;
    materials::enqueue(app.handle(), &state.learning.material_state(), material_id).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while materials::detail(&state.learning.material_state(), material_id)
        .unwrap()
        .material
        .status
        != "parsed"
    {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let server = model_server(material_id, gate, invalid_citation);
    let profile = providers::upsert(
        app.handle(),
        &state.providers,
        ProfileInput {
            id: None,
            name: "练习测试".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: server.url(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: Some("practice-test-token".into()),
        },
    )
    .unwrap();
    providers::set_active(app.handle(), &state.providers, &profile.id).unwrap();
    let plan = learning::start(
        app.handle(),
        &state.learning,
        PlanInput {
            goal: "掌握概率和贝叶斯定理".into(),
            level: "初学者".into(),
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
        let current = learning::get(&state.learning, &plan.id).unwrap();
        if !["running", "cancelling"].contains(&current.status.as_str()) {
            assert_eq!(current.status, "degraded", "{:?}", current.error);
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    learning::actions::adopt(app.handle(), &state.learning, &plan.id).unwrap();
    (app, state, server, plan.id)
}

fn prepare() -> (
    tauri::App<tauri::test::MockRuntime>,
    PracticeState,
    TestServer,
    String,
) {
    prepare_with_gate(None, false)
}

fn wait_set(state: &PracticeState, id: i64) -> PracticeSetDetail {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let detail = detail(state, id).unwrap();
        if !["generating", "cancelling"].contains(&detail.set.status.as_str()) {
            return detail;
        }
        assert!(Instant::now() < deadline, "练习生成未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn objective_questions_use_real_citations_answers_mistakes_and_evidence() {
    let (app, state, server, plan_id) = prepare();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let calls = server.request_count();
    assert_eq!(
        ipc(
            &window,
            "generate_practice",
            json!({"input": input(&plan_id, 21, vec!["choice"], "immediate")})
        )
        .unwrap_err()["code"],
        "invalid_question_count"
    );
    assert_eq!(
        ipc(
            &window,
            "generate_practice",
            json!({"input": input(&plan_id, 1, vec!["unknown"], "immediate")})
        )
        .unwrap_err()["code"],
        "unsupported_question_kind"
    );
    assert_eq!(
        ipc(
            &window,
            "generate_practice",
            json!({"input": {"planId": plan_id, "mode": "exam", "count": 2}})
        )
        .unwrap_err()["code"],
        "invalid_feedback_mode"
    );
    let started = ipc(
        &window,
        "generate_practice",
        json!({"input": input(&plan_id, 2, vec!["choice", "fill"], "immediate")}),
    )
    .unwrap();
    let id = started["set"]["id"].as_i64().unwrap();
    let generated = wait_set(&state, id);
    assert_eq!(generated.set.status, "ready");
    assert_eq!(generated.set.kinds, vec!["choice", "fill"]);
    assert_eq!(generated.questions.len(), 2);
    assert_eq!(generated.questions[0].options, vec!["先验概率", "条件概率"]);
    assert_eq!(generated.questions[0].hint, "注意证据出现之前的时间点");
    assert_eq!(generated.questions[0].citations[0].quote, "先验概率");
    assert_eq!(generated.questions[1].citations[0].quote, "后验概率");
    assert_eq!(server.request_count(), calls + 1);
    let choice = generated.questions[0].id;
    assert_eq!(
        ipc(
            &window,
            "submit_practice_answer",
            json!({"setId": id, "questionId": choice, "answer": "0"})
        )
        .unwrap_err()["code"],
        "invalid_answer"
    );
    let wrong = ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": id, "questionId": choice, "answer": 1}),
    )
    .unwrap();
    assert_eq!(wrong["answer"]["correct"], false);
    assert_eq!(wrong["answer"]["feedback"]["answer"], 0);
    assert_eq!(wrong["set"]["maxScore"], 2.0);
    assert_eq!(
        ipc(
            &window,
            "submit_practice_answer",
            json!({"setId": id, "questionId": choice, "answer": 1})
        )
        .unwrap_err()["code"],
        "answer_already_graded"
    );
    ipc(
        &window,
        "set_practice_bookmark",
        json!({"questionId": choice, "bookmarked": true}),
    )
    .unwrap();
    assert!(
        ipc(&window, "get_practice_set", json!({"id": id})).unwrap()["questions"][0]["bookmarked"]
            .as_bool()
            .unwrap()
    );
    let right = ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": id, "questionId": generated.questions[1].id, "answer": " 后验概率\n"}),
    )
    .unwrap();
    assert_eq!(right["answer"]["correct"], true);
    assert_eq!(right["set"]["status"], "completed");
    assert_eq!(right["set"]["score"], 1.0);
    assert_eq!(right["set"]["maxScore"], 2.0);
    let store = state.store().unwrap();
    let evidence: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_node_evidence WHERE kind = 'practice'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(evidence, 2);
    drop(store);
    let mistakes = ipc(&window, "list_practice_mistakes", json!({})).unwrap();
    assert_eq!(mistakes.as_array().unwrap().len(), 1);
    assert_eq!(mistakes[0]["questionId"], choice);
    ipc(
        &window,
        "set_practice_mistake_mastered",
        json!({"questionId": choice, "mastered": true}),
    )
    .unwrap();
    let export = ipc(&window, "export_practice_mistakes", json!({})).unwrap();
    assert_eq!(export["mistakes"][0]["mastered"], true);
    assert_eq!(
        ipc(&window, "list_practice_history", json!({}))
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn deferred_and_hint_feedback_do_not_reveal_answers_early() {
    let (app, state, _server, plan_id) = prepare();
    let deferred = generate(
        app.handle(),
        &state,
        input(&plan_id, 2, vec!["choice", "fill"], "deferred"),
    )
    .unwrap();
    let questions = wait_set(&state, deferred.set.id).questions;
    let pending = submit(
        app.handle(),
        &state,
        deferred.set.id,
        questions[0].id,
        json!(1),
    )
    .unwrap();
    assert_eq!(pending.answer.correct, None);
    assert_eq!(pending.answer.score, None);
    assert_eq!(pending.answer.feedback, json!({"status": "submitted"}));
    assert!(detail(&state, deferred.set.id).unwrap().answers.is_empty());
    assert_eq!(
        detail(&state, deferred.set.id)
            .unwrap()
            .answered_question_ids,
        vec![questions[0].id]
    );
    let completed = submit(
        app.handle(),
        &state,
        deferred.set.id,
        questions[1].id,
        json!("后验概率"),
    )
    .unwrap();
    assert_eq!(completed.answer.correct, Some(true));
    assert_eq!(completed.set.score, 1.0);
    assert_eq!(detail(&state, deferred.set.id).unwrap().answers.len(), 2);
    let hints = generate(
        app.handle(),
        &state,
        input(&plan_id, 1, vec!["choice"], "hints"),
    )
    .unwrap();
    let question = wait_set(&state, hints.set.id).questions.remove(0);
    let graded = submit(app.handle(), &state, hints.set.id, question.id, json!(0)).unwrap();
    assert_eq!(graded.answer.correct, None);
    assert_eq!(
        graded.answer.feedback,
        json!({"hint": "注意证据出现之前的时间点"})
    );
    let answer = &detail(&state, hints.set.id).unwrap().answers[0];
    assert_eq!(answer.correct, None);
    assert_eq!(answer.feedback.get("answer"), None);
}

#[test]
fn invalid_citations_roll_back_generation_and_resume_uses_a_new_attempt() {
    let (app, state, server, plan_id) = prepare_with_gate(None, true);
    let calls = server.request_count();
    let started = generate(
        app.handle(),
        &state,
        input(&plan_id, 2, vec!["choice", "fill"], "immediate"),
    )
    .unwrap();
    let failed = wait_set(&state, started.set.id);
    assert_eq!(failed.set.status, "failed");
    assert_eq!(failed.set.error.unwrap().code, "citation_mismatch");
    assert!(failed.questions.is_empty());
    assert_eq!(server.request_count(), calls + 1);
    resume(app.handle(), &state, started.set.id).unwrap();
    let failed = wait_set(&state, started.set.id);
    assert_eq!(failed.set.attempt, 2);
    assert_eq!(
        failed.set.request_id.as_deref(),
        Some(format!("practice-{}-2", started.set.id).as_str())
    );
    assert_eq!(server.request_count(), calls + 2);
}

#[test]
fn cancellation_stops_upstream_resume_and_reset_waits_for_workers() {
    let gate = Arc::new(AtomicBool::new(false));
    let (app, state, server, plan_id) = prepare_with_gate(Some(Arc::clone(&gate)), false);
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let calls = server.request_count();
    let started = generate(
        app.handle(),
        &state,
        input(&plan_id, 2, vec!["choice", "fill"], "immediate"),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let set = detail(&state, started.set.id).unwrap().set;
        if server.request_count() >= calls + 1
            && set.request_id.as_deref().is_some_and(|request| {
                providers::get_chat(&state.providers, request)
                    .is_ok_and(|result| result.status == "running")
            })
        {
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let cancelling = ipc(
        &window,
        "cancel_practice_generation",
        json!({"id": started.set.id}),
    )
    .unwrap();
    assert_eq!(cancelling["set"]["status"], "cancelling");
    gate.store(true, Ordering::Release);
    let cancelled = wait_set(&state, started.set.id);
    assert_eq!(cancelled.set.status, "cancelled");
    assert!(cancelled.questions.is_empty());
    ipc(
        &window,
        "resume_practice_generation",
        json!({"id": started.set.id}),
    )
    .unwrap();
    let recovered = wait_set(&state, started.set.id);
    assert_eq!(recovered.set.status, "ready");
    assert_eq!(recovered.set.attempt, 2);
    assert_eq!(server.request_count(), calls + 2);
    gate.store(false, Ordering::Release);
    let another = generate(
        app.handle(),
        &state,
        input(&plan_id, 2, vec!["choice", "fill"], "immediate"),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while detail(&state, another.set.id)
        .unwrap()
        .set
        .request_id
        .is_none()
    {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let stopping = state.shared();
    let worker = std::thread::spawn(move || stop(&stopping));
    std::thread::sleep(Duration::from_millis(20));
    gate.store(true, Ordering::Release);
    worker.join().unwrap().unwrap();
    state.store().unwrap().reset().unwrap();
    assert_eq!(history(&state).unwrap().len(), 0);
    assert_eq!(
        learning::get(&state.learning, &plan_id).unwrap_err().code,
        "not_found"
    );
}

#[test]
fn recovery_reuses_completed_model_output_without_another_provider_call() {
    let (app, state, server, plan_id) = prepare();
    let plan = learning::get(&state.learning, &plan_id).unwrap();
    let timestamp = now().unwrap();
    let store = state.store().unwrap();
    store.conn.execute(
        "INSERT INTO practice_sets (mode, status, plan_id, stage_id, provider_id, difficulty, feedback_mode, kinds_json, total_questions, request_id, created_at, updated_at) VALUES ('targeted', 'generating', 1, NULL, ?1, 'adaptive', 'immediate', ?2, 2, 'practice-1-1', ?3, ?3)",
        params![plan.provider_id, serde_json::to_string(&vec!["choice", "fill"]).unwrap(), timestamp],
    ).unwrap();
    drop(store);
    let calls = server.request_count();
    let result = providers::stream_chat(
        app.handle(),
        &state.providers,
        "practice-1-1",
        &plan.provider_id,
        &[ChatMessage {
            role: "system".into(),
            content: GENERATION_SYSTEM.into(),
            ..Default::default()
        }],
        8192,
    )
    .unwrap();
    assert_eq!(result.status, "done");
    assert_eq!(server.request_count(), calls + 1);
    recover(app.handle(), &state).unwrap();
    assert_eq!(detail(&state, 1).unwrap().set.status, "interrupted");
    resume(app.handle(), &state, 1).unwrap();
    let recovered = wait_set(&state, 1);
    assert_eq!(recovered.set.status, "ready");
    assert_eq!(recovered.set.attempt, 1);
    assert_eq!(recovered.questions.len(), 2);
    assert_eq!(server.request_count(), calls + 1);
    assert_eq!(resume(app.handle(), &state, 1).unwrap().set.status, "ready");
    assert_eq!(server.request_count(), calls + 1);
}

#[test]
fn stage_scope_is_enforced_against_model_node_ids() {
    let (app, state, _server, plan_id) = prepare();
    let mut scoped = input(&plan_id, 1, vec!["choice"], "immediate");
    scoped.stage_id = Some("stage-1".into());
    let started = generate(app.handle(), &state, scoped.clone()).unwrap();
    assert_eq!(wait_set(&state, started.set.id).set.status, "ready");
    scoped.stage_id = Some("stage-2".into());
    let started = generate(app.handle(), &state, scoped).unwrap();
    let rejected = wait_set(&state, started.set.id);
    assert_eq!(rejected.set.status, "failed");
    assert_eq!(rejected.set.error.unwrap().code, "invalid_model_output");
    assert!(rejected.questions.is_empty());
}

#[test]
fn migration_preserves_the_objective_question_kind_configuration() {
    let store = Store::in_memory().unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_sets DROP COLUMN kinds_json", [])
        .unwrap();
    migrate(&store.conn).unwrap();
    store.conn.execute("INSERT INTO learning_plans (id, status, provider_id, input_json, created_at, updated_at) VALUES (1, 'done', 'p1', '{}', 'now', 'now')", []).unwrap();
    store.conn.execute("INSERT INTO practice_sets (mode, status, plan_id, provider_id, difficulty, feedback_mode, total_questions, created_at, updated_at) VALUES ('targeted', 'ready', 1, 'p1', 'adaptive', 'immediate', 1, 'now', 'now')", []).unwrap();
    let kinds: String = store
        .conn
        .query_row(
            "SELECT kinds_json FROM practice_sets WHERE id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(
        serde_json::from_str::<Vec<String>>(&kinds).unwrap(),
        vec!["choice", "fill"]
    );
}

#[test]
fn node_scope_is_validated_persisted_and_enforced_in_real_generation() {
    let (app, state, _server, plan_id) = prepare();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let mut request = input(&plan_id, 1, vec!["choice"], "immediate");
    request.stage_id = Some("stage-1".into());
    request.node_id = Some("prob".into());
    let prompt = generation_prompt(
        &learning::get(&state.learning, &plan_id).unwrap(),
        &[],
        &request,
        &HashSet::from(["prob".to_string()]),
        None,
    )
    .unwrap();
    assert!(prompt.contains("\"id\":\"prob\""));
    assert!(!prompt.contains("\"id\":\"cond\""));
    request.node_id = Some("bayes".into());
    let error = ipc(&window, "generate_practice", json!({"input":request})).unwrap_err();
    assert_eq!(error["code"], "invalid_practice_scope");
    request.node_id = Some("prob".into());
    let started = ipc(&window, "generate_practice", json!({"input":request})).unwrap();
    assert_eq!(started["set"]["nodeId"], "prob");
    let generated = wait_set(&state, started["set"]["id"].as_i64().unwrap());
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    assert_eq!(generated.set.node_id.as_deref(), Some("prob"));
    assert!(generated
        .questions
        .iter()
        .all(|question| question.node_ids == vec!["prob"]));
    let history = ipc(&window, "list_practice_history", json!({})).unwrap();
    assert!(history
        .as_array()
        .unwrap()
        .iter()
        .any(|set| set["id"] == started["set"]["id"] && set["nodeId"] == "prob"));
}
