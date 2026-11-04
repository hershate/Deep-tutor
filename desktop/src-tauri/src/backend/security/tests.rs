use super::*;
use serde_json::{json, Value};
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};

pub(super) fn setup() -> (tauri::App<tauri::test::MockRuntime>, SecurityState) {
    let state = SecurityState::for_tests().unwrap();
    let app = tauri::test::mock_builder()
        .manage(state.shared())
        .invoke_handler(tauri::generate_handler![
            commands::get_security_overview,
            commands::login_local_user,
            commands::logout_local_user,
            commands::create_local_user,
            commands::set_local_user_active,
            commands::remove_local_user,
            commands::create_access_token,
            commands::revoke_access_token,
            commands::authenticate_access_token,
            commands::set_security_quota,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    (app, state)
}

pub(super) fn ipc(
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

#[test]
fn users_roles_tokens_and_deactivation_are_real_commands() {
    let (app, state) = setup();
    let initial = ipc(&app, "get_security_overview", json!({})).unwrap();
    assert_eq!(initial["currentUser"]["role"], "管理员");
    assert_eq!(initial["users"].as_array().unwrap().len(), 1);
    assert!(initial["modes"][0]["enabled"].as_bool().unwrap());
    assert!(!initial["modes"][2]["enabled"].as_bool().unwrap());
    let created = ipc(
        &app,
        "create_local_user",
        json!({"name":"学习伙伴","role":"成员"}),
    )
    .unwrap();
    assert_eq!(created["users"].as_array().unwrap().len(), 2);
    assert_eq!(
        ipc(
            &app,
            "create_local_user",
            json!({"name":"学习伙伴","role":"成员"})
        )
        .unwrap_err()["code"],
        "user_exists"
    );
    let member = ipc(&app, "login_local_user", json!({"userId":2})).unwrap();
    assert_eq!(member["currentUser"]["name"], "学习伙伴");
    assert_eq!(
        ipc(
            &app,
            "create_local_user",
            json!({"name":"越权","role":"管理员"})
        )
        .unwrap_err()["code"],
        "permission_denied"
    );
    let token = ipc(&app, "create_access_token", json!({"name":"原生扩展"})).unwrap();
    let raw = token["token"].as_str().unwrap();
    assert_eq!(raw.len(), 47);
    assert!(!token["view"]["mask"].as_str().unwrap().contains(raw));
    let store = state.store().unwrap();
    assert_ne!(
        store
            .conn
            .query_row(
                "SELECT token_hash FROM access_tokens WHERE id = 1",
                [],
                |row| row.get::<_, String>(0)
            )
            .unwrap(),
        raw
    );
    drop(store);
    assert_eq!(
        ipc(&app, "authenticate_access_token", json!({"token":raw})).unwrap()["id"],
        2
    );
    let listed = ipc(&app, "get_security_overview", json!({})).unwrap();
    assert!(listed["tokens"][0]["lastUsedAt"].is_string());
    ipc(&app, "revoke_access_token", json!({"tokenId":1})).unwrap();
    assert_eq!(
        ipc(&app, "authenticate_access_token", json!({"token":raw})).unwrap_err()["code"],
        "invalid_token"
    );
    ipc(&app, "login_local_user", json!({"userId":1})).unwrap();
    assert_eq!(
        ipc(
            &app,
            "set_local_user_active",
            json!({"userId":1,"active":false})
        )
        .unwrap_err()["code"],
        "cannot_disable_current"
    );
    ipc(
        &app,
        "set_local_user_active",
        json!({"userId":2,"active":false}),
    )
    .unwrap();
    assert_eq!(
        ipc(&app, "login_local_user", json!({"userId":2})).unwrap_err()["code"],
        "account_disabled"
    );
    ipc(
        &app,
        "set_local_user_active",
        json!({"userId":2,"active":true}),
    )
    .unwrap();
    ipc(&app, "remove_local_user", json!({"userId":2})).unwrap();
    let remaining = ipc(&app, "get_security_overview", json!({})).unwrap();
    assert_eq!(remaining["users"].as_array().unwrap().len(), 1);
    assert!(remaining["tokens"].as_array().unwrap().is_empty());
    let events = state.store().unwrap().events_since(0).unwrap();
    assert!(events
        .events
        .iter()
        .any(|event| event.kind == "security.user_created"));
    assert!(events
        .events
        .iter()
        .any(|event| event.kind == "security.token_revoked"));
}

#[test]
fn jwt_rejects_tampering_and_expiry_but_allows_a_new_local_login() {
    let (app, state) = setup();
    let overview = ipc(&app, "get_security_overview", json!({})).unwrap();
    let sid = overview["session"]["id"].as_str().unwrap();
    let token = state
        .credentials
        .get(&format!("session-{sid}"))
        .unwrap()
        .unwrap();
    assert_eq!(token.split('.').count(), 3);
    let store = state.store().unwrap();
    assert_eq!(verify(&store.conn, &state, &token).unwrap().id, 1);
    let mut forged = token.to_string();
    let last = forged.pop().unwrap();
    forged.push(if last == 'A' { 'B' } else { 'A' });
    assert_eq!(
        verify(&store.conn, &state, &forged).unwrap_err().code,
        "invalid_session"
    );
    let issued = now().unwrap();
    let expired = jwt(
        &secret(&state).unwrap(),
        sid,
        1,
        (issued - Duration::days(8)).unix_timestamp(),
        (issued - Duration::days(1)).unix_timestamp(),
    )
    .unwrap();
    store
        .conn
        .execute(
            "UPDATE auth_sessions SET expires_at = ?1 WHERE id = ?2",
            params![timestamp(issued - Duration::days(1)).unwrap(), sid],
        )
        .unwrap();
    state
        .credentials
        .set(&format!("session-{sid}"), &expired)
        .unwrap();
    assert_eq!(
        verify(&store.conn, &state, &expired).unwrap_err().code,
        "session_expired"
    );
    drop(store);
    let logged_out = ipc(&app, "get_security_overview", json!({})).unwrap();
    assert_eq!(logged_out["requiresLogin"], true);
    assert_eq!(logged_out["currentUser"], Value::Null);
    assert_eq!(logged_out["users"].as_array().unwrap().len(), 1);
    assert!(logged_out["tokens"].as_array().unwrap().is_empty());
    assert!(logged_out["quotas"].as_array().unwrap().is_empty());
    let renewed = ipc(&app, "login_local_user", json!({"userId":1})).unwrap();
    assert_ne!(renewed["session"]["id"], sid);
    let store = state.store().unwrap();
    assert_eq!(
        verify(
            &store.conn,
            &state,
            &state
                .credentials
                .get(&format!(
                    "session-{}",
                    renewed["session"]["id"].as_str().unwrap()
                ))
                .unwrap()
                .unwrap()
        )
        .unwrap()
        .id,
        1
    );
    assert_eq!(
        store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM events WHERE payload LIKE '%' || ?1 || '%'",
                params![token.as_str()],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        0
    );
}

#[test]
fn quotas_count_real_resources_and_backup_excludes_authentication_material() {
    let (app, state) = setup();
    ipc(&app, "get_security_overview", json!({})).unwrap();
    let store = Store::at_user(state.database_path.clone(), 1).unwrap();
    store.replace_collection("notes", 0, &json!([{"id":1,"title":"真实笔记","excerpt":"摘要","content":"内容","tag":"学习","updated":"刚刚"}])).unwrap();
    let materials = crate::backend::materials::MaterialState::new(&state.database_path).unwrap();
    with_user(1, || {
        crate::backend::materials::import_text(
            app.handle(),
            &materials,
            "配额材料".into(),
            "真实材料".into(),
        )
    })
    .unwrap();
    store.replace_collection("projects", 0, &json!([{"id":1,"title":"真实项目","description":"项目","due":"2099-01-01","progress":0,"tag":"项目","milestones":[]}])).unwrap();
    let providers = crate::backend::providers::ProviderState::for_tests_at(&state.database_path);
    let profile = with_user(1, || {
        let profile = crate::backend::providers::upsert(
            app.handle(),
            &providers,
            crate::backend::providers::ProfileInput {
                id: None,
                name: "本地测试".into(),
                preset: Some("custom".into()),
                kind: "本地模型".into(),
                protocol: "OpenAI Chat".into(),
                base_url: "http://127.0.0.1:9".into(),
                default_model: "test-model".into(),
                models: vec!["test-model".into()],
                enabled: Some(true),
                note: None,
                api_key: Some("test-only".into()),
            },
        )
        .unwrap();
        crate::backend::providers::set_active(app.handle(), &providers, &profile.id).unwrap();
        profile
    });
    let input = json!({"goal":"真实方案","level":"beginner","dailyMinutes":30,"deadline":"2099-01-01","materialIds":[]}).to_string();
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, created_at, updated_at) VALUES ('done', ?1, ?2, 'now', 'now')", params![profile.id, input]).unwrap();
    store.conn.execute("INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (1, 1, 'now')", []).unwrap();
    let learning =
        crate::backend::learning::LearningState::new(&state.database_path, &providers, &materials);
    let agent = crate::backend::agent::AgentState::new(&state.database_path, &providers, &learning);
    with_user(1, || {
        crate::backend::agent::new_session(
            app.handle(),
            &agent,
            Some("真实会话".into()),
            Some("plan-1".into()),
            None,
        )
    })
    .unwrap();
    store.conn.execute("INSERT INTO community_posts (title, body, kind, created_at, updated_at) VALUES ('真实讨论', '讨论内容', 'discussion', 'now', 'now')", []).unwrap();
    let token = ipc(&app, "create_access_token", json!({"name":"不进入备份"})).unwrap();
    let token_hash = hash(token["token"].as_str().unwrap());
    let overview = ipc(&app, "get_security_overview", json!({})).unwrap();
    for quota in overview["quotas"].as_array().unwrap() {
        assert_eq!(quota["used"], 1);
    }
    let limited = ipc(
        &app,
        "set_security_quota",
        json!({"userId":1,"resource":"notes","limit":77}),
    )
    .unwrap();
    assert!(limited["quotas"]
        .as_array()
        .unwrap()
        .iter()
        .any(|quota| quota["resource"] == "notes" && quota["limit"] == 77));
    let path = state
        .database_path
        .parent()
        .unwrap()
        .join("security.dtbackup");
    with_user(1, || {
        crate::backend::backup::export(app.handle(), &store, &learning, &path)
    })
    .unwrap();
    let preview = with_user(1, || crate::backend::backup::preview(&learning, &path)).unwrap();
    assert_eq!(preview.table_counts["access_tokens"], 0);
    assert_eq!(preview.table_counts["auth_sessions"], 0);
    let mut archive = zip::ZipArchive::new(std::fs::File::open(&path).unwrap()).unwrap();
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut archive.by_name("state.db").unwrap(), &mut bytes).unwrap();
    assert!(!bytes
        .windows(token_hash.len())
        .any(|window| window == token_hash.as_bytes()));
    assert_eq!(
        state
            .store()
            .unwrap()
            .conn
            .query_row("SELECT COUNT(*) FROM access_tokens", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        1
    );
    let target = crate::backend::learning::LearningState::for_tests().unwrap();
    let target_store = with_user(1, || target.store().unwrap());
    with_user(1, || {
        crate::backend::backup::import(app.handle(), &target_store, &target, &path)
    })
    .unwrap();
    assert_eq!(
        target_store
            .conn
            .query_row("SELECT COUNT(*) FROM local_users", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        target_store
            .conn
            .query_row("SELECT COUNT(*) FROM access_tokens", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        target_store
            .conn
            .query_row("SELECT COUNT(*) FROM auth_sessions", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        target_store
            .conn
            .query_row(
                "SELECT item_limit FROM security_quotas WHERE user_id = 1 AND resource = 'notes'",
                [],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        77
    );
}

#[test]
fn hmac_matches_the_standard_sha256_vector() {
    let actual = signature("key", "The quick brown fox jumps over the lazy dog")
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    assert_eq!(
        actual,
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    );
}

#[test]
fn base64url_round_trips_every_token_length() {
    for length in 1..=64_u8 {
        let bytes = (0..length).collect::<Vec<_>>();
        assert_eq!(
            decode(&super::super::random::encode(&bytes)).unwrap(),
            bytes
        );
    }
}

#[test]
fn account_store_is_reused_across_session_checks() {
    let state = SecurityState::for_tests().unwrap();
    {
        let store = state.store().unwrap();
        store.conn.pragma_update(None, "query_only", true).unwrap();
    }
    let store = state.store().unwrap();
    let query_only: bool = store
        .conn
        .pragma_query_value(None, "query_only", |row| row.get(0))
        .unwrap();
    assert!(
        query_only,
        "each session check must not reopen the account database"
    );
}
