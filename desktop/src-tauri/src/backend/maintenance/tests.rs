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
    // 31#3：宽限覆盖置零——刚写入的 backup-work/anki 导出立即视为过期孤儿
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = Some(std::time::Duration::ZERO);
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
    // 14#19：安装中断残留——无清单空目录
    std::fs::create_dir_all(root.join("plugins/broken.events")).unwrap();
    write(root, "backup-work/123-4/state.db", "snapshot");
    write(root, "exports/anki-123-4.db", "stale");
    store.conn.execute("INSERT INTO materials (title, kind, size, sha256, status, progress, parse_mode, text_length, source_meta, created_at, updated_at) VALUES ('缺失', 'txt', 1, ?1, 'metadata_only', 0, '', 0, '', ?2, ?2)", params!["a".repeat(64), timestamp]).unwrap();
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert_eq!(report["database"]["integrity"], "ok");
    assert_eq!(report["database"]["journalMode"], "wal");
    assert_eq!(report["database"]["schemaVersion"], 1);
    assert_eq!(report["orphans"]["originals"].as_array().unwrap().len(), 1);
    assert_eq!(report["orphans"]["scratch"].as_array().unwrap().len(), 1);
    assert_eq!(report["orphans"]["plugins"].as_array().unwrap().len(), 2);
    assert_eq!(report["orphans"]["tempdirs"].as_array().unwrap().len(), 2);
    assert_eq!(
        report["orphans"]["missingOriginals"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    // originals 1 + scratch 1 + plugins 2（stale 与 broken）
    assert_eq!(cleanup["deletedFiles"], 4);
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
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = None;
}

// 31#3：宽限方向回归——进行中的 backup-work/anki 导出/清单 tmp 刚写入时
// 处于宽限期内，不报告也不清理（原先方向写反，刚写入即被当孤儿删除）
#[test]
fn grace_window_protects_in_flight_files() {
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    write(root, "plugins/.manifest-7.tmp", "partial");
    write(root, "backup-work/123-4/state.db", "snapshot");
    write(root, "exports/anki-123-4.db", "in-flight");
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert!(report["orphans"]["tempdirs"].as_array().unwrap().is_empty());
    assert!(report["orphans"]["plugins"]
        .as_array()
        .unwrap()
        .iter()
        .all(|item| !item["path"].as_str().unwrap().contains(".manifest-")));
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedTempItems"], 0);
    assert!(root.join("plugins/.manifest-7.tmp").is_file());
    assert!(root.join("backup-work/123-4/state.db").is_file());
    assert!(root.join("exports/anki-123-4.db").is_file());
}

// 65#1：过期 agent 产物在扫描中按孤儿呈现，清理命令物理回收行与文件；
// 未过期产物的行与文件不受影响
#[test]
fn expired_agent_artifacts_are_purged_on_cleanup() {
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = Some(std::time::Duration::ZERO);
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    let store = learning.store().unwrap();
    store.conn.execute("INSERT INTO agent_sessions (title, plan_id, provider_id, created_at, updated_at) VALUES ('会话', 'plan-1', 'p1', ?1, ?1)", params![timestamp]).unwrap();
    store
        .conn
        .execute(
            "INSERT INTO agent_artifacts (session_id, path, plan_id, stage_id, unit_index, content_json, sha256, status, created_at, expires_at) VALUES (1, 'expired.json', 'plan-1', 'stage-1', 0, '{}', 'old', 'committed', ?1, '2000-01-01T00:00:00Z')",
            params![timestamp],
        )
        .unwrap();
    store
        .conn
        .execute(
            "INSERT INTO agent_artifacts (session_id, path, plan_id, stage_id, unit_index, content_json, sha256, status, created_at, expires_at) VALUES (1, 'alive.json', 'plan-1', 'stage-1', 0, '{}', 'new', 'committed', ?1, '2099-01-01T00:00:00Z')",
            params![timestamp],
        )
        .unwrap();
    write(root, "agent-workspaces/1/scratch/expired.json", "expired");
    write(root, "agent-workspaces/1/scratch/alive.json", "alive");
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    let scratch = report["orphans"]["scratch"].as_array().unwrap();
    assert_eq!(scratch.len(), 1);
    assert!(scratch[0]["path"]
        .as_str()
        .unwrap()
        .contains("expired.json"));
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedFiles"], 1);
    assert_eq!(cleanup["failures"], json!([]));
    let remaining: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM agent_artifacts WHERE path = 'expired.json'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(remaining, 0, "过期行必须连同文件一起物理删除");
    assert!(!root
        .join("agent-workspaces/1/scratch/expired.json")
        .exists());
    assert!(root.join("agent-workspaces/1/scratch/alive.json").is_file());
    let alive: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM agent_artifacts WHERE path = 'alive.json'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(alive, 1);
    let after = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert!(after["orphans"]["scratch"].as_array().unwrap().is_empty());
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = None;
}

// 65#3：sync 目录存量超出保留数的 json 与宽限期外的下载临时件按孤儿
// 扫描清理；非 sync- 前缀或段非数字的文件不动
#[test]
fn sync_directory_leftovers_are_scanned_and_cleaned() {
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    for index in 0..12 {
        write(root, &format!("sync/sync-100-{index}.json"), "{}");
    }
    write(root, "sync/sync-100-3.download-7", "partial");
    write(root, "sync/sync-100-4.previous-2", "backup");
    write(root, "sync/notes.txt", "foreign");
    write(root, "sync/sync-weird.json", "foreign");
    // 默认宽限：临时件刚写入受保护，仅超量 json（保留 10 份）报告
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert_eq!(report["orphans"]["sync"].as_array().unwrap().len(), 2);
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedFiles"], 2);
    assert!(root.join("sync/sync-100-3.download-7").is_file());
    // 宽限归零：崩溃遗留的临时件进入清理
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = Some(std::time::Duration::ZERO);
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert_eq!(report["orphans"]["sync"].as_array().unwrap().len(), 2);
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedFiles"], 2);
    assert!(!root.join("sync/sync-100-3.download-7").exists());
    assert!(!root.join("sync/sync-100-4.previous-2").exists());
    assert!(root.join("sync/notes.txt").is_file(), "非本应用产物不动");
    assert!(
        root.join("sync/sync-weird.json").is_file(),
        "段非数字不视为本应用产物"
    );
    let jsons = std::fs::read_dir(root.join("sync"))
        .unwrap()
        .filter_map(std::result::Result::ok)
        .filter(|entry| entry.file_name().to_string_lossy().ends_with(".json"))
        .count();
    assert_eq!(jsons, 11, "保留最近 10 份 + 1 份外来 json");
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = None;
}

// 65#4/65#7：materials.reset-{n} 残留目录与迁移 staging 库纳入 tempdirs
// 扫描；宽限期内（reset 可能正在进行）不报告
#[test]
fn reset_residue_and_migration_tmp_are_reported() {
    let (app, learning) = setup();
    let root = learning.database_path().parent().unwrap();
    write(root, "materials.reset-42/keep.txt", "residue");
    let staged_name = format!(
        "{}.migration-tmp",
        learning
            .database_path()
            .file_name()
            .unwrap()
            .to_string_lossy()
    );
    write(root, &staged_name, "staged db");
    std::fs::create_dir_all(root.join("materials.reset-abc")).unwrap();
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    assert!(
        report["orphans"]["tempdirs"].as_array().unwrap().is_empty(),
        "宽限期内可能正在进行 reset/迁移，不报告"
    );
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = Some(std::time::Duration::ZERO);
    let report = ipc(&app, "get_maintenance_report", json!({})).unwrap();
    let tempdirs = report["orphans"]["tempdirs"].as_array().unwrap();
    assert_eq!(tempdirs.len(), 2);
    let paths = tempdirs
        .iter()
        .map(|item| item["path"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(paths.contains(&"materials.reset-42"));
    assert!(paths.contains(&staged_name.as_str()));
    let cleanup = ipc(&app, "cleanup_orphan_files", json!({})).unwrap();
    assert_eq!(cleanup["deletedTempItems"], 2);
    assert!(!root.join("materials.reset-42").exists());
    assert!(!root.join(&staged_name).exists());
    assert!(
        root.join("materials.reset-abc").exists(),
        "非数字后缀不视为 reset 中间态"
    );
    *GRACE_OVERRIDE
        .lock()
        .unwrap_or_else(|poison| poison.into_inner()) = None;
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
