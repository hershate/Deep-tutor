use super::*;
use crate::backend::BackendState;
use rusqlite::params;
use serde_json::{json, Value};
use sha2::Digest;
use std::sync::Mutex;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

fn setup(learning: &LearningState) -> tauri::App<tauri::test::MockRuntime> {
    let path = learning.database_path();
    let providers = learning.provider_state();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(path, &providers, learning),
            practice: crate::backend::practice::PracticeState::new(path, &providers, learning),
            review: crate::backend::review::ReviewState::new(path, learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                path,
                &providers,
                &learning.material_state(),
            ),
            community: crate::backend::community::CommunityState::new(path),
            plugins: crate::backend::plugins::PluginState::new(path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(learning),
        })
        .invoke_handler(tauri::generate_handler![
            commands::export_full_backup,
            commands::preview_full_backup,
            commands::import_full_backup
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    app
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

fn plan_result(material_id: i64) -> Value {
    let check = || json!({"status": "pass", "details": "备份结构校验"});
    json!({
        "goal": "备份中的真实方案", "level": "基础", "deadline": "2099-12-31", "milestone": null, "milestoneDate": null,
        "graph": {"nodes": [{"id": "node-1", "title": "真实节点", "summary": "节点摘要", "difficulty": 1, "required": true, "estimatedMinutes": 20, "citations": [{"materialId": material_id, "quote": "备份原文"}]}], "edges": []},
        "stages": [{"id": "stage-1", "title": "阶段", "nodeIds": ["node-1"], "difficulty": 1, "minutes": 20, "startDate": "2099-01-01", "endDate": "2099-01-01"}],
        "schedule": {"startDate": "2099-01-01", "endDate": "2099-01-01", "totalMinutes": 20, "days": 1, "withinDeadline": true, "milestoneDate": null, "withinMilestone": null, "entries": [{"date": "2099-01-01", "stageId": "stage-1", "nodeIds": ["node-1"], "minutes": 20}]},
        "resources": {"items": [{"materialId": material_id, "title": "原始资料", "sha256": "abc", "kind": "txt", "verified": true}], "externalSearch": {"status": "disabled", "reason": "本地", "queries": []}},
        "validation": {"dependencyClosure": check(), "difficultyStep": check(), "cognitiveLoad": check(), "milestoneFeasibility": check(), "gapAnalysis": check(), "passed": true, "fixes": []}, "warnings": []
    })
}

fn seed_source(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &LearningState,
) -> (String, String) {
    let store = state.store().unwrap();
    store.bootstrap().unwrap();
    store.replace_collection("preferences", 0, &json!({"proactive": true, "hintsOnly": true, "notifications": true, "weeklyReport": true, "highContrast": false, "reducedMotion": false, "largeText": false, "theme": "dark"})).unwrap();
    store
        .put_draft(
            "deep-tutor-draft-new",
            &json!({"title": "源草稿", "content": "保留源草稿", "tag": "复盘", "savedAt": 1}),
        )
        .unwrap();
    store.replace_collection("tasks", 0, &json!([{"id": 1, "title": "源任务", "subject": "数学", "duration": 20, "done": false, "kind": "学习"}])).unwrap();
    store.replace_collection("notes", 0, &json!([
        {"id": 1, "title": "源笔记", "excerpt": "摘要", "tag": "复盘", "updated": "now", "content": "真实笔记内容"},
        {"id": 2, "title": "第二条源笔记", "excerpt": "连续标识", "tag": "复盘", "updated": "now", "content": "不得与第一条冲突"}
    ])).unwrap();
    store.replace_collection("projects", 0, &json!([{"id": 1, "title": "源项目", "description": "真实项目", "due": "2099-01-01", "progress": 20, "tag": "项目", "milestones": []}])).unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    store.conn.execute("INSERT INTO provider_profiles (name, preset, kind, protocol, base_url, default_model, models_json, enabled, created_at, updated_at) VALUES ('源模型', 'custom', '本地模型', 'OpenAI Chat', 'http://127.0.0.1:1', 'test-model', '[\"test-model\"]', 1, ?1, ?1)", params![timestamp]).unwrap();
    let material = crate::backend::materials::import_text(
        app.handle(),
        &state.material_state(),
        "原始资料.txt".into(),
        "备份原文：概率与贝叶斯".into(),
    )
    .unwrap()
    .material;
    let result = plan_result(material.id);
    let artifacts = json!({"step0": {"snapshot": [{"id": material.id, "title": "原始资料", "sha256": material.sha256.clone(), "text": "备份原文：概率与贝叶斯", "warning": null}]}});
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, artifacts_json, result_json, created_at, updated_at) VALUES ('done', 'p1', ?1, ?4, ?2, ?3, ?3)", params![json!({"goal": "备份中的真实方案", "level": "基础", "dailyMinutes": 45, "deadline": "2099-12-31", "materialIds": [material.id], "providerId": "p1", "milestone": null, "milestoneDate": null}).to_string(), result.to_string(), timestamp, artifacts.to_string()]).unwrap();
    store
        .conn
        .execute(
            "INSERT INTO learning_plan_materials (plan_id, material_id) VALUES (1, ?1)",
            params![material.id],
        )
        .unwrap();
    store.conn.execute("INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (1, 1, ?1)", params![timestamp]).unwrap();
    store.conn.execute("INSERT INTO learning_annotations (plan_id, stage_id, note_id, created_at) VALUES (1, 'stage-1', 1, ?1)", params![timestamp]).unwrap();
    // 34#7：请求行携带会话外键 agent_session_id=1——导入时必须按表间依赖
    // 重映射到目标库会话 id，不得残留源库字面值
    store.conn.execute("INSERT INTO provider_requests (request_id, provider_id, model, status, output, attempts, agent_session_id, created_at, updated_at) VALUES ('request-1', 1, 'test-model', 'done', '已保存的真实模型输出', 1, 1, ?1, ?1)", params![timestamp]).unwrap();
    store.conn.execute("INSERT INTO learning_plan_steps (plan_id, step_index, status, request_id, created_at, updated_at) VALUES (1, 0, 'done', 'request-1', ?1, ?1)", params![timestamp]).unwrap();
    let share = crate::backend::sharing::create(
        app.handle(),
        &crate::backend::sharing::SharingState::new(state),
        "plan-1",
        None,
    )
    .unwrap();
    crate::backend::plugins::install(app.handle(), &crate::backend::plugins::PluginState::new(state.database_path()).unwrap(), json!({"id": "test.events", "name": "备份事件插件", "version": "1.0.0", "description": "声明式记录", "kind": "events", "subscriptions": ["backup.*"]}).to_string()).unwrap();
    store.conn.execute("INSERT INTO agent_sessions (title, plan_id, provider_id, created_at, updated_at) VALUES ('真实会话', 'plan-1', 'p1', ?1, ?1)", params![timestamp]).unwrap();
    let content = json!({"title": "真实产物", "overview": "产物概述", "sections": [{"heading": "正文", "content": "产物正文"}], "examples": [], "checkpoints": [{"question": "检查", "answer": "答案"}], "citations": [{"materialId": material.id, "quote": "备份原文"}]});
    let bytes = serde_json::to_vec_pretty(&content).unwrap();
    let hash = format!("{:x}", sha2::Sha256::digest(&bytes));
    let path = state
        .database_path()
        .parent()
        .unwrap()
        .join("agent-workspaces/1/scratch/real.json");
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, &bytes).unwrap();
    store.conn.execute("INSERT INTO agent_artifacts (session_id, path, plan_id, stage_id, unit_index, content_json, sha256, status, created_at, expires_at) VALUES (1, 'real.json', 'plan-1', 'stage-1', 0, ?1, ?2, 'committed', ?3, '2099-01-01T00:00:00Z')", params![content.to_string(), hash, timestamp]).unwrap();
    store.conn.execute("INSERT INTO provider_requests (request_id, provider_id, model, status, output, attempts, created_at, updated_at) VALUES ('request-2', 1, 'test-model', 'done', ?1, 1, ?2, ?2)", params![json!({"citations": [{"materialId": material.id, "quote": "备份原文"}]}).to_string(), timestamp]).unwrap();
    store.conn.execute("INSERT INTO provider_meta (key, value) VALUES ('routing_settings', ?1)", params![json!({"mode": "routing", "routes": {"plan": {"providerId": "p1", "model": "test-model"}}, "aggregateProviderIds": ["p1"], "aggregateDefault": "p1", "failover": ["p1"], "circuit": {"failures": 3, "cooldown": 30, "timeout": 30, "retries": 1}}).to_string()]).unwrap();
    crate::backend::store::Store::event(
        &store.conn,
        "agent.artifact_committed",
        json!({"sessionId": 1, "artifactId": 1, "planId": "plan-1"}),
    )
    .unwrap();
    (share.token, material.sha256)
}

fn seed_target(app: &tauri::App<tauri::test::MockRuntime>, state: &LearningState) {
    let store = state.store().unwrap();
    store.bootstrap().unwrap();
    store
        .put_draft(
            "deep-tutor-draft-new",
            &json!({"title": "目标草稿", "content": "保留目标草稿", "tag": "复盘", "savedAt": 1}),
        )
        .unwrap();
    store.replace_collection("notes", 0, &json!([{"id": 1, "title": "目标已有笔记", "excerpt": "保留", "tag": "复盘", "updated": "now", "content": "不得覆盖"}])).unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    store.conn.execute("INSERT INTO provider_profiles (name, preset, kind, protocol, base_url, default_model, models_json, enabled, created_at, updated_at) VALUES ('目标模型', 'custom', '本地模型', 'OpenAI Chat', 'http://127.0.0.1:2', 'target-model', '[\"target-model\"]', 1, ?1, ?1)", params![timestamp]).unwrap();
    store.conn.execute("INSERT INTO provider_requests (request_id, provider_id, model, status, output, attempts, created_at, updated_at) VALUES ('request-1', 1, 'target-model', 'done', '目标已有输出', 1, ?1, ?1)", params![timestamp]).unwrap();
    store.replace_collection("projects", 0, &json!([{"id": 1, "title": "目标项目", "description": "保留目标项目", "due": "2099-01-01", "progress": 10, "tag": "项目", "milestones": []}])).unwrap();
    crate::backend::materials::import_text(
        app.handle(),
        &state.material_state(),
        "目标资料.txt".into(),
        "目标库的不同原件".into(),
    )
    .unwrap();
    let input = json!({"goal": "目标方案", "level": "基础", "dailyMinutes": 30, "deadline": "2099-12-31", "materialIds": [1], "providerId": "p1", "milestone": null, "milestoneDate": null});
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES ('done', 'p1', ?1, ?2, ?3, ?3)", params![input.to_string(), plan_result(1).to_string(), timestamp]).unwrap();
    store.conn.execute("INSERT INTO agent_sessions (title, plan_id, provider_id, created_at, updated_at) VALUES ('目标会话', 'plan-1', 'p1', ?1, ?1)", params![timestamp]).unwrap();
    crate::backend::plugins::install(app.handle(), &crate::backend::plugins::PluginState::new(state.database_path()).unwrap(), json!({"id": "test.events", "name": "目标插件", "version": "1.0.0", "description": "保留目标插件", "kind": "events", "subscriptions": []}).to_string()).unwrap();
}

#[test]
fn full_backup_round_trip_remaps_ids_and_restores_real_files_without_secrets() {
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    let (token, material_hash) = seed_source(&source_app, &source);
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("full.dtbackup");
    let exported = ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    assert_eq!(exported["files"], 3);
    let archive_bytes = std::fs::read(&path).unwrap();
    assert!(!archive_bytes
        .windows(token.len())
        .any(|window| window == token.as_bytes()));
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    seed_target(&target_app, &target);
    let preview = ipc(
        &target_app,
        "preview_full_backup",
        json!({"path": path.to_string_lossy()}),
    )
    .unwrap();
    assert_eq!(preview["format"], "deep-tutor-backup");
    assert_eq!(preview["version"], 3);
    assert_eq!(preview["tableCounts"]["learning_plans"], 1);
    assert_eq!(preview["files"], 3);
    // 26#4：成功路径预演不落盘的直接断言——预演后目标笔记数必须保持
    // 导入前的 1 条（此前仅失败路径有该性质断言，成功路径靠实现自证）
    let before_import: usize = target
        .store()
        .unwrap()
        .collection("notes")
        .unwrap()
        .value
        .as_array()
        .map(Vec::len)
        .unwrap_or(0);
    assert_eq!(before_import, 1, "预演不得写入任何记录");
    let state = ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    )
    .unwrap();
    let notes = state["notes"].as_array().unwrap();
    assert_eq!(notes.len(), 3, "{state}");
    assert!(notes
        .iter()
        .any(|note| note["title"] == "目标已有笔记" && note["content"] == "不得覆盖"));
    assert!(notes
        .iter()
        .any(|note| note["id"] == 2 && note["title"] == "源笔记"));
    assert!(notes
        .iter()
        .any(|note| note["id"] == 3 && note["title"] == "第二条源笔记"));
    assert_eq!(state["tasks"].as_array().unwrap().len(), 1);
    let plans = crate::backend::learning::list(&target).unwrap();
    let imported = plans.iter().find(|plan| plan.id != "plan-1").unwrap();
    assert_eq!(imported.material_ids, [2]);
    assert_eq!(imported.provider_id, "p2");
    assert_eq!(
        imported.result.as_ref().unwrap().resources["items"][0]["materialId"],
        2
    );
    assert_eq!(imported.adopted_project_id, Some(2));
    assert_eq!(imported.artifacts["step0"]["snapshot"][0]["id"], 2);
    assert!(
        crate::backend::providers::get_chat(&target.provider_state(), "request-2")
            .unwrap()
            .output
            .contains("\"materialId\":2")
    );
    let routing = target
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT value FROM provider_meta WHERE key = 'routing_settings'",
            [],
            |row| row.get::<_, String>(0),
        )
        .unwrap();
    assert!(routing.contains("\"p2\"") && !routing.contains("\"p1\""));
    let events = target.store().unwrap().events_since(0).unwrap();
    assert!(events
        .events
        .iter()
        .any(|event| event.kind == "agent.artifact_committed"
            && event.payload["sessionId"] == 2
            && event.payload["planId"] == "plan-2"));
    assert_eq!(imported.steps[0].request_id.as_deref(), Some("imported-1"));
    assert_eq!(
        crate::backend::providers::get_chat(&target.provider_state(), "imported-1")
            .unwrap()
            .output,
        "已保存的真实模型输出"
    );
    // 34#7：agent_session_id 按表间依赖重映射——源会话 1 → 目标会话 2；
    // 恒为源库字面值 1 会把预算挂到目标库无关会话上
    let remapped_session: i64 = target
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT agent_session_id FROM provider_requests WHERE request_id = 'imported-1'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(remapped_session, 2);
    let original = target
        .database_path()
        .parent()
        .unwrap()
        .join("materials/originals")
        .join(format!("{material_hash}.txt"));
    assert_eq!(
        std::fs::read_to_string(original).unwrap(),
        "备份原文：概率与贝叶斯"
    );
    let artifact = target
        .database_path()
        .parent()
        .unwrap()
        .join("agent-workspaces/2/scratch/real.json");
    assert!(std::fs::read_to_string(artifact)
        .unwrap()
        .contains("真实产物"));
    let plugins = crate::backend::plugins::list(
        &crate::backend::plugins::PluginState::new(target.database_path()).unwrap(),
    )
    .unwrap();
    assert_eq!(plugins.len(), 2);
    assert_eq!(
        crate::backend::sharing::read_token(
            &crate::backend::sharing::SharingState::new(&target),
            &token
        )
        .unwrap()
        .plan
        .goal,
        "备份中的真实方案"
    );
    assert_eq!(state["preferences"]["theme"], "light");
    assert_eq!(
        target
            .store()
            .unwrap()
            .draft("deep-tutor-draft-1")
            .unwrap()
            .value["content"],
        "保留源草稿"
    );
    assert_eq!(
        target
            .store()
            .unwrap()
            .draft("deep-tutor-draft-new")
            .unwrap()
            .value["content"],
        "保留目标草稿"
    );
    let agent = crate::backend::agent::get(
        &crate::backend::agent::AgentState::new(
            target.database_path(),
            &target.provider_state(),
            &target,
        ),
        "agent-2",
    )
    .unwrap();
    assert_eq!(agent.artifacts[0].content["title"], "真实产物");
    assert!(ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()})
    )
    .is_ok());
    let material_count = target
        .store()
        .unwrap()
        .conn
        .query_row("SELECT COUNT(*) FROM materials", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap();
    assert_eq!(material_count, 2);
}

#[test]
fn corrupt_or_incomplete_backups_are_rejected_without_partial_writes() {
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    seed_source(&source_app, &source);
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("source.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    let mut archive = zip::ZipArchive::new(std::fs::File::open(&path).unwrap()).unwrap();
    let manifest: Value = serde_json::from_str(
        &std::io::read_to_string(archive.by_name("manifest.json").unwrap()).unwrap(),
    )
    .unwrap();
    let mut files = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).unwrap();
        if entry.name() == "manifest.json" {
            continue;
        }
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut bytes).unwrap();
        files.push((entry.name().to_string(), bytes));
    }
    let file = manifest["files"][0]["path"].as_str().unwrap().to_string();
    let corrupted = directory.path().join("corrupt.dtbackup");
    let mut writer = zip::ZipWriter::new(std::fs::File::create(&corrupted).unwrap());
    writer
        .start_file("manifest.json", zip::write::SimpleFileOptions::default())
        .unwrap();
    std::io::Write::write_all(&mut writer, manifest.to_string().as_bytes()).unwrap();
    for (name, content) in files {
        writer
            .start_file(&name, zip::write::SimpleFileOptions::default())
            .unwrap();
        let content = if name == format!("files/{file}") {
            b"corrupted original".as_slice()
        } else {
            content.as_slice()
        };
        std::io::Write::write_all(&mut writer, content).unwrap();
    }
    writer.finish().unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    assert_eq!(
        ipc(
            &target_app,
            "preview_full_backup",
            json!({"path": corrupted.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "backup_checksum_mismatch"
    );
    assert_eq!(
        ipc(
            &target_app,
            "import_full_backup",
            json!({"path": corrupted.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "backup_checksum_mismatch"
    );
    assert!(target.store().unwrap().app_state().unwrap()["notes"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn unsafe_archive_paths_are_rejected_before_any_file_write() {
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    seed_source(&source_app, &source);
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("source.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    let mut archive = zip::ZipArchive::new(std::fs::File::open(&path).unwrap()).unwrap();
    let mut manifest: Value = serde_json::from_str(
        &std::io::read_to_string(archive.by_name("manifest.json").unwrap()).unwrap(),
    )
    .unwrap();
    manifest["files"][0]["path"] = json!("../outside.txt");
    let mut entries = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).unwrap();
        if entry.name() == "manifest.json" {
            continue;
        }
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut bytes).unwrap();
        entries.push((entry.name().to_string(), bytes));
    }
    let unsafe_path = directory.path().join("unsafe.dtbackup");
    let mut writer = zip::ZipWriter::new(std::fs::File::create(&unsafe_path).unwrap());
    writer
        .start_file("manifest.json", zip::write::SimpleFileOptions::default())
        .unwrap();
    std::io::Write::write_all(&mut writer, manifest.to_string().as_bytes()).unwrap();
    for (name, bytes) in entries {
        writer
            .start_file(name, zip::write::SimpleFileOptions::default())
            .unwrap();
        std::io::Write::write_all(&mut writer, &bytes).unwrap();
    }
    writer.finish().unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    assert_eq!(
        ipc(
            &target_app,
            "preview_full_backup",
            json!({"path": unsafe_path.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "invalid_backup_path"
    );
    assert_eq!(
        ipc(
            &target_app,
            "import_full_backup",
            json!({"path": unsafe_path.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "invalid_backup_path"
    );
    assert!(target.store().unwrap().app_state().unwrap()["notes"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn broken_provider_reference_rolls_back_database_and_staged_originals() {
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    seed_source(&source_app, &source);
    source
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE learning_plans SET provider_id = 'p999' WHERE id = 1",
            [],
        )
        .unwrap();
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("broken.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    assert_eq!(
        ipc(
            &target_app,
            "import_full_backup",
            json!({"path": path.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "invalid_backup_reference"
    );
    assert!(target.store().unwrap().app_state().unwrap()["notes"]
        .as_array()
        .unwrap()
        .is_empty());
    let originals = target
        .database_path()
        .parent()
        .unwrap()
        .join("materials/originals");
    assert!(!originals.exists() || std::fs::read_dir(originals).unwrap().count() == 0);
}

#[test]
fn append_import_rewrites_pk_fk_child_tables_with_parent() {
    // 23#4：material_text 主键=外键——追加导入时主键必须随父资料映射重写，
    // 不能按子表自身 MAX+1 重映射（会撞外键或静默挂到错误资料）
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    let (_, material_hash) = seed_source(&source_app, &source);
    // 直接为源资料落一行解析文本（import_text 只 staged，正常路径由解析写入）
    {
        let store = source.store().unwrap();
        let material_id: i64 = store
            .conn
            .query_row("SELECT id FROM materials ORDER BY id LIMIT 1", [], |row| {
                row.get(0)
            })
            .unwrap();
        store
            .conn
            .execute(
                "INSERT INTO material_text (material_id, content, mode, updated_at) VALUES (?1, '源解析文本', 'standard', '2026-01-01T00:00:00Z')",
                params![material_id],
            )
            .unwrap();
    }
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("append.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    // 目标库已有 id=1 的不同资料：源资料 id 必须重映射，material_text 随行
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    seed_target(&target_app, &target);
    let state = ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    )
    .unwrap();
    let _ = &state;
    let store = target.store().unwrap();
    let imported_material: i64 = store
        .conn
        .query_row(
            "SELECT id FROM materials WHERE sha256 = ?1",
            params![material_hash],
            |row| row.get(0),
        )
        .unwrap();
    assert_ne!(imported_material, 1, "同哈希资料未复用时源资料应获新 id");
    let text: String = store
        .conn
        .query_row(
            "SELECT content FROM material_text WHERE material_id = ?1",
            params![imported_material],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(text, "源解析文本", "material_text 主键应随父资料映射重写");
    // 孤儿检查：material_text 的每一行都必须能 JOIN 到 materials
    let orphans: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM material_text t LEFT JOIN materials m ON m.id = t.material_id WHERE m.id IS NULL",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(orphans, 0, "material_text 不得出现孤儿行");
    let total: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM materials", [], |row| row.get(0))
        .unwrap();
    assert_eq!(total, 2, "追加导入后目标库应有 2 条资料（原有 1 + 追加 1）");
}

#[test]
fn repeat_import_with_quota_rows_skips_by_composite_key() {
    // 23#5：security_quotas 复合主键 (user_id, resource)——同一份备份重复
    // 导入必须按原键跳过，不再撞 UNIQUE 整单回滚
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    seed_source(&source_app, &source);
    // 模拟登录态快照：源库带 local_users + security_quotas 行（生产导出时
    // 由 archive 注入当前用户配额）
    {
        let store = source.store().unwrap();
        let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
        store
            .conn
            .execute(
                "INSERT INTO local_users (id, display_name, role, active, created_at, updated_at) VALUES (1, '源用户', '管理员', 1, ?1, ?1)",
                params![timestamp],
            )
            .unwrap();
        for resource in ["notes", "materials"] {
            store
                .conn
                .execute(
                    "INSERT INTO security_quotas (user_id, resource, item_limit) VALUES (1, ?1, 500)",
                    params![resource],
                )
                .unwrap();
        }
    }
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("quota.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    seed_target(&target_app, &target);
    let first = ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    );
    assert!(first.is_ok(), "首次导入应成功：{first:?}");
    let second = ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    );
    assert!(second.is_ok(), "重复导入必须按原键跳过配额行：{second:?}");
    let store = target.store().unwrap();
    let quotas: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM security_quotas", [], |row| row.get(0))
        .unwrap();
    assert_eq!(quotas, 2, "重复导入不得新增配额行");
    // 31#43：provider_requests 重复导入幂等——同备份二次导入按内容签名找回
    // 首次导入的 imported-N 行并跳过，不再复制出重复请求行
    let requests_after_first: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM provider_requests", [], |row| {
            row.get(0)
        })
        .unwrap();
    let third = ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    );
    assert!(third.is_ok(), "三次导入仍应成功：{third:?}");
    let requests_after_third: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM provider_requests", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(
        requests_after_third, requests_after_first,
        "重复导入不得复制请求行"
    );
}

// 64#6：导入后 events id 续接与 payload 内嵌 eventId 重映射——导入事件
// id 全部高于导入前最大值（游标不重叠），导入后新事件 AUTOINCREMENT 接续
// 不回退，payload 里引用的源事件 id 改写为目标 id（插件去重/前端游标
// 续接的前提）
#[test]
fn imported_events_continue_ids_and_remap_payload_references() {
    let _lock = crate::backend::sync::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let source = LearningState::for_tests().unwrap();
    let source_app = setup(&source);
    seed_source(&source_app, &source);
    // 一对带引用的事件：referencing.payload.eventId 指向 referenced
    let source_store = source.store().unwrap();
    let referenced = crate::backend::store::Store::event(
        &source_store.conn,
        "plugin.ping",
        json!({"marker": "referenced"}),
    )
    .unwrap();
    let referencing = crate::backend::store::Store::event(
        &source_store.conn,
        "plugin.ping",
        json!({"marker": "referencing", "eventId": referenced.id}),
    )
    .unwrap();
    assert!(referencing.id > referenced.id);
    drop(source_store);
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.dtbackup");
    ipc(
        &source_app,
        "export_full_backup",
        json!({"destination": path.to_string_lossy()}),
    )
    .unwrap();
    let target = LearningState::for_tests().unwrap();
    let target_app = setup(&target);
    seed_target(&target_app, &target);
    let pre_max: i64 = target
        .store()
        .unwrap()
        .conn
        .query_row("SELECT COALESCE(MAX(id), 0) FROM events", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert!(pre_max > 0, "目标库导入前须已有事件");
    ipc(
        &target_app,
        "import_full_backup",
        json!({"path": path.to_string_lossy()}),
    )
    .unwrap();
    let imported: i64 = target
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE id > ?1",
            params![pre_max],
            |row| row.get(0),
        )
        .unwrap();
    assert!(imported > 0, "导入必须带入源库事件");
    // payload 内嵌 eventId 已改写为目标库中被引用事件的新 id
    let remapped: (i64, Value) = target
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT id, payload FROM events WHERE kind = 'plugin.ping' AND json_extract(payload, '$.marker') = 'referencing'",
            [],
            |row| {
                Ok((
                    row.get(0)?,
                    serde_json::from_str::<Value>(&row.get::<_, String>(1)?)
                        .expect("事件载荷必须是 JSON"),
                ))
            },
        )
        .unwrap();
    let referenced_target: i64 = target
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT id FROM events WHERE kind = 'plugin.ping' AND json_extract(payload, '$.marker') = 'referenced'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(remapped.1["eventId"].as_i64(), Some(referenced_target));
    assert_ne!(
        remapped.1["eventId"].as_i64(),
        Some(referenced.id),
        "不得残留源库字面 id"
    );
    // 导入后新事件 AUTOINCREMENT 接续（前端 lastEventId 游标不回退的前提）
    let max_after: i64 = target
        .store()
        .unwrap()
        .conn
        .query_row("SELECT COALESCE(MAX(id), 0) FROM events", [], |row| {
            row.get(0)
        })
        .unwrap();
    let target_store = target.store().unwrap();
    let fresh = crate::backend::store::Store::event(
        &target_store.conn,
        "plugin.ping",
        json!({"marker": "post-import"}),
    )
    .unwrap();
    assert!(
        fresh.id > max_after,
        "AUTOINCREMENT 必须接续导入后的最大 id"
    );
}
