use super::*;
use crate::backend::BackendState;
use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

fn setup() -> (tauri::App<tauri::test::MockRuntime>, LearningState) {
    let learning = LearningState::for_tests().unwrap();
    let path = learning.database_path();
    let providers = learning.provider_state();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                path,
                &providers,
                &learning.material_state(),
            ),
            community: crate::backend::community::CommunityState::new(path),
            plugins: crate::backend::plugins::PluginState::new(path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_maintenance_report,
            commands::cleanup_orphan_files,
            commands::vacuum_database,
            crate::backend::commands::events_since
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    (app, learning)
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

fn write(root: &Path, relative: &str, content: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

#[test]
fn cleanup_preserves_valid_data_and_vacuum_reports_actual_results() {
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    let material = crate::backend::materials::import_text(
        app.handle(),
        &learning.material_state(),
        "保留.txt".into(),
        "有效原件".into(),
    )
    .unwrap()
    .material;
    let original = root
        .join("materials/originals")
        .join(format!("{}.txt", material.sha256));
    assert!(original.is_file());
    crate::backend::plugins::install(app.handle(), &crate::backend::plugins::PluginState::new(learning.database_path()).unwrap(), json!({"id": "valid.events", "name": "有效插件", "version": "1.0.0", "description": "真实清单", "kind": "events", "subscriptions": []}).to_string()).unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    let store = learning.store().unwrap();
    store.conn.execute("INSERT INTO agent_sessions (title, plan_id, provider_id, created_at, updated_at) VALUES ('会话', 'plan-1', 'p1', ?1, ?1)", params![timestamp]).unwrap();
    store.conn.execute("INSERT INTO agent_artifacts (session_id, path, plan_id, stage_id, unit_index, content_json, sha256, status, created_at, expires_at) VALUES (1, 'real.json', 'plan-1', 'stage-1', 0, '{}', 'real', 'committed', ?1, '2099-01-01T00:00:00Z')", params![timestamp]).unwrap();
    write(root, "agent-workspaces/1/scratch/real.json", "{}");
    write(root, "materials/originals/orphan.txt", "orphan");
    write(root, "agent-workspaces/999/scratch/orphan.json", "{}");
    write(root, "plugins/stale.events/manifest.json", &json!({"id": "stale.events", "name": "过期插件", "version": "1.0.0", "description": "失效记录", "kind": "events", "subscriptions": []}).to_string());
    // 14#19：安装中断残留——无清单空目录与遗留在 plugins 根的 .manifest-N.tmp
    std::fs::create_dir_all(root.join("plugins/broken.events")).unwrap();
    write(root, "plugins/.manifest-7.tmp", "partial");
    write(root, "backup-work/123-4/state.db", "snapshot");
    write(root, "exports/anki-123-4.db", "stale");
    store.conn.execute("INSERT INTO materials (title, kind, size, sha256, status, progress, parse_mode, text_length, source_meta, created_at, updated_at) VALUES ('缺失', 'txt', 1, ?1, 'metadata_only', 0, '', 0, '', ?2, ?2)", params!["a".repeat(64), timestamp]).unwrap();
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert_eq!(report["database"]["integrity"], "ok");
    assert_eq!(report["database"]["journalMode"], "wal");
    assert_eq!(report["database"]["schemaVersion"], 1);
    assert_eq!(report["orphans"]["originals"].as_array().unwrap().len(), 1);
    assert_eq!(report["orphans"]["scratch"].as_array().unwrap().len(), 1);
    assert_eq!(report["orphans"]["plugins"].as_array().unwrap().len(), 3);
    assert_eq!(report["orphans"]["tempdirs"].as_array().unwrap().len(), 2);
    assert_eq!(
        report["orphans"]["missingOriginals"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedFiles"], 5);
    assert_eq!(cleanup["deletedTempItems"], 2);
    assert_eq!(cleanup["failures"], json!([]));
    assert!(cleanup["freedBytes"].as_u64().unwrap() > 0);
    assert!(original.is_file());
    assert!(root.join("agent-workspaces/1/scratch/real.json").is_file());
    assert!(root.join("plugins/valid.events/manifest.json").is_file());
    assert!(!root.join("materials/originals/orphan.txt").exists());
    assert!(!root
        .join("agent-workspaces/999/scratch/orphan.json")
        .exists());
    assert!(!root.join("plugins/stale.events/manifest.json").exists());
    assert!(!root.join("plugins/broken.events").exists());
    assert!(!root.join("backup-work/123-4").exists());
    assert!(!root.join("exports/anki-123-4.db").exists());
    let after = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert!(after["orphans"]["originals"].as_array().unwrap().is_empty());
    assert!(after["orphans"]["scratch"].as_array().unwrap().is_empty());
    assert!(after["orphans"]["plugins"].as_array().unwrap().is_empty());
    assert!(after["orphans"]["tempdirs"].as_array().unwrap().is_empty());
    assert_eq!(
        after["orphans"]["missingOriginals"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let vacuum = ipc(&app, "vacuum_database", json!({})).unwrap();
    assert!(vacuum["beforeBytes"].as_u64().unwrap() > 0);
    assert!(vacuum["afterBytes"].as_u64().unwrap() > 0);
    assert_eq!(vacuum["walLog"], 0);
    assert_eq!(vacuum["walCheckpointed"], 0);
    assert_eq!(vacuum["walBusy"], 0);
    assert_eq!(vacuum["journalMode"], "wal");
    let events = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
    let kinds = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| event["kind"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(
        kinds.contains(&"maintenance.scan_completed")
            && kinds.contains(&"maintenance.cleanup_completed")
            && kinds.contains(&"maintenance.vacuum_completed")
    );
}

#[test]
fn foreign_key_violations_and_future_schema_are_rejected_without_deleting_files() {
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    write(root, "materials/originals/orphan.txt", "must remain");
    let store = learning.store().unwrap();
    store.conn.execute("PRAGMA foreign_keys = OFF", []).unwrap();
    store
        .conn
        .execute(
            "INSERT INTO learning_plan_materials (plan_id, material_id) VALUES (999, 999)",
            [],
        )
        .unwrap();
    store.conn.execute("PRAGMA foreign_keys = ON", []).unwrap();
    assert_eq!(
        ipc(&app, "get_maintenance_report", json!({})).unwrap_err()["code"],
        "foreign_key_violation"
    );
    assert_eq!(
        ipc(&app, "cleanup_orphan_files", json!({})).unwrap_err()["code"],
        "foreign_key_violation"
    );
    assert_eq!(
        ipc(&app, "vacuum_database", json!({})).unwrap_err()["code"],
        "foreign_key_violation"
    );
    assert!(root.join("materials/originals/orphan.txt").is_file());
    store
        .conn
        .execute("CREATE TABLE future_canary (id INTEGER)", [])
        .unwrap();
    store.conn.execute("PRAGMA user_version = 99", []).unwrap();
    let schema = |connection: &rusqlite::Connection| {
        let mut statement = connection
            .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            .unwrap();
        statement
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<std::result::Result<Vec<_>, _>>()
            .unwrap()
    };
    let before = schema(&store.conn);
    let error = learning.store().err().expect("未来版本必须拒绝打开");
    assert_eq!(error.code, "unsupported_schema");
    assert_eq!(
        schema(&store.conn),
        before,
        "拒绝未来版本之前不得执行任何建表迁移"
    );
}
