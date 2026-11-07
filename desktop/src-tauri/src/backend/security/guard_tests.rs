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
}
