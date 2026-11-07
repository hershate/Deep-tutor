use super::*;
use crate::backend::BackendState;
use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::{ipc::InvokeBody, webview::InvokeRequest, Manager};

fn fixture_result(goal: &str) -> Value {
    let check = || json!({"status": "pass", "details": "真实校验"});
    json!({
        "goal": goal,
        "level": "不进入分享快照的个人基础描述",
        "deadline": "2099-12-31",
        "milestone": "完成分类项目",
        "milestoneDate": null,
        "graph": {
            "nodes": [
                {"id": "probability", "title": "概率", "summary": "基础概念", "difficulty": 1, "required": true, "estimatedMinutes": 20, "citations": [{"materialId": 1, "quote": "概率"}]},
                {"id": "bayes", "title": "贝叶斯定理", "summary": "更新判断", "difficulty": 2, "required": true, "estimatedMinutes": 30, "citations": []}
            ],
            "edges": [{"from": "probability", "to": "bayes"}]
        },
        "stages": [{"id": "stage-1", "title": "理解并应用", "nodeIds": ["probability", "bayes"], "difficulty": 2, "minutes": 50, "startDate": "2099-01-01", "endDate": "2099-01-02"}],
        "schedule": {
            "startDate": "2099-01-01", "endDate": "2099-01-02", "totalMinutes": 50, "days": 2,
            "withinDeadline": true, "milestoneDate": null, "withinMilestone": null,
            "entries": [{"date": "2099-01-01", "stageId": "stage-1", "nodeIds": ["probability", "bayes"], "minutes": 50}]
        },
        "resources": {"items": [], "externalSearch": {"status": "disabled", "reason": "不进入分享", "queries": []}},
        "validation": {
            "dependencyClosure": check(), "difficultyStep": check(), "cognitiveLoad": check(),
            "milestoneFeasibility": check(), "gapAnalysis": check(), "passed": true, "fixes": []
        },
        "warnings": []
    })
}

fn add_plan(state: &SharingState, status: &str, goal: &str) -> i64 {
    let store = state.store().unwrap();
    let timestamp = OffsetDateTime::now_utc().format(&Rfc3339).unwrap();
    let input = json!({"goal": goal, "level": "私人基础", "dailyMinutes": 45, "deadline": "2099-12-31", "materialIds": [], "providerId": "private-provider", "milestone": null, "milestoneDate": null});
    let result = if status == "done" {
        Some(fixture_result(goal).to_string())
    } else {
        None
    };
    store.conn.execute(
        "INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES (?1, 'secret-provider', ?2, ?3, ?4, ?4)",
        params![status, input.to_string(), result, timestamp],
    ).unwrap();
    store.conn.last_insert_rowid()
}

fn setup() -> (tauri::App<tauri::test::MockRuntime>, SharingState) {
    let learning = crate::backend::learning::LearningState::for_tests().unwrap();
    let state = SharingState::new(&learning);
    let path = learning.database_path().to_path_buf();
    let providers = learning.provider_state();
    let materials = learning.material_state();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(state.store().unwrap()),
            materials: learning.material_state().shared(),
            providers: learning.provider_state(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(&path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(&path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(&path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(&path, &providers, &materials),
            community: crate::backend::community::CommunityState::new(&path),
            plugins: crate::backend::plugins::PluginState::new(&path).unwrap(),
            sharing: SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            commands::create_shared_plan,
            commands::list_shared_plans,
            commands::read_shared_plan,
            commands::revoke_shared_plan,
            commands::read_shared_plan_package,
            commands::export_shared_plan_package,
            commands::read_shared_plan_file,
            crate::backend::commands::events_since,
            crate::backend::commands::reset_application,
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    (app, state)
}

fn ipc(
    app: &tauri::App<tauri::test::MockRuntime>,
    command: &str,
    arguments: Value,
) -> std::result::Result<Value, Value> {
    let window = app.get_webview_window("main").unwrap();
    tauri::test::get_ipc_response(
        &window,
        InvokeRequest {
            cmd: command.into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost".parse().unwrap(),
            body: InvokeBody::Json(arguments),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.into(),
        },
    )
    .map(|body| body.deserialize::<Value>().unwrap())
}

#[test]
fn ipc_shares_real_immutable_snapshots_and_revokes_after_commit() {
    let (app, state) = setup();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let plan_id = add_plan(&state, "done", "真实贝叶斯学习目标");
    let created = ipc(
        &app,
        "create_shared_plan",
        json!({"planId": format!("plan-{plan_id}"), "expiresInDays": null}),
    )
    .unwrap();
    let id = created["share"]["id"].as_i64().unwrap();
    let token = created["token"].as_str().unwrap();
    assert_eq!(token.len(), 46);
    assert_eq!(created["share"]["status"], "active");
    let expiry =
        OffsetDateTime::parse(created["share"]["expiresAt"].as_str().unwrap(), &Rfc3339).unwrap();
    let created_at =
        OffsetDateTime::parse(created["share"]["createdAt"].as_str().unwrap(), &Rfc3339).unwrap();
    assert_eq!((expiry - created_at).whole_days(), 90);
    let view = ipc(&app, "read_shared_plan", json!({"token": token})).unwrap();
    assert_eq!(view["plan"]["goal"], "真实贝叶斯学习目标");
    assert_eq!(view["plan"]["dailyMinutes"], 45);
    assert_eq!(view["source"], "local");
    assert!(view["plan"].get("level").is_none());
    assert!(view["plan"].get("providerId").is_none());
    let listed = ipc(&app, "list_shared_plans", json!({"planId": null})).unwrap();
    assert!(!listed.to_string().contains(token));
    let store = state.store().unwrap();
    let hash = store
        .conn
        .query_row(
            "SELECT token_hash FROM shared_plans WHERE id = ?1",
            params![id],
            |row| row.get::<_, String>(0),
        )
        .unwrap();
    assert_eq!(hash, token_hash(token));
    store
        .conn
        .execute(
            "UPDATE learning_plans SET result_json = ?1 WHERE id = ?2",
            params![
                fixture_result("后续版本不应修改旧分享").to_string(),
                plan_id
            ],
        )
        .unwrap();
    assert_eq!(
        read_token(&state, token).unwrap().plan.goal,
        "真实贝叶斯学习目标"
    );
    drop(store);
    let persisted = SharingState::new(&crate::backend::learning::LearningState::new(
        &state.learning.database_path(),
        &crate::backend::providers::ProviderState::for_tests_at(state.learning.database_path()),
        &crate::backend::materials::MaterialState::new(state.learning.database_path()).unwrap(),
    ));
    assert_eq!(read_token(&persisted, token).unwrap().plan.stages.len(), 1);
    ipc(&app, "revoke_shared_plan", json!({"id": id})).unwrap();
    assert_eq!(
        ipc(&app, "read_shared_plan", json!({"token": token})).unwrap_err()["code"],
        "share_unavailable"
    );
    let events = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
    let kinds = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| event["kind"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(kinds, ["share.created", "share.revoked"]);
    ipc(&app, "reset_application", json!({})).unwrap();
    assert!(list(&state, None).unwrap().is_empty());
}

#[test]
fn packages_detect_corruption_and_expiration_without_claiming_remote_revocation() {
    let (app, state) = setup();
    let plan_id = add_plan(&state, "done", "离线只读方案");
    let running_id = add_plan(&state, "running", "尚未完成");
    assert_eq!(
        create(app.handle(), &state, &format!("plan-{running_id}"), None)
            .unwrap_err()
            .code,
        "plan_not_ready"
    );
    assert_eq!(
        create(app.handle(), &state, "plan-9999", None)
            .unwrap_err()
            .code,
        "not_found"
    );
    assert_eq!(
        create(app.handle(), &state, &format!("plan-{plan_id}"), Some(0))
            .unwrap_err()
            .code,
        "invalid_share_expiry"
    );
    assert_eq!(
        read_token(&state, "timestamp-not-secret").unwrap_err().code,
        "invalid_share_token"
    );
    let created = create(app.handle(), &state, &format!("plan-{plan_id}"), Some(7)).unwrap();
    let package: SharePackage = serde_json::from_str(&created.package).unwrap();
    assert_eq!(package.version, 1);
    let offline = read_package(&state, &created.package).unwrap();
    assert_eq!(offline.source, "offline");
    assert_eq!(offline.plan.graph.nodes.len(), 2);
    let mut corrupt = package.clone();
    corrupt.plan.goal = "被篡改的目标".into();
    assert_eq!(
        read_package(&state, &serde_json::to_string(&corrupt).unwrap())
            .unwrap_err()
            .code,
        "share_checksum_mismatch"
    );
    let mut unsupported = serde_json::to_value(&package).unwrap();
    unsupported["version"] = json!(2);
    assert_eq!(
        read_package(&state, &unsupported.to_string())
            .unwrap_err()
            .code,
        "unsupported_share_package"
    );
    revoke(app.handle(), &state, created.share.id).unwrap();
    assert!(read_package(&state, &created.package).is_ok());
    let expired = (OffsetDateTime::now_utc() - Duration::seconds(1))
        .format(&Rfc3339)
        .unwrap();
    state
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE shared_plans SET expires_at = ?1 WHERE id = ?2",
            params![expired, created.share.id],
        )
        .unwrap();
    assert_eq!(
        read_token(&state, &created.token).unwrap_err().code,
        "share_unavailable"
    );
    assert_eq!(list(&state, None).unwrap()[0].status, "revoked");
    assert_eq!(
        read_package(&state, &created.package).unwrap().source,
        "offline"
    );
    let expired_package = make_package(created.token, offline.plan, expired).unwrap();
    assert_eq!(
        read_package(&state, &expired_package).unwrap_err().code,
        "share_expired"
    );
}

#[test]
fn package_files_are_bounded_typed_and_atomically_written() {
    let (app, state) = setup();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let plan_id = add_plan(&state, "done", "文件分享");
    let created = ipc(
        &app,
        "create_shared_plan",
        json!({"planId": format!("plan-{plan_id}"), "expiresInDays": 30}),
    )
    .unwrap();
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("plan.dtshare");
    ipc(
        &app,
        "export_shared_plan_package",
        json!({"package": created["package"], "destination": destination.to_string_lossy()}),
    )
    .unwrap();
    let view = ipc(
        &app,
        "read_shared_plan_file",
        json!({"path": destination.to_string_lossy()}),
    )
    .unwrap();
    assert_eq!(view["plan"]["goal"], "文件分享");
    assert_eq!(ipc(&app, "export_shared_plan_package", json!({"package": created["package"], "destination": directory.path().join("unsafe.exe").to_string_lossy()})).unwrap_err()["code"], "invalid_share_destination");
    std::fs::write(
        &destination,
        created["package"]
            .as_str()
            .unwrap()
            .replace("文件分享", "篡改内容"),
    )
    .unwrap();
    assert_eq!(
        ipc(
            &app,
            "read_shared_plan_file",
            json!({"path": destination.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "share_checksum_mismatch"
    );
    assert_eq!(
        read_package_file(&state, &directory.path().join("private.txt"))
            .unwrap_err()
            .code,
        "invalid_share_file"
    );
}

#[test]
fn public_tokens_resolve_the_owner_without_opening_the_other_workspace() {
    let (app, state) = setup();
    let created = super::super::security::with_user(1, || {
        let plan_id = add_plan(&state, "done", "第一位用户的分享");
        create(app.handle(), &state, &format!("plan-{plan_id}"), None).unwrap()
    });
    super::super::security::with_user(2, || {
        assert!(list(&state, None).unwrap().is_empty());
    });
    let view = read_token(&state, &created.token).unwrap();
    assert_eq!(view.plan.goal, "第一位用户的分享");
    super::super::security::with_user(1, || {
        revoke(app.handle(), &state, created.share.id).unwrap();
    });
    assert_eq!(
        read_token(&state, &created.token).unwrap_err().code,
        "share_unavailable"
    );
}
