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
    assert_eq!(events(&state, None, None).unwrap()[0].kind, "plugin.ping");
    // 14#18：before 游标分页——排除最新一条后只剩更早投递
    let earlier = events(&state, None, Some(event_id)).unwrap();
    assert!(!earlier.is_empty());
    assert!(earlier.iter().all(|item| item.event_id < event_id));
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

// 70#5：1.1.1 前存量清单声明 app.* 订阅——重载按过期订阅滤除而非整单拒绝
#[test]
fn legacy_app_namespace_subscriptions_are_dropped_on_reload() {
    let (app, state) = setup();
    install(app.handle(), &state, manifest("1.0.0", &["practice.*"])).unwrap();
    // 模拟存量磁盘清单：含 1.1.1 前白名单内的 app.* 订阅
    fs::write(
        manifest_path(&state, "practice-events").unwrap(),
        manifest("1.1.0", &["app.*", "practice.*", "app.*"]),
    )
    .unwrap();
    let reloaded = reload(app.handle(), &state, "practice-events".into()).unwrap();
    assert_eq!(reloaded.version, "1.1.0");
    assert_eq!(reloaded.subscriptions, vec!["practice.*"]);
    assert_eq!(reloaded.generation, 1);
    // 全部订阅都是过期命名空间时：滤除后空订阅仍可重载
    fs::write(
        manifest_path(&state, "practice-events").unwrap(),
        manifest("1.1.1", &["app.*"]),
    )
    .unwrap();
    let reloaded = reload(app.handle(), &state, "practice-events".into()).unwrap();
    assert_eq!(reloaded.version, "1.1.1");
    assert!(reloaded.subscriptions.is_empty());
    // 非法的未知命名空间（非 app）不享受滤除——仍按原错误拒绝
    fs::write(
        manifest_path(&state, "practice-events").unwrap(),
        manifest("1.2.0", &["secret.*"]),
    )
    .unwrap();
    assert_eq!(
        reload(app.handle(), &state, "practice-events".into())
            .unwrap_err()
            .code,
        "invalid_plugin_subscriptions"
    );
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

// 117#3：白名单双向一致性——后端实发事件命名空间（Store::event 调用点的
// 字符串字面量）必须 ⊆ EVENT_NAMESPACES ∪ {state}，反向白名单每项也须有
// 实发事件（61#1「只含真实 emit」）；state.changed 是 replace_collection 的
// 高频内部 UI 状态同步（载荷仅含 namespace/revision），不作为插件面开放。
// 前端 Integrations.tsx 的 namespaces 数组须与白名单同步。新增命名空间时同步
// 三处：EVENT_NAMESPACES、Integrations.tsx、本测试自动跟随
#[test]
fn event_namespaces_cover_whitelist() {
    let mut emitted: std::collections::BTreeSet<String> = Default::default();
    let mut stack = vec![std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/backend")];
    while let Some(directory) = stack.pop() {
        for entry in std::fs::read_dir(&directory).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|ext| ext == "rs")
                && !path
                    .file_name()
                    .is_some_and(|name| name.to_string_lossy().contains("tests"))
            {
                let content = std::fs::read_to_string(&path).unwrap();
                for offset in content.match_indices("Store::event(").map(|(at, _)| at) {
                    let after = &content.as_bytes()[offset + "Store::event(".len()..];
                    // 字节级窗口（到 json! 或 240B 截止）——注释里的中文可能
                    // 落在窗口内，字符串切片会切断 UTF-8 边界
                    let window = &after[..after
                        .windows(5)
                        .position(|probe| probe == b"json!")
                        .unwrap_or(after.len())
                        .min(240)];
                    let mut cursor = 0;
                    while let Some(rel) = window[cursor..].iter().position(|&byte| byte == b'"') {
                        let start = cursor + rel + 1;
                        let Some(rel_end) = window[start..].iter().position(|&byte| byte == b'"')
                        else {
                            break;
                        };
                        let end = start + rel_end;
                        let candidate = &window[start..end];
                        if candidate.is_ascii() && !candidate.is_empty() {
                            let name = std::str::from_utf8(candidate).unwrap();
                            let mut parts = name.split('.');
                            let looks_like_kind = name.contains('.')
                                && parts.next().is_some_and(|head| {
                                    !head.is_empty()
                                        && head.starts_with(|c: char| c.is_ascii_lowercase())
                                        && head.chars().all(|c| {
                                            c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'
                                        })
                                })
                                && parts.all(|part| {
                                    !part.is_empty()
                                        && part.chars().all(|c| {
                                            c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'
                                        })
                                });
                            if looks_like_kind {
                                emitted.insert(name.to_string());
                            }
                        }
                        cursor = end + 1;
                    }
                }
            }
        }
    }
    // 提取器自检：已知事件缺失说明扫描失效（如调用形态变化），测试不得静默通过
    assert!(
        emitted.contains("plan.adopted"),
        "源码扫描未提取到已知事件，提取器失效：{emitted:?}"
    );
    let prefixes: std::collections::BTreeSet<&str> = emitted
        .iter()
        .map(|name| name.split('.').next().unwrap())
        .collect();
    for prefix in &prefixes {
        assert!(
            *prefix == "state" || EVENT_NAMESPACES.contains(prefix),
            "实发事件命名空间 {prefix} 不在订阅白名单——同步 EVENT_NAMESPACES 与 Integrations.tsx（state.changed 为内部状态同步例外）"
        );
    }
    for entry in EVENT_NAMESPACES {
        assert!(
            prefixes.contains(entry),
            "白名单项 {entry} 无任何实发事件（61#1 只含真实 emit）"
        );
    }
    // 前端同步：Integrations.tsx 的 namespaces 数组与白名单一致
    let frontend = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/pages/Integrations.tsx"),
    )
    .unwrap();
    let array = frontend
        .split("const namespaces = [")
        .nth(1)
        .and_then(|rest| rest.split(']').next())
        .expect("Integrations.tsx namespaces 数组缺失");
    for entry in EVENT_NAMESPACES {
        assert!(
            array.contains(&format!("'{entry}'")),
            "Integrations.tsx namespaces 缺少 {entry}——与后端白名单同步"
        );
    }
}

#[test]
fn wildcard_subscriptions_never_receive_internal_events() {
    // P-024：通配订阅 `*` 只覆盖 EVENT_NAMESPACES 的 22 个命名空间——
    // state.changed / draft.saved / application.reset 属内部事件，此前被
    // 写进 plugin_deliveries：插件「真实投递」计数随每次打字保存递增，
    // 「真实事件记录」首屏被内部事件占据，投递表随高频事件无限增长
    let (app, state) = setup();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    ipc(
        &window,
        "install_plugin",
        json!({"content": manifest("1.0.0", &["*"])}),
    )
    .unwrap();
    ipc(
        &window,
        "set_plugin_enabled",
        json!({"id": "practice-events", "enabled": true}),
    )
    .unwrap();
    let store = state.store().unwrap();
    let baseline = plugin_row(&store.conn, "practice-events")
        .unwrap()
        .event_count;
    for kind in [
        "state.changed",
        "draft.saved",
        "draft.deleted",
        "application.reset",
    ] {
        let event = Store::event(&store.conn, kind, json!({"namespace": "notes"})).unwrap();
        dispatch(&store.conn, event.id, kind).unwrap();
    }
    assert_eq!(
        plugin_row(&store.conn, "practice-events")
            .unwrap()
            .event_count,
        baseline,
        "内部事件不得计入插件投递"
    );
    let real = Store::event(
        &store.conn,
        "practice.answer_graded",
        json!({"answerId": 1}),
    )
    .unwrap();
    dispatch(&store.conn, real.id, "practice.answer_graded").unwrap();
    assert_eq!(
        plugin_row(&store.conn, "practice-events")
            .unwrap()
            .event_count,
        baseline + 1,
        "白名单命名空间的事件仍须照常投递"
    );
    // 判定口径直接固化在匹配函数上
    for kind in [
        "state.changed",
        "draft.saved",
        "draft.deleted",
        "application.reset",
    ] {
        assert!(!matches("*", kind), "{kind} 不得被通配订阅命中");
    }
    assert!(matches("*", "practice.answer_graded"));
    assert!(matches("practice.*", "practice.answer_graded"));
    assert!(!matches("practice.*", "plan.adopted"));
}

#[test]
fn frontend_event_scope_covers_the_same_internal_namespaces() {
    // P-024：前端 pluginEventScope.ts 的内部事件命名空间清单必须与后端
    // INTERNAL_EVENT_NAMESPACES 一致——两侧各写一份时曾出现口径分叉：
    // 后端 matches 已对 state/draft/application 提前返回 false，前端仍把
    // `*` 视为命中任意 kind，页面注释声称「与后端同口径」却失真，
    // state.changed（打字保存的高频写路径）继续触发全量刷新。
    // 前端清单多出后端没有的命名空间同样会分叉（前端判为命中、后端不投递）。
    let frontend = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/pluginEventScope.ts"),
    )
    .unwrap();
    let array = frontend
        .split("export const INTERNAL_EVENT_NAMESPACES: readonly string[] = [")
        .nth(1)
        .and_then(|rest| rest.split(']').next())
        .expect("pluginEventScope.ts 的 INTERNAL_EVENT_NAMESPACES 清单缺失");
    for namespace in INTERNAL_EVENT_NAMESPACES {
        assert!(
            array.contains(&format!("'{namespace}'")),
            "前端内部事件命名空间缺少 {namespace}——与后端 INTERNAL_EVENT_NAMESPACES 同步"
        );
    }
    let listed = array.matches('\'').count() / 2;
    assert_eq!(
        listed,
        INTERNAL_EVENT_NAMESPACES.len(),
        "前端内部事件命名空间清单与后端数量不一致——两侧必须逐条对应"
    );
}
