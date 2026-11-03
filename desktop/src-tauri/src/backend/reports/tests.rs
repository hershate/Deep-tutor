use super::*;
use crate::backend::BackendState;
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
        .invoke_handler(tauri::generate_handler![
            commands::get_learning_report,
            commands::get_learning_calendar,
            commands::get_focus_overview,
            commands::start_focus_session,
            commands::complete_focus_session,
            commands::pause_focus_session,
            commands::resume_focus_session,
            commands::cancel_focus_session,
            commands::save_study_checkin,
            commands::adopt_report_recommendations,
            commands::export_learning_report,
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

#[test]
fn focus_completion_and_empty_report_use_real_time_and_no_fake_metrics() {
    let (app, learning) = setup();
    let empty = ipc(&app, "get_learning_report", json!({"period":"8w"})).unwrap();
    assert_eq!(empty["metrics"]["focusSeconds"], 0);
    assert_eq!(empty["metrics"]["correctPercent"], Value::Null);
    assert_eq!(empty["metrics"]["retentionPercent"], Value::Null);
    assert!(empty["trend"].as_array().unwrap().is_empty());
    assert_eq!(empty["recommendations"][0]["kind"], "focus");
    let started = ipc(
        &app,
        "start_focus_session",
        json!({"taskId":null,"plannedSeconds":1500}),
    )
    .unwrap();
    assert_eq!(started["status"], "running");
    assert_eq!(
        ipc(
            &app,
            "start_focus_session",
            json!({"taskId":null,"plannedSeconds":1500})
        )
        .unwrap_err()["code"],
        "focus_active"
    );
    let old = timestamp(now().unwrap() - Duration::seconds(3700)).unwrap();
    learning
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE focus_sessions SET started_at = ?1 WHERE id = 1",
            params![old],
        )
        .unwrap();
    let active = ipc(&app, "get_focus_overview", json!({})).unwrap();
    assert!(active["active"]["actualSeconds"].as_u64().unwrap() >= 3600);
    assert_eq!(active["active"]["remainingSeconds"], 0);
    let paused = ipc(&app, "pause_focus_session", json!({})).unwrap();
    assert_eq!(paused["status"], "paused");
    assert!(paused["actualSeconds"].as_u64().unwrap() >= 3600);
    let resumed = ipc(&app, "resume_focus_session", json!({})).unwrap();
    assert_eq!(resumed["status"], "running");
    assert!(resumed["actualSeconds"].as_u64().unwrap() >= 3600);
    let completed = ipc(&app, "complete_focus_session", json!({})).unwrap();
    assert_eq!(completed["status"], "completed");
    assert!(completed["actualSeconds"].as_u64().unwrap() >= 3600);
    assert_eq!(
        ipc(&app, "complete_focus_session", json!({})).unwrap_err()["code"],
        "no_active_focus"
    );
    assert_eq!(
        ipc(&app, "cancel_focus_session", json!({})).unwrap_err()["code"],
        "no_active_focus"
    );
    let report = ipc(&app, "get_learning_report", json!({"period":"8w"})).unwrap();
    assert!(report["metrics"]["focusSeconds"].as_u64().unwrap() >= 3600);
    assert!(report["timeComposition"]
        .as_array()
        .unwrap()
        .iter()
        .any(|slice| slice["kind"] == "学习" && slice["seconds"].as_u64().unwrap() >= 3600));
    let calendar = ipc(&app, "get_learning_calendar", json!({})).unwrap();
    assert_eq!(calendar["streak"], 1);
    assert!(
        calendar["days"].as_array().unwrap().last().unwrap()["minutes"]
            .as_u64()
            .unwrap()
            >= 60
    );
}

fn insert_plan(store: &Store) {
    let now = timestamp(now().unwrap()).unwrap();
    let input = json!({"goal":"真实方案","level":"基础","dailyMinutes":30,"deadline":"2099-01-01","materialIds":[]}).to_string();
    let result = json!({
        "goal":"真实方案","level":"基础","deadline":"2099-01-01","milestone":null,"milestoneDate":null,
        "graph":{"nodes":[{"id":"weak","title":"待巩固节点","summary":"真实节点","difficulty":2,"required":true,"estimatedMinutes":20,"citations":[]}],"edges":[]},
        "stages":[{"id":"stage-1","title":"真实阶段","nodeIds":["weak"],"difficulty":2,"minutes":20,"startDate":"2099-01-01","endDate":"2099-01-02"}],
        "schedule":{"startDate":"2099-01-01","endDate":"2099-01-02","totalMinutes":20,"days":2,"withinDeadline":true,"milestoneDate":null,"withinMilestone":null,"entries":[]},
        "resources":{},"validation":{"dependencyClosure":{"status":"pass","details":"真实"},"difficultyStep":{"status":"pass","details":"真实"},"cognitiveLoad":{"status":"pass","details":"真实"},"milestoneFeasibility":{"status":"pass","details":"真实"},"gapAnalysis":{"status":"pass","details":"真实"},"passed":true,"fixes":[]},"warnings":[]
    }).to_string();
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES ('done', 'p1', ?1, ?2, ?3, ?3)", params![input, result, now]).unwrap();
    store.conn.execute("INSERT INTO learning_plan_schedule_tasks (plan_id, task_id, date, stage_id, node_ids_json, minutes) VALUES (1, 1, '2099-01-01', 'stage-1', '[\"weak\"]', 20)", []).unwrap();
    store.conn.execute("INSERT INTO learning_reading_progress (plan_id, stage_id, completed, bookmarked, updated_at) VALUES (1, 'stage-1', 1, 0, ?1)", params![now]).unwrap();
    store.conn.execute("INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at) VALUES (1, 'weak', 'practice.objective', 0.25, 1, '{}', ?1)", params![now]).unwrap();
    store.conn.execute("INSERT INTO practice_sets (mode, status, plan_id, provider_id, difficulty, feedback_mode, kinds_json, total_questions, created_at, updated_at) VALUES ('targeted', 'ready', 1, 'p1', 'adaptive', 'immediate', '[]', 0, ?1, ?1)", params![now]).unwrap();
}

#[test]
fn report_links_plan_evidence_checkins_adoption_and_real_csv() {
    let (app, learning) = setup();
    let store = learning.store().unwrap();
    store.replace_collection("tasks", 0, &json!([{"id":1,"title":"真实任务","subject":"测试","duration":20,"done":false,"kind":"学习"}])).unwrap();
    insert_plan(&store);
    ipc(
        &app,
        "start_focus_session",
        json!({"taskId":1,"plannedSeconds":1500}),
    )
    .unwrap();
    store.conn.execute("UPDATE focus_sessions SET status = 'completed', actual_seconds = 1200, ended_at = ?1 WHERE id = 1", params![timestamp(now().unwrap()).unwrap()]).unwrap();
    let report = ipc(&app, "get_learning_report", json!({"period":"8w"})).unwrap();
    assert_eq!(report["plans"][0]["title"], "真实方案");
    assert_eq!(report["plans"][0]["focusSeconds"], 1200);
    assert_eq!(report["plans"][0]["completedStages"], 1);
    assert_eq!(report["plans"][0]["totalStages"], 1);
    assert_eq!(report["weekly"][0]["seconds"], 1200);
    assert_eq!(report["masteries"][0]["title"], "待巩固节点");
    assert_eq!(report["masteries"][0]["mastery"], 0.25);
    let id = report["recommendations"][0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    let adopted = ipc(
        &app,
        "adopt_report_recommendations",
        json!({"ids":[id.clone()]}),
    )
    .unwrap();
    assert_eq!(adopted["added"], 1);
    assert!(store
        .collection("tasks")
        .unwrap()
        .value
        .as_array()
        .unwrap()
        .iter()
        .any(|task| task["title"].as_str().unwrap().contains("待巩固节点")));
    assert_eq!(
        ipc(&app, "adopt_report_recommendations", json!({"ids":[id]})).unwrap()["added"],
        0
    );
    let today = day(OffsetDateTime::now_local().unwrap_or_else(|_| now().unwrap()));
    let calendar = ipc(
        &app,
        "save_study_checkin",
        json!({"input":{"date":today,"minutes":35,"note":"真实复盘"}}),
    )
    .unwrap();
    assert_eq!(
        calendar["days"].as_array().unwrap().last().unwrap()["source"],
        "self_report"
    );
    assert_eq!(
        calendar["days"].as_array().unwrap().last().unwrap()["minutes"],
        35
    );
    let destination = learning
        .workspace_path()
        .parent()
        .unwrap()
        .join("report.csv");
    let downloaded = ipc(
        &app,
        "export_learning_report",
        json!({"destination":destination.to_string_lossy(),"period":"8w"}),
    )
    .unwrap();
    let content = std::fs::read_to_string(&destination).unwrap();
    assert!(content.contains("真实方案") && content.contains("1200"));
    assert_eq!(downloaded["bytes"].as_u64().unwrap(), content.len() as u64);
    assert_eq!(
        downloaded["sha256"],
        format!("{:x}", Sha256::digest(content.as_bytes()))
    );
    assert_eq!(
        ipc(
            &app,
            "save_study_checkin",
            json!({"input":{"date":"2099-01-01","minutes":35,"note":"未来"}})
        )
        .unwrap_err()["code"],
        "future_checkin"
    );
}
