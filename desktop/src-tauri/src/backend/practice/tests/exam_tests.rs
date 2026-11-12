use super::*;

fn exam_input(plan_id: &str, count: u32, kinds: Vec<&str>) -> GenerateInput {
    GenerateInput {
        plan_id: plan_id.into(),
        stage_id: None,
        node_id: None,
        count: Some(count),
        difficulty: Some("comprehensive".into()),
        feedback_mode: Some("deferred".into()),
        kinds: Some(kinds.into_iter().map(str::to_owned).collect()),
        mode: Some("exam".into()),
        variant_of: None,
        time_limit_minutes: Some(45),
        pass_score_percent: Some(80),
    }
}

fn wait_for_grading(state: &PracticeState, question_id: i64) -> (String, f64) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let (status, score): (String, f64) = state
            .store()
            .unwrap()
            .conn
            .query_row(
                "SELECT status, score FROM practice_answers WHERE question_id = ?1",
                params![question_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        if !["grading", "cancelling"].contains(&status.as_str()) {
            return (status, score);
        }
        assert!(Instant::now() < deadline, "考试主观评分未在限时内结束");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn exam_config_deferred_feedback_unanswered_scores_and_idempotent_finish_are_real() {
    let (app, state, _server, plan_id) = prepare();
    let mut invalid = exam_input(&plan_id, 2, vec!["choice", "fill"]);
    invalid.feedback_mode = Some("immediate".into());
    assert_eq!(
        generate(app.handle(), &state, invalid.clone())
            .unwrap_err()
            .code,
        "invalid_feedback_mode"
    );
    invalid.feedback_mode = Some("deferred".into());
    invalid.time_limit_minutes = Some(0);
    assert_eq!(
        generate(app.handle(), &state, invalid.clone())
            .unwrap_err()
            .code,
        "invalid_exam_time_limit"
    );
    invalid.time_limit_minutes = Some(45);
    invalid.pass_score_percent = Some(101);
    assert_eq!(
        generate(app.handle(), &state, invalid.clone())
            .unwrap_err()
            .code,
        "invalid_pass_score"
    );
    invalid.pass_score_percent = Some(80);
    invalid.variant_of = Some(999);
    assert_eq!(
        generate(app.handle(), &state, invalid).unwrap_err().code,
        "variant_in_exam"
    );

    let started = generate(
        app.handle(),
        &state,
        exam_input(&plan_id, 2, vec!["choice", "fill"]),
    )
    .unwrap();
    let generated = wait_set(&state, started.set.id);
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    assert_eq!(generated.set.mode, "exam");
    assert_eq!(generated.set.feedback_mode, "deferred");
    assert_eq!(generated.set.exam_time_limit_minutes, Some(45));
    assert_eq!(generated.set.pass_score_percent, Some(80));
    // 47#1：计时从首次拉卷起算——生成完成时 deadline 尚未落位（生成耗时
    // 与等待不再吃掉考试时长）
    assert!(generated.set.exam_started_at.is_none());
    assert!(generated.set.exam_deadline.is_none());
    assert!(!generated.exam_expired);
    assert!(generated.exam_remaining_seconds.is_none());
    // 首次拉卷（get_practice_set 命令路径）起算——此处直接调用同一入口
    start_exam_timer(&state, generated.set.id).unwrap();
    // 幂等：重复拉卷不重置计时
    start_exam_timer(&state, generated.set.id).unwrap();
    let reopened = wait_set(&state, started.set.id);
    assert!(reopened.set.exam_started_at.is_some());
    assert!(reopened.set.exam_deadline.is_some());
    assert!(!reopened.exam_expired);
    assert!(reopened
        .exam_remaining_seconds
        .is_some_and(|seconds| seconds > 0 && seconds <= 2700));
    let started_at =
        OffsetDateTime::parse(reopened.set.exam_started_at.as_deref().unwrap(), &Rfc3339).unwrap();
    let deadline =
        OffsetDateTime::parse(reopened.set.exam_deadline.as_deref().unwrap(), &Rfc3339).unwrap();
    assert_eq!((deadline - started_at).whole_minutes(), 45);

    let choice = generated.questions[0].id;
    let answer = submit(app.handle(), &state, generated.set.id, choice, json!(1)).unwrap();
    assert_eq!(answer.answer.correct, None);
    assert_eq!(answer.set.status, "ready");
    assert_eq!(answer.set.answered_questions, 1);
    // 123#1：考试进行中 set 级分数遮蔽——逐题提交实时累计 practice_sets.score，
    // 若随 detail/AnswerResult 原样返回，考生 diff 两次响应即可推断每题对错
    // （98#1/101#4 同族旁路：载荷在场即泄露面，不看 UI 是否渲染）
    assert_eq!(answer.set.score, None);
    assert_eq!(answer.set.max_score, None);
    assert_eq!(detail(&state, generated.set.id).unwrap().set.score, None);
    assert_eq!(
        detail(&state, generated.set.id).unwrap().set.max_score,
        None
    );
    // 148#2：考试进行中回显本人作答原文，判分信息保持遮蔽
    let in_progress = detail(&state, generated.set.id).unwrap();
    assert!(!in_progress.answers.is_empty(), "本人已提交作答应回显");
    assert!(in_progress.answers.iter().all(|answer| answer.score.is_none()
        && answer.correct.is_none()
        && answer.feedback.is_null()), "交卷前不得泄露判分信息");
    assert_eq!(
        detail(&state, generated.set.id)
            .unwrap()
            .answered_question_ids,
        vec![choice]
    );
    // 98#1：考试进行中错题本与错题导出不得泄露解析——deferred 承诺
    // 「交卷后统一反馈」，错题行虽在逐题提交时已落库（reason 为完整解析），
    // 但未交卷集合被 mistakes() 过滤，客观题与主观题评分共用该过滤
    assert_eq!(mistakes(app.handle(), &state).unwrap().len(), 0);
    assert_eq!(
        export_mistakes(app.handle(), &state).unwrap()["mistakes"]
            .as_array()
            .unwrap()
            .len(),
        0
    );
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let finished = ipc(&window, "submit_exam", json!({"id": generated.set.id})).unwrap();
    assert_eq!(finished["set"]["status"], "completed");
    assert_eq!(finished["set"]["score"], 0.0);
    assert_eq!(finished["set"]["maxScore"], 2.0);
    assert_eq!(finished["answers"].as_array().unwrap().len(), 2);
    assert_eq!(finished["answers"][1]["response"], serde_json::Value::Null);
    assert_eq!(finished["answers"][1]["feedback"]["unanswered"], true);
    assert_eq!(finished["answers"][1]["score"], 0.0);
    // 03#13-27：未作答不再写入错题本，只保留真实答错的一道
    assert_eq!(mistakes(app.handle(), &state).unwrap().len(), 1);
    let store = state.store().unwrap();
    let evidence: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_node_evidence WHERE kind = 'exam'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(evidence, 2);
    let completed_events = store
        .events_since(0)
        .unwrap()
        .events
        .into_iter()
        .filter(|event| event.kind == "practice.exam_completed")
        .count();
    assert_eq!(completed_events, 1);
    drop(store);
    assert_eq!(
        exams::finalize(app.handle(), &state, generated.set.id)
            .unwrap()
            .set
            .status,
        "completed"
    );
    let completed_events = state
        .store()
        .unwrap()
        .events_since(0)
        .unwrap()
        .events
        .into_iter()
        .filter(|event| event.kind == "practice.exam_completed")
        .count();
    assert_eq!(completed_events, 1);
}

#[test]
fn expired_exam_rejects_new_answers_and_finalizes_only_as_unanswered() {
    let (app, state, _server, plan_id) = prepare();
    let started = generate(
        app.handle(),
        &state,
        exam_input(&plan_id, 1, vec!["choice"]),
    )
    .unwrap();
    let generated = wait_set(&state, started.set.id);
    assert_eq!(generated.set.status, "ready");
    state
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE practice_sets SET exam_deadline = '2000-01-01T00:00:00Z' WHERE id = ?1",
            params![generated.set.id],
        )
        .unwrap();
    let expired = detail(&state, generated.set.id).unwrap();
    assert!(expired.exam_expired);
    assert_eq!(expired.exam_remaining_seconds, Some(0));
    let question = generated.questions[0].id;
    assert_eq!(
        submit(app.handle(), &state, generated.set.id, question, json!(0))
            .unwrap_err()
            .code,
        "exam_deadline_exceeded"
    );
    let finished = exams::finalize(app.handle(), &state, generated.set.id).unwrap();
    assert_eq!(finished.set.status, "completed");
    assert_eq!(finished.set.score, Some(0.0));
    assert_eq!(finished.answers[0].feedback["unanswered"], true);
    assert_eq!(finished.answers[0].score, Some(0.0));
}

#[test]
fn exam_waits_for_subjective_grading_before_finishing() {
    let gate = Arc::new(AtomicBool::new(false));
    let (app, state, _server, plan_id) = prepare_with_gate(Some(Arc::clone(&gate)), false);
    gate.store(true, Ordering::Release);
    let started = generate(
        app.handle(),
        &state,
        exam_input(&plan_id, 2, vec!["short", "essay"]),
    )
    .unwrap();
    let generated = wait_set(&state, started.set.id);
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    let short = generated.questions[0].id;
    gate.store(false, Ordering::Release);
    subjective::submit(
        app.handle(),
        &state,
        generated.set.id,
        short,
        json!("先验概率是证据更新前的判断"),
    )
    .unwrap();
    assert_eq!(
        exams::finalize(app.handle(), &state, generated.set.id)
            .unwrap_err()
            .code,
        "grading_in_progress"
    );
    gate.store(true, Ordering::Release);
    let (status, score) = wait_for_grading(&state, short);
    assert_eq!(status, "done");
    assert_eq!(score, 0.8);
    assert_eq!(
        detail(&state, generated.set.id).unwrap().set.status,
        "ready"
    );
    // 148#2：考试进行中回显本人作答原文，判分信息保持遮蔽
    let in_progress = detail(&state, generated.set.id).unwrap();
    assert!(!in_progress.answers.is_empty(), "本人已提交作答应回显");
    assert!(in_progress.answers.iter().all(|answer| answer.score.is_none()
        && answer.correct.is_none()
        && answer.feedback.is_null()), "交卷前不得泄露判分信息");
    let finished = exams::finalize(app.handle(), &state, generated.set.id).unwrap();
    assert_eq!(finished.set.status, "completed");
    assert_eq!(finished.set.score, Some(0.8));
    assert_eq!(finished.answers.len(), 2);
    assert_eq!(finished.answers[0].feedback["criteria"][0]["score"], 0.6);
    assert_eq!(finished.answers[1].feedback["unanswered"], true);
    let store = state.store().unwrap();
    let scores = store
        .conn
        .prepare("SELECT score FROM learning_node_evidence WHERE kind = 'exam' ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get::<_, f64>(0))
        .unwrap()
        .collect::<std::result::Result<Vec<_>, _>>()
        .unwrap();
    // 101#4：考试 evidence 统一在交卷时补写（原顺序 0.8 先写自主观评分
    // 即时路径，现按答案行序回填）——断言改为顺序无关
    let mut scores = scores;
    scores.sort_by(|left, right| left.total_cmp(right));
    assert_eq!(scores, vec![0.0, 0.8]);
}

#[test]
fn exam_schema_migration_adds_persistent_timer_configuration() {
    let store = Store::in_memory().unwrap();
    store
        .conn
        .execute_batch(
            "ALTER TABLE practice_sets DROP COLUMN exam_time_limit_minutes;
         ALTER TABLE practice_sets DROP COLUMN exam_started_at;
         ALTER TABLE practice_sets DROP COLUMN exam_deadline;
         ALTER TABLE practice_sets DROP COLUMN pass_score_percent;",
        )
        .unwrap();
    migrate(&store.conn).unwrap();
    let columns: i64 = store.conn.query_row(
        "SELECT COUNT(*) FROM pragma_table_info('practice_sets') WHERE name IN ('exam_time_limit_minutes', 'exam_started_at', 'exam_deadline', 'pass_score_percent')",
        [],
        |row| row.get(0),
    ).unwrap();
    assert_eq!(columns, 4);
}

#[test]
fn abandoning_a_permanently_failed_grading_unblocks_finalize() {
    // 123#7：主观评分永久失败的考试原先无法交卷（grading_in_progress 死锁，
    // 作答不能重答也没有删除命令）——显式放弃后 pending=0，交卷按 0 分计
    let (app, state, _server, plan_id) = prepare();
    let started = generate(
        app.handle(),
        &state,
        exam_input(&plan_id, 2, vec!["short", "essay"]),
    )
    .unwrap();
    let generated = wait_set(&state, started.set.id);
    assert_eq!(generated.set.status, "ready", "{:?}", generated.set.error);
    let first = generated.questions[0].id;
    subjective::submit(
        app.handle(),
        &state,
        generated.set.id,
        first,
        json!("评分溢出"),
    )
    .unwrap();
    let (status, _) = wait_for_grading(&state, first);
    assert_eq!(status, "failed");
    assert_eq!(
        exams::finalize(app.handle(), &state, generated.set.id)
            .unwrap_err()
            .code,
        "grading_in_progress"
    );
    // 恢复评分入口同源：失败作答经详情枚举（pending_answers）
    let failed_id = detail(&state, generated.set.id)
        .unwrap()
        .answers
        .iter()
        .find(|answer| answer.question_id == first)
        .unwrap()
        .id;
    let abandoned = subjective::abandon(app.handle(), &state, failed_id).unwrap();
    assert_eq!(abandoned.set.status, "ready");
    let finished = exams::finalize(app.handle(), &state, generated.set.id).unwrap();
    assert_eq!(finished.set.status, "completed");
    assert_eq!(finished.set.score, Some(0.0));
    assert_eq!(finished.set.max_score, Some(2.0));
    // 交卷补写把放弃题与未作答题都按 0 分计入证据（101#4 回填路径）
    let store = state.store().unwrap();
    let scores: Vec<f64> = store
        .conn
        .prepare("SELECT score FROM learning_node_evidence WHERE kind = 'exam' ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<std::result::Result<Vec<_>, _>>()
        .unwrap();
    assert_eq!(scores.len(), 2);
    assert!(scores.iter().all(|score| *score == 0.0));
}
