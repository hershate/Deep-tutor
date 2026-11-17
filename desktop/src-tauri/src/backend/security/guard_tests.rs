use super::*;
use crate::backend::{learning::LearningState, BackendState};
use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};

fn setup() -> (tauri::App<tauri::test::MockRuntime>, LearningState) {
    let learning = LearningState::for_tests().unwrap();
    let path = learning.database_path().to_path_buf();
    let providers = learning.provider_state();
    let security = SecurityState::for_tests_at(&path);
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                &path,
                &providers,
                &learning.material_state(),
            ),
            community: crate::backend::community::CommunityState::new(&path),
            plugins: crate::backend::plugins::PluginState::new(&path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .manage(security)
        .invoke_handler(guard_handler(tauri::generate_handler![
            crate::backend::commands::bootstrap,
            crate::backend::commands::get_app_state,
            crate::backend::commands::replace_collection,
            crate::backend::materials::commands::import_material_text,
            crate::backend::materials::commands::list_materials,
            commands::get_security_overview,
            crate::backend::providers::commands::list_provider_profiles,
            crate::backend::providers::commands::upsert_provider_profile,
            crate::backend::community::commands::get_community_overview,
            crate::backend::community::commands::create_community_post,
            commands::create_local_user,
            commands::login_local_user,
            commands::set_security_quota,
        ]))
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

fn note(id: i64, title: &str) -> Value {
    json!({"id":id,"title":title,"excerpt":"私有摘要","content":"私有内容","tag":"学习","updated":"刚刚"})
}

#[test]
fn guarded_commands_switch_real_workspace_connections_and_enforce_quota() {
    let (app, learning) = setup();
    let initial = ipc(&app, "bootstrap", json!({})).unwrap();
    assert!(initial["notes"].as_array().unwrap().is_empty());
    assert_eq!(
        ipc(&app, "get_security_overview", json!({})).unwrap()["currentUser"]["id"],
        1
    );
    ipc(
        &app,
        "create_local_user",
        json!({"name":"第二位用户","role":"成员"}),
    )
    .unwrap();
    ipc(
        &app,
        "replace_collection",
        json!({"namespace":"notes","expectedRevision":0,"value":[note(1,"第一位私有笔记")]}),
    )
    .unwrap();
    let material = ipc(
        &app,
        "import_material_text",
        json!({"title":"第一位材料","content":"第一位原件"}),
    )
    .unwrap();
    assert_eq!(material["material"]["id"], 1);
    assert!(learning
        .workspace_path()
        .parent()
        .unwrap()
        .join("users/1/materials/originals")
        .is_dir());
    ipc(
        &app,
        "set_security_quota",
        json!({"userId":1,"resource":"notes","limit":1}),
    )
    .unwrap();

    let switched = ipc(&app, "login_local_user", json!({"userId":2})).unwrap();
    assert_eq!(switched["currentUser"]["id"], 2);
    assert!(ipc(&app, "get_app_state", json!({})).unwrap()["notes"]
        .as_array()
        .unwrap()
        .is_empty());
    assert!(ipc(&app, "list_materials", json!({}))
        .unwrap()
        .as_array()
        .unwrap()
        .is_empty());
    ipc(
        &app,
        "replace_collection",
        json!({"namespace":"notes","expectedRevision":0,"value":[note(9,"第二位私有笔记")]}),
    )
    .unwrap();
    ipc(&app, "login_local_user", json!({"userId":1})).unwrap();
    assert_eq!(
        ipc(&app, "get_app_state", json!({})).unwrap()["notes"][0]["title"],
        "第一位私有笔记"
    );
    assert_eq!(
        ipc(&app, "list_materials", json!({}))
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(ipc(&app, "replace_collection", json!({"namespace":"notes","expectedRevision":1,"value":[note(1,"第一位私有笔记"),note(2,"超限笔记")]})).unwrap_err()["code"], "quota_exceeded");
    ipc(&app, "login_local_user", json!({"userId":2})).unwrap();
    assert_eq!(
        ipc(&app, "get_app_state", json!({})).unwrap()["notes"][0]["title"],
        "第二位私有笔记"
    );
    assert!(learning
        .store()
        .unwrap()
        .collection("notes")
        .unwrap()
        .value
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn expired_session_offers_login_then_switches_workspace() {
    let (app, _) = setup();
    ipc(&app, "bootstrap", json!({})).unwrap();
    ipc(
        &app,
        "create_local_user",
        json!({"name":"过期后用户","role":"成员"}),
    )
    .unwrap();
    let security = app.state::<SecurityState>();
    let overview = ipc(&app, "get_security_overview", json!({})).unwrap();
    let sid = overview["session"]["id"].as_str().unwrap();
    let issued = now().unwrap();
    let expired = jwt(
        &secret(&security).unwrap(),
        sid,
        1,
        (issued - Duration::days(8)).unix_timestamp(),
        (issued - Duration::days(1)).unix_timestamp(),
    )
    .unwrap();
    security
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE auth_sessions SET expires_at = ?1 WHERE id = ?2",
            params![timestamp(issued - Duration::days(1)).unwrap(), sid],
        )
        .unwrap();
    security
        .credentials
        .set(&format!("session-{sid}"), &expired)
        .unwrap();
    assert_eq!(
        ipc(&app, "get_app_state", json!({})).unwrap_err()["code"],
        "session_expired"
    );
    let gate = ipc(&app, "get_security_overview", json!({})).unwrap();
    assert_eq!(gate["requiresLogin"], true);
    assert_eq!(gate["users"].as_array().unwrap().len(), 2);
    let renewed = ipc(&app, "login_local_user", json!({"userId":2})).unwrap();
    assert_eq!(renewed["currentUser"]["id"], 2);
    assert!(ipc(&app, "get_app_state", json!({})).unwrap()["notes"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn async_commands_derive_principal_from_session_not_thread_local() {
    // 23#7：tauri 把 async 命令体 spawn 到 tokio worker，guard 在 IPC 线程
    // 设置的 thread-local principal 到不了那里——命令必须从会话派生当前
    // 用户；曾 fail-open 落回 user1 工作区把 user2 的档案写进别人的库
    let (app, learning) = setup();
    ipc(&app, "bootstrap", json!({})).unwrap();
    ipc(
        &app,
        "create_community_post",
        json!({"input":{"title":"user1 私有讨论","body":"仅 user1 可见","kind":"discussion","projectId":null}}),
    )
    .unwrap();
    let created = ipc(
        &app,
        "create_local_user",
        json!({"name": "第二用户", "role": "成员"}),
    )
    .unwrap();
    let second = created["users"]
        .as_array()
        .unwrap()
        .iter()
        .find(|user| user["name"] == "第二用户")
        .unwrap()["id"]
        .as_i64()
        .unwrap();
    ipc(&app, "login_local_user", json!({"userId": second})).unwrap();
    let profile = ipc(
        &app,
        "upsert_provider_profile",
        json!({"input": {
            "name": "user2 档案", "kind": "本地模型", "protocol": "OpenAI Chat",
            "baseUrl": "http://127.0.0.1:1", "defaultModel": "m", "models": ["m"]
        }}),
    )
    .unwrap();
    let profile_id = profile["id"].as_str().unwrap().to_string();
    let user2 =
        crate::backend::store::Store::at_user(learning.database_path().to_path_buf(), second)
            .unwrap();
    let in_user2: i64 = user2
        .conn
        .query_row(
            "SELECT COUNT(*) FROM provider_profiles WHERE name = 'user2 档案'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(in_user2, 1, "user2 的档案必须写入 user2 工作区");
    let user1 =
        crate::backend::store::Store::at_user(learning.database_path().to_path_buf(), 1).unwrap();
    let in_user1: i64 = user1
        .conn
        .query_row(
            "SELECT COUNT(*) FROM provider_profiles WHERE name = 'user2 档案'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(in_user1, 0, "user1 工作区不得出现 user2 的档案");
    // 同步读命令在 guard 的 with_user(second) 下应列出该档案
    let listed = ipc(&app, "list_provider_profiles", json!({})).unwrap();
    assert!(
        listed
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["id"].as_str() == Some(&profile_id)),
        "user2 视角的列表必须包含其档案"
    );
    let worker_app = app.handle().clone();
    let community = std::thread::spawn(move || {
        let command_app = worker_app.clone();
        let state = worker_app.state::<BackendState>();
        tauri::async_runtime::block_on(crate::backend::community::commands::get_community_overview(
            command_app,
            state,
            None,
            None,
        ))
        .unwrap()
    })
    .join()
    .unwrap();
    assert!(
        community.posts.is_empty(),
        "无 IPC 线程 principal 的 async 工作线程必须仍从会话打开 user2 工作区"
    );
}

// 64#5：命令在途与用户切换并发——写线程持续导入资料，主线程高频切换
// 本地用户制造「命令在途」窗口；每条命令按其执行时刻的 principal 落库，
// 两用户工作区各只含本会话写入（无跨用户串写、无死锁、无失败）
#[test]
fn concurrent_imports_during_user_switches_stay_isolated() {
    let (app, learning) = setup();
    ipc(&app, "bootstrap", json!({})).unwrap();
    ipc(
        &app,
        "create_local_user",
        json!({"name":"切换并发用户","role":"成员"}),
    )
    .unwrap();
    let user_root = learning.workspace_path().parent().unwrap().to_path_buf();
    let handle = app.handle().clone();
    let total = 30usize;
    let written = std::thread::scope(|scope| {
        let writer = scope.spawn(move || {
            let window = handle.get_webview_window("main").unwrap();
            let mut ok = 0usize;
            for index in 0..total {
                let response = tauri::test::get_ipc_response(
                    &window,
                    InvokeRequest {
                        cmd: "import_material_text".into(),
                        callback: CallbackFn(0),
                        error: CallbackFn(1),
                        url: "http://tauri.localhost".parse().unwrap(),
                        body: InvokeBody::Json(json!({
                            "title": format!("切换{index}.txt"),
                            "content": format!("切换正文{index}")
                        })),
                        headers: Default::default(),
                        invoke_key: tauri::test::INVOKE_KEY.into(),
                    },
                );
                if response
                    .map(|body| body.deserialize::<Value>().is_ok())
                    .unwrap_or(false)
                {
                    ok += 1;
                }
            }
            ok
        });
        let mut user = 1;
        while !writer.is_finished() {
            ipc(&app, "login_local_user", json!({"userId": user})).unwrap();
            user = if user == 1 { 2 } else { 1 };
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        writer.join().unwrap()
    });
    assert_eq!(written, total, "切换窗口期间导入不得失败");
    ipc(&app, "login_local_user", json!({"userId": 1})).unwrap();
    let first = ipc(&app, "list_materials", json!({}))
        .unwrap()
        .as_array()
        .unwrap()
        .len();
    ipc(&app, "login_local_user", json!({"userId": 2})).unwrap();
    let second = ipc(&app, "list_materials", json!({}))
        .unwrap()
        .as_array()
        .unwrap()
        .len();
    assert_eq!(first + second, total, "资料行必须全部落库且互不重复");
    assert!(
        first > 0 && second > 0,
        "切换窗口必须实际覆盖两个用户：{first}/{second}"
    );
    let originals = |user: i64| user_root.join(format!("users/{user}/materials/originals"));
    let file_count = |user: i64| {
        std::fs::read_dir(originals(user))
            .map(|entries| entries.count())
            .unwrap_or(0)
    };
    assert_eq!(
        file_count(1) + file_count(2),
        total,
        "原件文件同样按用户隔离"
    );
}

#[test]
fn workspace_ownership_recheck_rejects_operations_after_a_user_switch() {
    // 150#9：备份导入/应用重置是「先停机再取 store 锁」的阻塞命令体，
    // 其间可能发生工作区切换——进入关键段时复核会话用户与命令发起时
    // 捕获的 principal，不一致即中止，避免把一人的备份写进（或清空）
    // 另一人的工作区
    let (app, _learning) = setup();
    ipc(&app, "bootstrap", json!({})).unwrap();
    ipc(
        &app,
        "create_local_user",
        json!({"name":"归属复核用户","role":"成员"}),
    )
    .unwrap();
    assert_eq!(
        ipc(&app, "get_security_overview", json!({})).unwrap()["currentUser"]["id"],
        1
    );
    // 未切换时归属一致，放行
    guard::ensure_workspace(app.handle(), Some(1)).unwrap();
    ipc(&app, "login_local_user", json!({"userId":2})).unwrap();
    // 切换后仍按旧 principal 发起的操作必须被拒绝
    let error = guard::ensure_workspace(app.handle(), Some(1)).unwrap_err();
    assert_eq!(error.code, "workspace_switched");
    // 当前会话用户与 principal 一致时放行
    guard::ensure_workspace(app.handle(), Some(2)).unwrap();
}

#[test]
fn blocking_workspace_operations_exclude_user_switches() {
    // 150#9：备份导入/应用重置的关键段持 WORKSPACE_SWITCH 写锁——
    // 用户切换与关键段不得交错（交错即产生跨用户写入）。本测试验证
    // 写锁的互斥语义：持锁期间切换不得完成，释放后必须完成
    let (app, _learning) = setup();
    ipc(&app, "bootstrap", json!({})).unwrap();
    ipc(
        &app,
        "create_local_user",
        json!({"name":"互斥用户","role":"成员"}),
    )
    .unwrap();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let holder = std::thread::spawn(move || {
        guard::exclusive(|| {
            started_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
    });
    started_rx.recv().unwrap();
    let handle = app.handle().clone();
    let (invoked_tx, invoked_rx) = std::sync::mpsc::channel();
    let switcher = std::thread::spawn(move || {
        let window = handle.get_webview_window("main").unwrap();
        invoked_tx.send(()).unwrap();
        tauri::test::get_ipc_response(
            &window,
            InvokeRequest {
                cmd: "login_local_user".into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: "http://tauri.localhost".parse().unwrap(),
                body: InvokeBody::Json(json!({"userId": 2})),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.into(),
            },
        )
    });
    invoked_rx.recv().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));
    assert!(
        !switcher.is_finished(),
        "关键段持切换写锁期间用户切换不得完成"
    );
    release_tx.send(()).unwrap();
    holder.join().unwrap();
    let response = switcher.join().unwrap();
    assert!(
        response
            .map(|body| body.deserialize::<Value>().is_ok())
            .unwrap_or(false),
        "释放关键段后用户切换必须完成"
    );
}

#[test]
fn frontend_session_event_survives_snapshot_coalescing() {
    let source = include_str!("../../../../src/backend/useBackendState.ts");
    let coalesced = source
        .split("const requestSyncCoalesced")
        .nth(1)
        .unwrap()
        .split("void onBackendEvent")
        .next()
        .unwrap();
    assert!(
        coalesced.contains("requestSync(event)"),
        "去抖回调必须保留会话事件并触发工作区代际切换"
    );
}
