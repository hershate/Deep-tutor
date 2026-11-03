use super::*;
use crate::backend::BackendState;
use std::sync::Mutex;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, PluginState) {
    let state = PluginState::for_tests().unwrap();
    let path = state.database_path.clone();
    let materials = crate::backend::materials::MaterialState::new(&path).unwrap();
    let providers = crate::backend::providers::ProviderState::for_tests_at(&path);
    let learning = crate::backend::learning::LearningState::new(&path, &providers, &materials);
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: materials.shared(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                &path, &providers, &materials,
            ),
            community: crate::backend::community::CommunityState::new(&path),
            plugins: PluginState::new(&path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::install_plugin,
            super::commands::set_plugin_enabled,
            super::commands::reload_plugin,
            super::commands::uninstall_plugin,
            super::commands::emit_plugin_ping,
            super::commands::get_plugin_overview,
            super::commands::list_plugin_events,
            super::commands::get_developer_asset,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
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

fn manifest(version: &str, subscriptions: &[&str]) -> String {
    json!({"id": "practice-events", "name": "练习事件记录", "version": version, "description": "仅记录真实原生事件", "kind": "events", "subscriptions": subscriptions}).to_string()
}

#[test]
fn manifest_lifecycle_event_deduplication_and_reload_preservation_are_real() {
    let (app, state) = setup();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    assert_eq!(install(app.handle(), &state, json!({"id": "../unsafe", "name": "不安全", "version": "1.0.0", "description": "", "kind": "events", "subscriptions": []}).to_string()).unwrap_err().code, "invalid_plugin_id");
    let installed = ipc(
        &window,
        "install_plugin",
        json!({"content": manifest("1.0.0", &["plugin.*", "practice.*"])}),
    )
    .unwrap();
    assert_eq!(installed["enabled"], false);
    assert_eq!(installed["generation"], 0);
    assert!(manifest_path(&state, "practice-events").unwrap().is_file());
    assert_eq!(
        install(app.handle(), &state, manifest("1.0.0", &[]))
            .unwrap_err()
            .code,
        "plugin_already_installed"
    );
    let enabled = ipc(
        &window,
        "set_plugin_enabled",
        json!({"id": "practice-events", "enabled": true}),
    )
    .unwrap();
    assert_eq!(enabled["enabled"], true);
    let event_id = ipc(&window, "emit_plugin_ping", json!({}))
        .unwrap()
        .as_i64()
        .unwrap();
    dispatch(&state.store().unwrap().conn, event_id, "plugin.ping").unwrap();
    let after_ping = plugin_row(&state.store().unwrap().conn, "practice-events")
        .unwrap()
        .event_count;
    assert_eq!(after_ping, 2);
    let first = plugin_row(&state.store().unwrap().conn, "practice-events").unwrap();
    assert_eq!(first.event_count, after_ping);
    assert_eq!(events(&state, None).unwrap()[0].kind, "plugin.ping");
    let store = state.store().unwrap();
    let practice = Store::event(
        &store.conn,
        "practice.answer_graded",
        json!({"answerId": 1}),
    )
    .unwrap();
    assert!(practice.id > event_id);
    drop(store);
    assert_eq!(
        plugin_row(&state.store().unwrap().conn, "practice-events")
            .unwrap()
            .event_count,
        3
    );

    fs::write(
        manifest_path(&state, "practice-events").unwrap(),
        manifest("2.0.0", &["practice.*"]),
    )
    .unwrap();
    let reloaded = ipc(&window, "reload_plugin", json!({"id": "practice-events"})).unwrap();
    assert_eq!(reloaded["generation"], 1);
    assert_eq!(reloaded["version"], "2.0.0");
    assert_eq!(reloaded["enabled"], true);
    assert_eq!(reloaded["subscriptions"], json!(["practice.*"]));
    fs::write(manifest_path(&state, "practice-events").unwrap(), "{}").unwrap();
    assert_eq!(
        reload(app.handle(), &state, "practice-events".into())
            .unwrap_err()
            .code,
        "invalid_plugin_manifest"
    );
    let preserved = plugin_row(&state.store().unwrap().conn, "practice-events").unwrap();
    assert_eq!(preserved.generation, 1);
    assert_eq!(preserved.version, "2.0.0");
    assert_eq!(preserved.subscriptions, vec!["practice.*"]);
    let failed_reloads: i64 = state
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM plugin_reloads WHERE status = 'failed'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(failed_reloads, 1);
    set_enabled(app.handle(), &state, "practice-events".into(), false).unwrap();
    ping(app.handle(), &state).unwrap();
    assert_eq!(
        plugin_row(&state.store().unwrap().conn, "practice-events")
            .unwrap()
            .event_count,
        3
    );
    ipc(
        &window,
        "uninstall_plugin",
        json!({"id": "practice-events"}),
    )
    .unwrap();
    assert!(list(&state).unwrap().is_empty());
    assert!(!manifest_path(&state, "practice-events").unwrap().exists());
}

#[test]
fn assets_are_real_files_and_reset_removes_installed_manifest_data() {
    let (app, state) = setup();
    let overview = overview(&state).unwrap();
    assert_eq!(overview.assets.len(), 9);
    assert_eq!(
        overview
            .assets
            .iter()
            .filter(|asset| asset.kind == "legacy-skill")
            .count(),
        8
    );
    let intent = asset(&state, "deep-tutor-intent").unwrap();
    assert!(intent.content.contains("Deep-tutor Intent"));
    assert_eq!(intent.sha256.len(), 64);
    assert_eq!(intent.file_name, "deep-tutor-intent.md");
    assert_eq!(asset(&state, "missing").unwrap_err().code, "not_found");
    install(app.handle(), &state, manifest("1.0.0", &["plugin.*"])).unwrap();
    state.store().unwrap().reset().unwrap();
    reset_files(&state).unwrap();
    assert!(list(&state).unwrap().is_empty());
    assert!(!state.root().exists());
    assert_eq!(super::overview(&state).unwrap().enabled_count, 0);
}
