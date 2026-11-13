use super::*;
use crate::backend::{security::SecurityState, BackendState};
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
        .manage(SecurityState::for_tests_at(&path))
        .invoke_handler(tauri::generate_handler![
            commands::search_workspace,
            commands::get_notifications,
            commands::mark_all_notifications_read,
            commands::mark_notification_read,
            commands::get_learning_profile,
            commands::save_learning_profile,
            commands::restart_learning_diagnostic,
            commands::get_dashboard,
            crate::backend::review::commands::create_review_card,
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

fn profile(name: &str) -> ProfileInput {
    // 47#4：起止相等（原先 00:00==00:00 的全天静音语义）已被校验拒绝——
    // 改用「当前时刻 ±1 分钟」的窗口，任何时刻运行测试都处于免打扰期
    let minute_of_day = {
        let local = super::super::store::now_local();
        u32::from(local.time().hour()) * 60 + u32::from(local.time().minute())
    };
    let (start, end) = (
        (minute_of_day + 1440 - 1) % 1440,
        (minute_of_day + 1) % 1440,
    );
    ProfileInput {
        name: name.into(),
        role: "自学者".into(),
        goal: "真实目标".into(),
        language: "zh-CN".into(),
        weekly_hours: 8,
        learning_style: "案例与练习结合".into(),
        explanation_style: "先追问，再针对讲解".into(),
        quiet_start: format!("{:02}:{:02}", start / 60, start % 60),
        quiet_end: format!("{:02}:{:02}", end / 60, end % 60),
        quiet_enabled: true,
        diagnostic_started_at: None,
    }
}

fn insert_plan(store: &Store) {
    let now = timestamp(now().unwrap()).unwrap();
    let input = json!({"goal":"真实目标","level":"基础","dailyMinutes":30,"deadline":"2099-01-01","materialIds":[]}).to_string();
    let result = json!({
        "goal":"真实目标","level":"基础","deadline":"2099-01-01","milestone":null,"milestoneDate":null,
        "graph":{"nodes":[{"id":"node-1","title":"真实知识节点","summary":"真实摘要","difficulty":1,"required":true,"estimatedMinutes":20,"citations":[]}],"edges":[]},
        "stages":[{"id":"stage-1","title":"真实阶段","nodeIds":["node-1"],"difficulty":1,"minutes":20,"startDate":"2099-01-01","endDate":"2099-01-02"}],
        "schedule":{"startDate":"2099-01-01","endDate":"2099-01-02","totalMinutes":20,"days":2,"withinDeadline":true,"milestoneDate":null,"withinMilestone":null,"entries":[]},
        "resources":{},"validation":{"dependencyClosure":{"status":"pass","details":"真实"},"difficultyStep":{"status":"pass","details":"真实"},"cognitiveLoad":{"status":"pass","details":"真实"},"milestoneFeasibility":{"status":"pass","details":"真实"},"gapAnalysis":{"status":"pass","details":"真实"},"passed":true,"fixes":[]},"warnings":[]
    }).to_string();
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES ('done', 'p1', ?1, ?2, ?3, ?3)", params![input, result, now]).unwrap();
    // 知识节点搜索只返回已采用方案（学习空间仅渲染已采用方案），夹具补一条采用记录
    store.conn.execute("INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (1, 1, ?1)", params![now]).unwrap();
    store.conn.execute("INSERT INTO learning_node_evidence (plan_id, node_id, kind, score, max_score, details_json, created_at) VALUES (1, 'node-1', 'practice.objective', 1, 1, '{}', ?1)", params![now]).unwrap();
}

#[test]
fn profile_search_and_dashboard_use_actual_workspace_data() {
    let (app, learning) = setup();
    let store = learning.store().unwrap();
    store.replace_collection("notes", 0, &json!([{"id":1,"title":"贝叶斯真实笔记","excerpt":"真实摘要","content":"真实内容","tag":"概率","updated":"刚刚"}])).unwrap();
    crate::backend::materials::import_text(
        app.handle(),
        &learning.material_state(),
        "真实教材".into(),
        "贝叶斯教材内容".into(),
    )
    .unwrap();
    insert_plan(&store);
    let saved_profile = ipc(
        &app,
        "save_learning_profile",
        json!({"input":profile("真实学习者")}),
    )
    .unwrap();
    assert_eq!(saved_profile["name"], "真实学习者");
    assert_eq!(saved_profile["weeklyHours"], 8);
    let mut custom_hours = profile("真实学习者");
    custom_hours.weekly_hours = 10;
    let mut custom_saved = ipc(
        &app,
        "save_learning_profile",
        json!({"input": custom_hours}),
    )
    .unwrap();
    assert_eq!(custom_saved["weeklyHours"], 10);
    custom_saved["language"] = json!("en-US");
    let language_saved = ipc(
        &app,
        "save_learning_profile",
        json!({"input": custom_saved}),
    )
    .unwrap();
    assert_eq!(language_saved["weeklyHours"], 10);
    let restarted = ipc(&app, "restart_learning_diagnostic", json!({})).unwrap();
    assert!(restarted["diagnosticStartedAt"].is_string());
    let mut invalid = profile("无效时间");
    invalid.quiet_start = "25:00".into();
    assert_eq!(
        ipc(&app, "save_learning_profile", json!({"input":invalid})).unwrap_err()["code"],
        "invalid_profile"
    );
    let pages = ipc(&app, "search_workspace", json!({"query":""})).unwrap();
    assert!(pages
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item["page"] == "reports"));
    let results = ipc(&app, "search_workspace", json!({"query":"真实"})).unwrap();
    assert!(results
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item["kind"] == "note" && item["title"] == "贝叶斯真实笔记"));
    assert!(results
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item["kind"] == "material" && item["title"] == "真实教材"));
    assert!(results
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item["kind"] == "node"
            && item["title"] == "真实知识节点"
            && item["subtitle"].as_str().unwrap().contains("100%")));
    let dashboard = ipc(&app, "get_dashboard", json!({})).unwrap();
    assert_eq!(dashboard["name"], "真实学习者");
    assert_eq!(dashboard["goal"], "真实目标");
    assert_eq!(dashboard["nextTask"]["title"], "真实阶段");
    assert_eq!(dashboard["nextTask"]["minutes"], 20);
}

#[test]
fn due_notifications_are_deduplicated_read_and_quiet() {
    let (app, learning) = setup();
    ipc(
        &app,
        "save_learning_profile",
        json!({"input":profile("真实学习者")}),
    )
    .unwrap();
    ipc(&app, "create_review_card", json!({"input":{"front":"真实问题","back":"真实答案","tag":null,"planId":null,"nodeId":null}})).unwrap();
    let notifications = ipc(&app, "get_notifications", json!({})).unwrap();
    assert!(notifications["unreadCount"].as_u64().unwrap() >= 1);
    assert!(notifications["suppressed"].as_bool().unwrap());
    assert!(notifications["items"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item["kind"] == "review" && item["title"].as_str().unwrap().contains("1 张")));
    let read = ipc(&app, "mark_all_notifications_read", json!({})).unwrap();
    assert_eq!(read["unreadCount"], 0);
    assert_eq!(
        ipc(&app, "get_notifications", json!({})).unwrap()["items"]
            .as_array()
            .unwrap()
            .len(),
        read["items"].as_array().unwrap().len()
    );
    let id = read["items"][0]["id"].as_str().unwrap();
    assert_eq!(
        ipc(&app, "mark_notification_read", json!({"id":id})).unwrap()["unreadCount"],
        0
    );
    assert_eq!(
        ipc(&app, "mark_notification_read", json!({"id":"missing"})).unwrap_err()["code"],
        "not_found"
    );
    let dashboard = ipc(&app, "get_dashboard", json!({})).unwrap();
    assert_eq!(dashboard["reviewDue"], 1);
    assert_eq!(dashboard["name"], "真实学习者");
    assert!(learning
        .store()
        .unwrap()
        .collection("preferences")
        .unwrap()
        .value["quietEnabled"]
        .as_bool()
        .unwrap());
}

#[test]
fn equal_quiet_times_save_for_allday_and_disabled() {
    // 114#2：设置页「全天不通知」（enabled+00:00/00:00，命中 quiet() 的
    // start==end 全天分支）与「关闭免打扰」（disabled+00:00/00:00，quiet()
    // 先查 enabled 直接返回 false）——47#4 的无条件相等禁令使两个选项
    // 永远无法保存且砖化存量同型档案；撤销后两种组合都必须落库成功
    let (app, _learning) = setup();
    let mut allday = profile("全天静音");
    allday.quiet_start = "00:00".into();
    allday.quiet_end = "00:00".into();
    allday.quiet_enabled = true;
    let saved = ipc(&app, "save_learning_profile", json!({"input":allday})).unwrap();
    assert_eq!(saved["quietStart"], "00:00");
    assert_eq!(saved["quietEnd"], "00:00");
    assert_eq!(saved["quietEnabled"], true);
    let mut disabled = profile("关闭免打扰");
    disabled.quiet_start = "00:00".into();
    disabled.quiet_end = "00:00".into();
    disabled.quiet_enabled = false;
    let saved = ipc(&app, "save_learning_profile", json!({"input":disabled})).unwrap();
    assert_eq!(saved["quietEnabled"], false);
    // 存量 enabled+00:00/00:00 档案路径：重新诊断全量回传 save 不再被拒
    let mut legacy = profile("存量档案");
    legacy.quiet_start = "00:00".into();
    legacy.quiet_end = "00:00".into();
    legacy.quiet_enabled = true;
    ipc(&app, "save_learning_profile", json!({"input":legacy})).unwrap();
    let restarted = ipc(&app, "restart_learning_diagnostic", json!({})).unwrap();
    assert!(restarted["diagnosticStartedAt"].is_string());
}

#[test]
fn frontend_upcoming_dates_use_compact_day_month_parts() {
    let source = include_str!("../../../../src/pages/Dashboard.tsx");
    assert!(!source.contains("<span className=\"upcoming-date\">{formatDate(item.date)}</span>"));
    assert!(source.contains("<small>{month}</small>"));
}

#[test]
fn frontend_narrow_page_headings_stack_actions() {
    let source = include_str!("../../../../src/styles.css");
    let narrow = source
        .split("@media (max-width: 639px) {\n  .ask-agent")
        .nth(1)
        .unwrap()
        .split(".mobile-bottom-nav")
        .next()
        .unwrap();
    assert!(narrow
        .contains(".page-heading { flex-direction: column; align-items: stretch; gap: 14px; }"));
    assert!(narrow.contains(".page-heading p:not(.date-line) { max-width: none; }"));
}
