use super::*;
use crate::backend::practice::subjective;
use crate::backend::providers::{self, ChatMessage};

fn generate_subjectives(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &PracticeState,
    plan_id: &str,
    feedback: &str,
) -> PracticeSetDetail {
    let started = generate(
        app.handle(),
        state,
        GenerateInput {
            plan_id: plan_id.into(),
            stage_id: None,
            node_id: None,
            count: Some(2),
            difficulty: Some("adaptive".into()),
            feedback_mode: Some(feedback.into()),
            kinds: Some(vec!["short".into(), "essay".into()]),
            mode: None,
            variant_of: None,
            time_limit_minutes: None,
            pass_score_percent: None,
        },
    )
    .unwrap();
    let generated = wait_set(state, started.set.id);
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    assert_eq!(generated.questions.len(), 2);
    assert_eq!(generated.questions[0].kind, "short");
    assert_eq!(generated.questions[1].kind, "essay");
    assert_eq!(generated.questions[0].rubric.len(), 2);
    assert_eq!(generated.questions[0].rubric[0].points, 0.6);
    assert_eq!(generated.questions[0].rubric[1].points, 0.4);
    generated
}

fn wait_answer(state: &PracticeState, set_id: i64, question_id: i64) -> AnswerView {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(answer) = detail(state, set_id)
            .unwrap()
            .answers
            .into_iter()
            .find(|answer| answer.question_id == question_id)
        {
            if !["grading", "cancelling"].contains(&answer.status.as_str()) {
                return answer;
            }
        }
        assert!(Instant::now() < deadline, "主观评分未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn subjective_rubrics_scores_citations_and_evidence_are_real() {
    let (app, state, _server, plan_id) = prepare();
    let generated = generate_subjectives(&app, &state, &plan_id, "immediate");
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let short = &generated.questions[0];
    let pending = ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": generated.set.id, "questionId": short.id, "answer": "先验概率是看到证据前的判断，后验概率由新证据更新。"}),
    )
    .unwrap();
    assert_eq!(pending["answer"]["status"], "grading");
    let answer = wait_answer(&state, generated.set.id, short.id);
    assert_eq!(answer.status, "done");
    assert_eq!(answer.score, Some(0.8));
    assert_eq!(answer.max_score, Some(1.0));
    assert_eq!(answer.correct, Some(true));
    assert_eq!(answer.feedback["criteria"][0]["score"], 0.6);
    assert_eq!(answer.feedback["criteria"][1]["score"], 0.2);
    assert_eq!(answer.feedback["criteria"][0]["maxScore"], 0.6);
    assert_eq!(answer.feedback["citations"][0]["quote"], "先验概率");
    assert_eq!(
        answer.feedback["criteria"][0]["evidence"],
        "学生说明了资料中的核心概念"
    );
    assert_eq!(
        detail(&state, generated.set.id)
            .unwrap()
            .set
            .answered_questions,
        1
    );
    assert_eq!(detail(&state, generated.set.id).unwrap().set.score, 0.8);
    assert_eq!(
        subjective::submit(
            app.handle(),
            &state,
            generated.set.id,
            short.id,
            json!("重复提交")
        )
        .unwrap_err()
        .code,
        "answer_already_graded"
    );

    let essay = &generated.questions[1];
    ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": generated.set.id, "questionId": essay.id, "answer": "不知道"}),
    )
    .unwrap();
    let second = wait_answer(&state, generated.set.id, essay.id);
    assert_eq!(second.score, Some(0.2));
    assert_eq!(second.correct, Some(false));
    let completed = detail(&state, generated.set.id).unwrap();
    assert_eq!(completed.set.status, "completed");
    assert_eq!(completed.set.score, 1.0);
    assert_eq!(completed.set.max_score, 2.0);
    assert_eq!(completed.answered_question_ids.len(), 2);
    assert_eq!(mistakes(&state).unwrap().len(), 1);
    assert!(mistakes(&state).unwrap()[0]
        .reason
        .contains("未说明先验概率"));
    let store = state.store().unwrap();
    let scores = store
        .conn
        .prepare("SELECT score FROM learning_node_evidence WHERE kind = 'practice' ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get::<_, f64>(0))
        .unwrap()
        .collect::<std::result::Result<Vec<_>, _>>()
        .unwrap();
    assert_eq!(scores, vec![0.8, 0.2]);
    drop(store);
    assert_eq!(
        subjective::submit(
            app.handle(),
            &state,
            generated.set.id,
            short.id,
            json!("重复提交")
        )
        .unwrap_err()
        .code,
        "set_not_ready"
    );
}

#[test]
fn subjective_cancellation_resume_and_reset_wait_for_the_grader() {
    let gate = Arc::new(AtomicBool::new(false));
    let (app, state, server, plan_id) = prepare_with_gate(Some(Arc::clone(&gate)), false);
    gate.store(true, Ordering::Release);
    let generated = generate_subjectives(&app, &state, &plan_id, "immediate");
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let short = &generated.questions[0];
    gate.store(false, Ordering::Release);
    let calls = server.request_count();
    let pending = ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": generated.set.id, "questionId": short.id, "answer": "先验概率决定证据更新的起点。"}),
    )
    .unwrap();
    let answer_id = pending["answer"]["id"].as_i64().unwrap();
    let request_id = pending["answer"]["requestId"].as_str().unwrap().to_string();
    let deadline = Instant::now() + Duration::from_secs(5);
    while server.request_count() < calls + 1
        || providers::get_chat(&state.providers, &request_id)
            .is_err_and(|error| error.code == "not_found")
    {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let cancelling = ipc(
        &window,
        "cancel_practice_answer",
        json!({"answerId": answer_id}),
    )
    .unwrap();
    assert_eq!(cancelling["answers"][0]["status"], "cancelling");
    gate.store(true, Ordering::Release);
    let cancelled = wait_answer(&state, generated.set.id, short.id);
    assert_eq!(cancelled.status, "cancelled");
    assert_eq!(cancelled.score, None);
    assert_eq!(cancelled.response, "先验概率决定证据更新的起点。");
    ipc(
        &window,
        "resume_practice_answer",
        json!({"answerId": answer_id}),
    )
    .unwrap();
    let resumed = wait_answer(&state, generated.set.id, short.id);
    assert_eq!(resumed.status, "done");
    assert_eq!(resumed.score, Some(0.8));
    assert_eq!(resumed.request_id.as_deref(), Some("practice-grade-1-2"));
    assert_eq!(server.request_count(), calls + 2);
    assert_eq!(
        subjective::cancel(app.handle(), &state, answer_id)
            .unwrap_err()
            .code,
        "no_active_grading"
    );

    gate.store(false, Ordering::Release);
    let essay = &generated.questions[1];
    ipc(
        &window,
        "submit_practice_answer",
        json!({"setId": generated.set.id, "questionId": essay.id, "answer": "先验概率与后验概率的论证"}),
    )
    .unwrap();
    let stopping = state.shared();
    let worker = std::thread::spawn(move || stop(&stopping));
    std::thread::sleep(Duration::from_millis(20));
    gate.store(true, Ordering::Release);
    worker.join().unwrap().unwrap();
    state.store().unwrap().reset().unwrap();
    assert_eq!(
        detail(&state, generated.set.id).unwrap_err().code,
        "not_found"
    );
}

#[test]
fn interrupted_grading_reuses_a_completed_request_without_another_upstream_call() {
    let (app, state, server, plan_id) = prepare();
    let generated = generate_subjectives(&app, &state, &plan_id, "immediate");
    let question = &generated.questions[0];
    let store = state.store().unwrap();
    store.conn.execute(
        "INSERT INTO practice_answers (question_id, set_id, response_json, score, max_score, correct, feedback_json, status, grader_request_id, attempt, created_at) VALUES (?1, ?2, ?3, 0, 1, 0, '{\"status\":\"grading\"}', 'grading', 'practice-grade-1-1', 1, 'now')",
        params![question.id, generated.set.id, "\"先验概率是证据更新前的判断\""],
    ).unwrap();
    drop(store);
    let calls = server.request_count();
    let plan = learning::get(&state.learning, &plan_id).unwrap();
    let result = providers::stream_chat(
        app.handle(),
        &state.providers,
        "practice-grade-1-1",
        &plan.provider_id,
        &[ChatMessage {
            role: "system".into(),
            content: subjective::GRADING_SYSTEM.into(),
            ..Default::default()
        }],
        4096,
    )
    .unwrap();
    assert_eq!(result.status, "done");
    assert_eq!(server.request_count(), calls + 1);
    subjective::recover(app.handle(), &state).unwrap();
    assert_eq!(
        detail(&state, generated.set.id).unwrap().answers[0].status,
        "interrupted"
    );
    subjective::resume(app.handle(), &state, 1).unwrap();
    let answer = wait_answer(&state, generated.set.id, question.id);
    assert_eq!(answer.status, "done");
    assert_eq!(answer.score, Some(0.8));
    assert_eq!(answer.request_id.as_deref(), Some("practice-grade-1-1"));
    assert_eq!(server.request_count(), calls + 1);
    assert_eq!(
        subjective::resume(app.handle(), &state, 1).unwrap().answers[0].status,
        "done"
    );
    assert_eq!(server.request_count(), calls + 1);
}

#[test]
fn subjective_schema_migration_preserves_existing_objective_questions() {
    let store = Store::in_memory().unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_questions DROP COLUMN rubric_json", [])
        .unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_answers DROP COLUMN attempt", [])
        .unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_answers DROP COLUMN error_code", [])
        .unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_answers DROP COLUMN error_message", [])
        .unwrap();
    migrate(&store.conn).unwrap();
    store.conn.execute("INSERT INTO learning_plans (id, status, provider_id, input_json, created_at, updated_at) VALUES (1, 'done', 'p1', '{}', 'now', 'now')", []).unwrap();
    store.conn.execute("INSERT INTO practice_sets (mode, status, plan_id, provider_id, difficulty, feedback_mode, total_questions, created_at, updated_at) VALUES ('targeted', 'ready', 1, 'p1', 'adaptive', 'immediate', 1, 'now', 'now')", []).unwrap();
    store.conn.execute("INSERT INTO practice_questions (set_id, position, kind, prompt, options_json, answer_json, hint, explanation, citations_json, node_ids_json, difficulty) VALUES (1, 0, 'choice', '原题', '[]', '0', '', '', '[]', '[\"prob\"]', 1)", []).unwrap();
    store.conn.execute("INSERT INTO practice_answers (question_id, set_id, response_json, score, max_score, correct, feedback_json, status, created_at) VALUES (1, 1, '0', 1, 1, 1, '{}', 'done', 'now')", []).unwrap();
    let rubric: String = store
        .conn
        .query_row(
            "SELECT rubric_json FROM practice_questions WHERE id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(rubric, "[]");
    let attempt: u32 = store
        .conn
        .query_row(
            "SELECT attempt FROM practice_answers WHERE id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(attempt, 1);
}

#[test]
fn invalid_model_scores_never_become_learning_evidence() {
    let (app, state, server, plan_id) = prepare();
    let generated = generate_subjectives(&app, &state, &plan_id, "immediate");
    let question = &generated.questions[0];
    let pending = subjective::submit(
        app.handle(),
        &state,
        generated.set.id,
        question.id,
        json!("评分溢出"),
    )
    .unwrap();
    assert_eq!(pending.answer.status, "grading");
    let failed = wait_answer(&state, generated.set.id, question.id);
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.error.unwrap().code, "invalid_model_output");
    assert_eq!(failed.response, "评分溢出");
    assert_eq!(failed.score, None);
    assert_eq!(
        detail(&state, generated.set.id)
            .unwrap()
            .set
            .answered_questions,
        0
    );
    let store = state.store().unwrap();
    let evidence: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_node_evidence WHERE kind = 'practice'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(evidence, 0);
    drop(store);
    let calls = server.request_count();
    subjective::resume(app.handle(), &state, pending.answer.id).unwrap();
    let failed = wait_answer(&state, generated.set.id, question.id);
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.request_id.as_deref(), Some("practice-grade-1-2"));
    assert_eq!(server.request_count(), calls + 1);
    assert!(mistakes(&state).unwrap().is_empty());
}
