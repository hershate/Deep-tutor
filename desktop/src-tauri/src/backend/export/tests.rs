use super::*;
use crate::backend::BackendState;
use lopdf::Document;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Manager;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

fn fixture_result() -> Value {
    let check = || json!({"status": "pass", "details": "来自真实结构校验"});
    json!({
        "goal": "贝叶斯 <script> 学习", "level": "基础概率", "deadline": "2099-12-31", "milestone": null, "milestoneDate": null,
        "graph": {
            "nodes": [
                {"id": "probability", "title": "条件概率", "summary": "在已知条件下计算概率", "difficulty": 1, "required": true, "estimatedMinutes": 20, "citations": [{"materialId": 1, "quote": "概率"}]},
                {"id": "bayes", "title": "贝叶斯更新", "summary": "使用证据更新判断", "difficulty": 2, "required": true, "estimatedMinutes": 30, "citations": []}
            ], "edges": [{"from": "probability", "to": "bayes"}]
        },
        "stages": [{"id": "stage-1", "title": "理解并应用", "nodeIds": ["probability", "bayes"], "difficulty": 2, "minutes": 50, "startDate": "2099-01-01", "endDate": "2099-01-02"}],
        "schedule": {"startDate": "2099-01-01", "endDate": "2099-01-02", "totalMinutes": 50, "days": 2, "withinDeadline": true, "milestoneDate": null, "withinMilestone": null, "entries": [{"date": "2099-01-01", "stageId": "stage-1", "nodeIds": ["probability", "bayes"], "minutes": 50}]},
        "resources": {"items": [{"materialId": 1, "title": "教材原文", "sha256": "abc123", "kind": "txt", "verified": true}], "externalSearch": {"status": "disabled", "reason": "本地资料", "queries": []}},
        "validation": {"dependencyClosure": check(), "difficultyStep": check(), "cognitiveLoad": check(), "milestoneFeasibility": check(), "gapAnalysis": check(), "passed": true, "fixes": []}, "warnings": []
    })
}

fn setup() -> (
    tauri::App<tauri::test::MockRuntime>,
    LearningState,
    tempfile::TempDir,
) {
    let learning = LearningState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: learning.provider_state(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::for_tests().unwrap(),
            practice: crate::backend::practice::PracticeState::for_tests().unwrap(),
            review: crate::backend::review::ReviewState::for_tests().unwrap(),
            knowledge: crate::backend::knowledge::KnowledgeState::for_tests().unwrap(),
            community: crate::backend::community::CommunityState::for_tests().unwrap(),
            plugins: crate::backend::plugins::PluginState::for_tests().unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            commands::export_content,
            commands::export_notes_archive,
            crate::backend::commands::events_since
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let store = learning.store().unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES ('done', 'provider-private', ?1, ?2, ?3, ?3)", params![json!({"goal": "贝叶斯 <script> 学习", "level": "基础概率", "dailyMinutes": 45, "deadline": "2099-12-31", "materialIds": [], "providerId": "provider-private", "milestone": null, "milestoneDate": null}).to_string(), fixture_result().to_string(), timestamp]).unwrap();
    let plan_id = store.conn.last_insert_rowid();
    store.conn.execute("INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (?1, 1, ?2)", params![plan_id, timestamp]).unwrap();
    store.conn.execute("INSERT INTO learning_lectures (plan_id, stage_id, version, status, current_unit, updated_at) VALUES (?1, 'stage-1', 1, 'done', 0, ?2)", params![plan_id, timestamp]).unwrap();
    store.conn.execute("INSERT INTO learning_lecture_versions (plan_id, stage_id, version, created_at) VALUES (?1, 'stage-1', 1, ?2)", params![plan_id, timestamp]).unwrap();
    let content = json!({"title": "真实讲义单元", "overview": "从实际持久化讲义导出", "sections": [{"heading": "条件概率", "content": "先确定条件，再计算概率。"}], "examples": [{"title": "例题", "content": "使用一次真实观察。"}], "checkpoints": [{"question": "什么是条件概率？", "answer": "已知某条件时的概率。"}], "citations": [{"materialId": 1, "quote": "概率"}]});
    store.conn.execute("INSERT INTO learning_lecture_units (plan_id, stage_id, version, unit_index, node_id, status, content_json, updated_at) VALUES (?1, 'stage-1', 1, 0, 'probability', 'done', ?2, ?3)", params![plan_id, content.to_string(), timestamp]).unwrap();
    store.replace_collection("notes", 0, &json!([{"id": 1, "title": "=真实笔记标题", "excerpt": "摘要", "tag": "复盘", "updated": "now", "content": "笔记正文 <img src=x>"}])).unwrap();
    let review = ReviewState::new(learning.database_path(), &learning);
    crate::backend::review::create(
        app.handle(),
        &review,
        crate::backend::review::CardInput {
            front: "真实卡片问题".into(),
            back: "真实卡片答案".into(),
            tag: Some("概率".into()),
            plan_id: None,
            node_id: None,
        },
    )
    .unwrap();
    (app, learning, tempfile::tempdir().unwrap())
}

fn ipc(
    app: &tauri::App<tauri::test::MockRuntime>,
    input: Value,
    destination: &Path,
) -> std::result::Result<Value, Value> {
    let window = app.get_webview_window("main").unwrap();
    tauri::test::get_ipc_response(
        &window,
        tauri::webview::InvokeRequest {
            cmd: "export_content".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(
                json!({"input": input, "destination": destination.to_string_lossy()}),
            ),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .map(|body| body.deserialize::<Value>().unwrap())
}

fn input(scope: &str, format: &str) -> Value {
    json!({"scope": scope, "format": format, "planId": "plan-1", "stageId": null, "unitIndex": null, "version": null})
}

fn exported(
    app: &tauri::App<tauri::test::MockRuntime>,
    input: Value,
    directory: &Path,
    name: &str,
) -> (Value, Vec<u8>, PathBuf) {
    let path = directory.join(name);
    let result = ipc(app, input, &path).unwrap();
    let bytes = std::fs::read(&path).unwrap();
    assert_eq!(result["bytes"], bytes.len());
    assert_eq!(result["sha256"], format!("{:x}", Sha256::digest(&bytes)));
    (result, bytes, path)
}

#[test]
fn text_html_opml_csv_and_ics_use_actual_persisted_content() {
    let (app, _, directory) = setup();
    let markdown = exported(&app, input("plan", "md"), directory.path(), "plan.md").1;
    let markdown = String::from_utf8(markdown).unwrap();
    assert!(markdown.contains("条件概率：在已知条件下计算概率"));
    assert!(markdown.contains("教材原文"));
    let html =
        String::from_utf8(exported(&app, input("plan", "html"), directory.path(), "plan.html").1)
            .unwrap();
    assert!(html.contains("贝叶斯 &lt;script&gt; 学习"));
    assert!(!html.contains("贝叶斯 <script> 学习"));
    let opml =
        String::from_utf8(exported(&app, input("plan", "opml"), directory.path(), "plan.opml").1)
            .unwrap();
    assert!(opml.contains("理解并应用"));
    assert!(opml.contains("&lt;script&gt;"));
    let txt =
        String::from_utf8(exported(&app, input("plan", "txt"), directory.path(), "plan.txt").1)
            .unwrap();
    assert!(txt.contains("每日可投入：45 分钟"));
    let schedule = String::from_utf8(
        exported(
            &app,
            input("schedule", "ics"),
            directory.path(),
            "schedule.ics",
        )
        .1,
    )
    .unwrap();
    assert!(
        schedule.contains("DTSTART;VALUE=DATE:20990101"),
        "{schedule}"
    );
    assert!(schedule.contains("DTEND;VALUE=DATE:20990102"), "{schedule}");
    assert!(schedule.contains("SUMMARY:贝叶斯"));
    let csv = String::from_utf8(
        exported(
            &app,
            input("knowledge", "csv"),
            directory.path(),
            "notes.csv",
        )
        .1,
    )
    .unwrap();
    assert!(csv.contains("\"'=真实笔记标题\""));
    assert!(csv.contains("笔记正文 <img src=x>"));
}

#[cfg(windows)]
#[test]
fn pdf_is_parseable_embedded_unicode_and_contains_the_real_plan() {
    let (app, _, directory) = setup();
    let (_, bytes, path) = exported(&app, input("plan", "pdf"), directory.path(), "plan.pdf");
    assert!(bytes.starts_with(b"%PDF-"));
    let pdf = Document::load(&path).unwrap();
    assert!(!pdf.get_pages().is_empty());
    let text = pdf
        .extract_text(&pdf.get_pages().keys().copied().collect::<Vec<_>>())
        .unwrap();
    assert!(text.contains("贝叶斯"), "{text}");
    assert!(text.contains("条件概率"), "{text}");
    assert!(pdf.extract_text(&[1]).unwrap().contains("真实结构校验"));
}

#[test]
fn apkg_is_a_real_zip_with_queryable_cards_for_every_supported_scope() {
    let (app, _, directory) = setup();
    for (scope, name) in [
        ("plan", "plan.apkg"),
        ("lecture", "lecture.apkg"),
        ("review", "review.apkg"),
        ("knowledge", "notes.apkg"),
    ] {
        let mut request = input(scope, "apkg");
        if scope == "lecture" {
            request["stageId"] = json!("stage-1");
            request["unitIndex"] = json!(0);
        }
        let (_, _, path) = exported(&app, request, directory.path(), name);
        let file = std::fs::File::open(path).unwrap();
        let mut archive = zip::ZipArchive::new(file).unwrap();
        assert_eq!(
            std::io::Read::read_to_string(
                &mut archive.by_name("media").unwrap(),
                &mut String::new()
            )
            .unwrap(),
            2
        );
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(
            &mut archive.by_name("collection.anki2").unwrap(),
            &mut bytes,
        )
        .unwrap();
        let collection = directory.path().join(format!("{scope}.anki2"));
        std::fs::write(&collection, bytes).unwrap();
        let connection = Connection::open(collection).unwrap();
        let fields = connection
            .query_row("SELECT flds FROM notes ORDER BY id LIMIT 1", [], |row| {
                row.get::<_, String>(0)
            })
            .unwrap();
        assert!(!fields.is_empty());
        assert!(fields.contains('\u{1f}'));
        let decks = connection
            .query_row("SELECT decks FROM col", [], |row| row.get::<_, String>(0))
            .unwrap();
        assert!(decks.contains(scope_name(scope)));
    }
}

fn scope_name(scope: &str) -> &'static str {
    match scope {
        "plan" => "贝叶斯",
        "lecture" => "真实讲义单元",
        "review" => "复习卡片",
        "knowledge" => "笔记与知识库",
        _ => "",
    }
}

#[test]
fn lecture_knowledge_and_rejections_are_real_commands_with_safe_events() {
    let (app, state, directory) = setup();
    let mut lecture = input("lecture", "md");
    lecture["stageId"] = json!("stage-1");
    lecture["unitIndex"] = json!(0);
    let text =
        String::from_utf8(exported(&app, lecture.clone(), directory.path(), "unit.md").1).unwrap();
    assert!(text.contains("先确定条件，再计算概率"));
    lecture["unitIndex"] = json!(9);
    assert_eq!(
        ipc(&app, lecture, &directory.path().join("missing.md")).unwrap_err()["code"],
        "lecture_not_ready"
    );
    assert_eq!(
        ipc(
            &app,
            input("plan", "ics"),
            &directory.path().join("invalid.ics")
        )
        .unwrap_err()["code"],
        "invalid_export_combination"
    );
    assert_eq!(
        ipc(
            &app,
            input("plan", "md"),
            &directory.path().join("wrong.txt")
        )
        .unwrap_err()["code"],
        "invalid_export_destination"
    );
    let events = tauri::test::get_ipc_response(
        &app.get_webview_window("main").unwrap(),
        tauri::webview::InvokeRequest {
            cmd: "events_since".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(json!({"lastId": 0})),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .unwrap()
    .deserialize::<Value>()
    .unwrap();
    let exported_events = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|event| event["kind"] == "content.exported")
        .collect::<Vec<_>>();
    assert!(!exported_events.is_empty());
    assert!(!exported_events
        .iter()
        .any(|event| event.to_string().contains("先确定条件")
            || event.to_string().contains("provider-private")));
    assert_eq!(
        crate::backend::export::export(
            app.handle(),
            &state,
            serde_json::from_value(input("review", "csv")).unwrap(),
            &directory.path().join("cards.csv")
        )
        .unwrap()
        .format
        .extension(),
        "csv"
    );
}

#[test]
fn notes_zip_is_generated_by_native_ipc_with_real_markdown_and_manifest() {
    let (app, _, directory) = setup();
    let path = directory.path().join("notes.zip");
    let result = tauri::test::get_ipc_response(
        &app.get_webview_window("main").unwrap(),
        tauri::webview::InvokeRequest {
            cmd: "export_notes_archive".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(json!({"destination": path.to_string_lossy()})),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .unwrap()
    .deserialize::<Value>()
    .unwrap();
    assert_eq!(result["count"], 1);
    let mut archive = zip::ZipArchive::new(std::fs::File::open(path).unwrap()).unwrap();
    assert_eq!(archive.len(), 2);
    let mut markdown = String::new();
    std::io::Read::read_to_string(
        &mut archive.by_name("知舟笔记/001-=真实笔记标题.md").unwrap(),
        &mut markdown,
    )
    .unwrap();
    assert!(markdown.contains("笔记正文 <img src=x>"));
    let mut manifest = String::new();
    std::io::Read::read_to_string(
        &mut archive.by_name("manifest.json").unwrap(),
        &mut manifest,
    )
    .unwrap();
    let manifest: Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(manifest["count"], 1);
}
