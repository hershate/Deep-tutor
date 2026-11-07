use super::*;

fn seed_choice_mistake(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &PracticeState,
    plan_id: &str,
) -> (PracticeSetDetail, i64) {
    let started = generate(
        app.handle(),
        state,
        input(plan_id, 2, vec!["choice", "fill"], "immediate"),
    )
    .unwrap();
    let generated = wait_set(state, started.set.id);
    assert_eq!(generated.set.status, "ready");
    let source = generated.questions[0].id;
    let answer = submit(app.handle(), state, generated.set.id, source, json!(1)).unwrap();
    assert_eq!(answer.answer.correct, Some(false));
    (generated, source)
}

fn variant_input(plan_id: &str, source: i64, kind: &str, count: u32) -> GenerateInput {
    GenerateInput {
        plan_id: plan_id.into(),
        stage_id: None,
        node_id: None,
        count: Some(count),
        difficulty: Some("adaptive".into()),
        feedback_mode: None,
        kinds: Some(vec![kind.into()]),
        mode: None,
        variant_of: Some(source),
        time_limit_minutes: None,
        pass_score_percent: None,
    }
}

#[test]
fn objective_variants_preserve_lineage_and_update_the_original_mistake_atomically() {
    let (app, state, _server, plan_id) = prepare();
    let (original, source) = seed_choice_mistake(&app, &state, &plan_id);
    assert_eq!(
        generate(
            app.handle(),
            &state,
            variant_input(&plan_id, source, "fill", 3)
        )
        .unwrap_err()
        .code,
        "invalid_variant_kind"
    );
    assert_eq!(
        generate(
            app.handle(),
            &state,
            variant_input(&plan_id, original.questions[1].id, "fill", 1)
        )
        .unwrap_err()
        .code,
        "variant_source_not_mistake"
    );
    let started = generate(
        app.handle(),
        &state,
        variant_input(&plan_id, source, "choice", 3),
    )
    .unwrap();
    let generated = wait_set(&state, started.set.id);
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    assert_eq!(generated.set.variant_of, Some(source));
    assert_eq!(generated.set.feedback_mode, "immediate");
    assert_eq!(generated.questions.len(), 3);
    assert!(generated.questions.iter().all(|question| {
        question.kind == "choice"
            && question.variant_of == Some(source)
            && question.prompt != original.questions[0].prompt
            && question.node_ids == vec!["prob"]
    }));
    let first = generated.questions[0].id;
    let answer = submit(app.handle(), &state, generated.set.id, first, json!(0)).unwrap();
    assert_eq!(answer.answer.correct, Some(true));
    let original_mistake = mistakes(&state)
        .unwrap()
        .into_iter()
        .find(|item| item.question_id == source)
        .unwrap();
    assert!(original_mistake.mastered);
    assert_eq!(original_mistake.mastered_question_id, Some(first));
    assert_eq!(original_mistake.plan_id, plan_id);
    assert_eq!(original_mistake.set_id, original.set.id);
    assert_eq!(original_mistake.kind, "choice");
    assert!(state
        .store()
        .unwrap()
        .events_since(0)
        .unwrap()
        .events
        .iter()
        .any(|event| event.kind == "practice.mistake_mastered_by_variant"
            && event.payload["sourceQuestionId"] == source
            && event.payload["variantQuestionId"] == first));

    let second = generated.questions[1].id;
    submit(app.handle(), &state, generated.set.id, second, json!(1)).unwrap();
    let original_mistake = mistakes(&state)
        .unwrap()
        .into_iter()
        .find(|item| item.question_id == source)
        .unwrap();
    assert!(original_mistake.mastered);
    assert_eq!(original_mistake.mastered_question_id, Some(first));
    assert!(mistakes(&state)
        .unwrap()
        .iter()
        .any(|item| item.question_id == second && !item.mastered));
    set_mistake_mastered(app.handle(), &state, source, false).unwrap();
    let original_mistake = mistakes(&state)
        .unwrap()
        .into_iter()
        .find(|item| item.question_id == source)
        .unwrap();
    assert!(!original_mistake.mastered);
    assert_eq!(original_mistake.mastered_question_id, None);
    set_mistake_mastered(app.handle(), &state, source, true).unwrap();
    let original_mistake = mistakes(&state)
        .unwrap()
        .into_iter()
        .find(|item| item.question_id == source)
        .unwrap();
    assert!(original_mistake.mastered);
    assert_eq!(original_mistake.mastered_question_id, None);
}

fn wait_subjective(state: &PracticeState, set_id: i64, question_id: i64) -> AnswerView {
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
        assert!(Instant::now() < deadline, "变式主观评分未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn subjective_variants_use_the_same_rubric_mastery_evidence() {
    let (app, state, _server, plan_id) = prepare();
    let started = generate(
        app.handle(),
        &state,
        GenerateInput {
            plan_id: plan_id.clone(),
            stage_id: None,
            node_id: None,
            count: Some(2),
            difficulty: Some("adaptive".into()),
            feedback_mode: Some("immediate".into()),
            kinds: Some(vec!["short".into(), "essay".into()]),
            mode: None,
            variant_of: None,
            time_limit_minutes: None,
            pass_score_percent: None,
        },
    )
    .unwrap();
    let original = wait_set(&state, started.set.id);
    assert_eq!(original.set.status, "ready");
    let source = original.questions[0].id;
    subjective::submit(
        app.handle(),
        &state,
        original.set.id,
        source,
        json!("不知道"),
    )
    .unwrap();
    let wrong = wait_subjective(&state, original.set.id, source);
    assert_eq!(wrong.correct, Some(false));
    let started = generate(
        app.handle(),
        &state,
        variant_input(&plan_id, source, "short", 1),
    )
    .unwrap();
    let variant = wait_set(&state, started.set.id);
    assert_eq!(variant.set.status, "ready", "{:?}", variant.set.error);
    assert_eq!(variant.questions.len(), 1);
    assert_eq!(variant.questions[0].kind, "short");
    assert_eq!(variant.questions[0].variant_of, Some(source));
    assert_eq!(variant.questions[0].rubric.len(), 2);
    let question = variant.questions[0].id;
    subjective::submit(
        app.handle(),
        &state,
        variant.set.id,
        question,
        json!("先验概率决定证据更新的起点，资料说明了这一关系。"),
    )
    .unwrap();
    let answer = wait_subjective(&state, variant.set.id, question);
    assert_eq!(answer.correct, Some(true));
    let original_mistake = mistakes(&state)
        .unwrap()
        .into_iter()
        .find(|item| item.question_id == source)
        .unwrap();
    assert!(original_mistake.mastered);
    assert_eq!(original_mistake.mastered_question_id, Some(question));
}

#[test]
fn variant_schema_migration_retains_existing_learning_records() {
    let store = Store::in_memory().unwrap();
    store
        .conn
        .execute("ALTER TABLE practice_sets DROP COLUMN variant_of", [])
        .unwrap();
    store
        .conn
        .execute(
            "ALTER TABLE practice_mistakes DROP COLUMN mastered_question_id",
            [],
        )
        .unwrap();
    migrate(&store.conn).unwrap();
    let set_column: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM pragma_table_info('practice_sets') WHERE name = 'variant_of'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    let mistake_column: i64 = store.conn.query_row(
        "SELECT COUNT(*) FROM pragma_table_info('practice_mistakes') WHERE name = 'mastered_question_id'",
        [],
        |row| row.get(0),
    ).unwrap();
    assert_eq!(set_column, 1);
    assert_eq!(mistake_column, 1);
}

#[test]
fn variant_mastery_propagates_up_the_chain_to_the_original_mistake() {
    let (app, state, _server, plan_id) = prepare();
    let (_original, source) = seed_choice_mistake(&app, &state, &plan_id);
    // V1：原错题的变式，答错成为新错题
    let v1_set = wait_set(
        &state,
        generate(
            app.handle(),
            &state,
            variant_input(&plan_id, source, "choice", 3),
        )
        .unwrap()
        .set
        .id,
    );
    let v1 = v1_set.questions[0].id;
    let wrong = submit(app.handle(), &state, v1_set.set.id, v1, json!(1)).unwrap();
    assert_eq!(wrong.answer.correct, Some(false));
    // V2：直接落库一条以 V1 为源的二级变式（桩服务器固定返回同题面，
    // 二级变式走模型会因"重复"被判无效；此处验证的是掌握传播而非生成）
    let store = state.store().unwrap();
    store
        .conn
        .execute(
            "INSERT INTO practice_questions (set_id, position, kind, prompt, options_json, answer_json, hint, explanation, citations_json, node_ids_json, variant_of, rubric_json, difficulty) SELECT set_id, position + 10, kind, prompt || '（二级变式）', options_json, answer_json, hint, explanation, citations_json, node_ids_json, ?1, rubric_json, difficulty FROM practice_questions WHERE id = ?1",
            params![v1],
        )
        .unwrap();
    let v2 = store.conn.last_insert_rowid();
    drop(store);
    let graded = submit(app.handle(), &state, v1_set.set.id, v2, json!(0)).unwrap();
    assert_eq!(graded.answer.correct, Some(true));
    // 03#13-25：掌握沿 variant_of 链传播——V1 与原错题一并标记掌握
    let rows = mistakes(&state).unwrap();
    for question in [source, v1] {
        let row = rows
            .iter()
            .find(|item| item.question_id == question)
            .unwrap();
        assert!(row.mastered, "question {question} should be mastered");
        assert_eq!(row.mastered_question_id, Some(v2));
    }
}
