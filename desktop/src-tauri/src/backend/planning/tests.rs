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
            commands::get_plan_overview,
            commands::get_plan_alternatives,
            commands::apply_plan_intensity,
            commands::update_learning_goal,
            commands::assign_task_date,
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

fn insert_plan(store: &Store) {
    let now = timestamp(OffsetDateTime::now_utc()).unwrap();
    let input = json!({"goal":"真实目标","level":"部分掌握","dailyMinutes":30,"deadline":"2099-01-01","materialIds":[]}).to_string();
    let result = json!({
        "goal":"真实目标","level":"部分掌握","deadline":"2099-01-01","milestone":null,"milestoneDate":null,
        "graph":{"nodes":[{"id":"node-1","title":"真实知识节点","summary":"真实摘要","difficulty":1,"required":true,"estimatedMinutes":20,"citations":[]}],"edges":[]},
        "stages":[{"id":"stage-1","title":"真实阶段","nodeIds":["node-1"],"difficulty":1,"minutes":20,"startDate":"2026-11-02","endDate":"2026-11-03"}],
        "schedule":{"startDate":"2026-11-02","endDate":"2026-11-03","totalMinutes":20,"days":2,"withinDeadline":true,"milestoneDate":null,"withinMilestone":null,"entries":[{"date":"2026-11-02","stageId":"stage-1","nodeIds":["node-1"],"minutes":20}]},
        "resources":{},"validation":{"dependencyClosure":{"status":"pass","details":"真实"},"difficultyStep":{"status":"pass","details":"真实"},"cognitiveLoad":{"status":"pass","details":"真实"},"milestoneFeasibility":{"status":"pass","details":"真实"},"gapAnalysis":{"status":"pass","details":"真实"},"passed":true,"fixes":[]},"warnings":[]
    }).to_string();
    store.conn.execute("INSERT INTO learning_plans (status, provider_id, input_json, result_json, created_at, updated_at) VALUES ('done', 'p1', ?1, ?2, ?3, ?3)", params![input, result, now]).unwrap();
    store.replace_collection("tasks", 0, &json!([
        {"id":1,"title":"真实排期任务","subject":"真实目标","duration":20,"done":false,"kind":"学习"},
        {"id":2,"title":"真实待排任务","subject":"真实目标","duration":40,"done":false,"kind":"练习"},
    ])).unwrap();
    store.conn.execute("INSERT INTO learning_plan_schedule_tasks (plan_id, task_id, date, stage_id, node_ids_json, minutes) VALUES (1, 1, '2026-11-02', 'stage-1', '[\"node-1\"]', 20)", []).unwrap();
    store.conn.execute("INSERT INTO learning_reading_progress (plan_id, stage_id, completed, bookmarked, updated_at) VALUES (1, 'stage-1', 1, 0, ?1)", params![now]).unwrap();
}

#[test]
fn ipc_schedule_intensity_and_task_dates_use_persistent_data() {
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    let overview = ipc(&app, "get_plan_overview", json!({"weekStart":"2026-11-04"})).unwrap();
    assert_eq!(overview["weekStart"], "2026-11-02");
    assert_eq!(overview["weeklyMinutes"], 20);
    assert_eq!(overview["days"][0]["events"][0]["title"], "真实排期任务");
    assert_eq!(overview["days"][0]["events"][0]["minutes"], 20);
    assert_eq!(overview["unscheduled"][0]["taskId"], 2);
    assert!(overview["unscheduled"][0]["date"].is_null());
    assert_eq!(overview["stages"][0]["completed"], true);
    let alternatives = ipc(
        &app,
        "get_plan_alternatives",
        json!({"weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(alternatives[0]["weeklyMinutes"], 16);
    assert_eq!(alternatives[1]["weeklyMinutes"], 20);
    assert_eq!(alternatives[2]["weeklyMinutes"], 24);
    let updated = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"冲刺","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(updated["weeklyMinutes"], 24);
    assert_eq!(updated["intensity"], "冲刺");
    let store = learning.store().unwrap();
    assert_eq!(store.collection("tasks").unwrap().value[0]["duration"], 24);
    assert_eq!(
        store.collection("preferences").unwrap().value["planIntensity"],
        "冲刺"
    );
    assert_eq!(
        store.collection("preferences").unwrap().value["learningStyle"],
        "案例与练习结合"
    );
    let plan = crate::backend::learning::get(&learning, "plan-1").unwrap();
    assert_eq!(plan.result.unwrap().schedule.total_minutes, 24);
    assert_eq!(plan.daily_minutes, 36);
    let reverted = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"标准","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(reverted["weeklyMinutes"], 20);
    assert_eq!(
        crate::backend::learning::get(&learning, "plan-1")
            .unwrap()
            .daily_minutes,
        30
    );
    assert_eq!(
        ipc(&app, "apply_plan_intensity", json!({"intensity":"过量"})).unwrap_err()["code"],
        "invalid_intensity"
    );
    // 34#3：切强度（在同版本落 versions 快照）后 regenerate 不得撞
    // (plan_id, version) 主键——原裸 INSERT 使切过强度的方案永久不可重生成
    let store = learning.store().unwrap();
    let versions: i64 = store
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_plan_versions WHERE plan_id = 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(versions >= 1, "强度切换应先落版本快照");
    crate::backend::learning::actions::regenerate(app.handle(), &learning, "1", 6).unwrap();
    let regenerated = crate::backend::learning::get(&learning, "plan-1").unwrap();
    assert_eq!(regenerated.status, "running");
    assert_eq!(regenerated.version, versions as u32 + 1);
    let scheduled = ipc(
        &app,
        "assign_task_date",
        json!({"taskId":2,"dateValue":"2026-11-03"}),
    )
    .unwrap();
    assert_eq!(scheduled["days"][1]["events"][0]["taskId"], 2);
    assert!(scheduled["unscheduled"].as_array().unwrap().is_empty());
    let rescheduled = ipc(
        &app,
        "assign_task_date",
        json!({"taskId":2,"dateValue":"2026-11-04"}),
    )
    .unwrap();
    assert_eq!(rescheduled["days"][2]["events"][0]["taskId"], 2);
    assert_eq!(learning.store().unwrap().conn.query_row("SELECT COUNT(*) FROM learning_plan_schedule_tasks WHERE plan_id = 1 AND task_id = 2", [], |row| row.get::<_, i64>(0)).unwrap(), 1);
}

#[test]
fn goal_update_enters_real_regeneration_and_empty_plan_is_not_invented() {
    let (app, learning) = setup();
    let empty = ipc(&app, "get_plan_overview", json!({})).unwrap();
    assert!(empty["plan"].is_null());
    assert!(empty["days"]
        .as_array()
        .unwrap()
        .iter()
        .all(|day| day["events"].as_array().unwrap().is_empty()));
    assert_eq!(
        ipc(
            &app,
            "update_learning_goal",
            json!({"input":{"goal":"新目标","deadline":"2099-01-01","weeklyHours":15}})
        )
        .unwrap_err()["code"],
        "no_plan"
    );
    insert_plan(&learning.store().unwrap());
    let started = ipc(&app, "update_learning_goal", json!({"input":{"planId":"plan-1","goal":"新的真实目标","deadline":"2099-02-01","weeklyHours":21,"level":"基础"}})).unwrap();
    assert_eq!(started["status"], "running");
    assert_eq!(started["version"], 2);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let plan = loop {
        let plan = crate::backend::learning::get(&learning, "plan-1").unwrap();
        if plan.status != "running" {
            break plan;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "再生成未进入真实失败终态"
        );
        std::thread::sleep(std::time::Duration::from_millis(10));
    };
    assert_eq!(plan.goal, "新的真实目标");
    assert_eq!(plan.daily_minutes, 180);
    assert_eq!(plan.status, "failed");
    assert_eq!(plan.error.unwrap().code, "not_found");
    assert!(plan.result.is_none());
    assert_eq!(
        learning
            .store()
            .unwrap()
            .collection("preferences")
            .unwrap()
            .value["weeklyHours"],
        21
    );
}

#[test]
fn adopted_plan_keeps_its_goal_and_history() {
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    crate::backend::learning::actions::adopt(app.handle(), &learning, "plan-1").unwrap();
    let error = ipc(&app, "update_learning_goal", json!({"input":{"planId":"plan-1","goal":"不能覆盖旧目标","deadline":"2099-02-01","weeklyHours":21}})).unwrap_err();
    assert_eq!(error["code"], "plan_adopted");
    let plan = crate::backend::learning::get(&learning, "plan-1").unwrap();
    assert_eq!(plan.goal, "真实目标");
    assert!(plan.result.is_some());
    assert!(plan.adopted_project_id.is_some());
}

#[test]
fn scaled_keeps_small_entries_and_clamps_only_daily() {
    // 52#1：条目/任务/阶段分钟不再套 10..=600 钳制——排期装填合法产出
    // 1–9 分钟尾条目，旧实现切强度抬到 10 且再切回时单调卡死；
    // 每日分钟（scaled_daily）仍钳界内保证排期步硬校验可重生成
    assert_eq!(scaled(2, "标准", "冲刺").unwrap(), 2);
    assert_eq!(scaled(2, "冲刺", "标准").unwrap(), 2);
    assert_eq!(scaled(2, "标准", "稳妥").unwrap(), 2);
    assert_eq!(scaled(0, "标准", "冲刺").unwrap(), 0);
    assert_eq!(scaled(600, "标准", "冲刺").unwrap(), 720);
    assert_eq!(scaled_daily(2, "标准", "冲刺").unwrap(), 10);
    assert_eq!(scaled_daily(600, "标准", "冲刺").unwrap(), 600);
}

#[test]
fn intensity_switch_scales_from_the_plan_own_base_not_the_global_preference() {
    // P-002：新建方案的排期从未乘过强度因子——全局偏好只是上一次强度切换
    // 留下的遗留值，拿它当缩放基数会把新建方案按遗留强度错缩（×0.833/×1.2）
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    let store = learning.store().unwrap();
    let record = store.collection("preferences").unwrap();
    let mut preferences = record.value;
    preferences["planIntensity"] = json!("冲刺");
    store
        .replace_collection("preferences", record.revision, &preferences)
        .unwrap();
    // 遗留强度不得被显示为当前方案的强度
    let overview = ipc(&app, "get_plan_overview", json!({"weekStart":"2026-11-04"})).unwrap();
    assert_eq!(overview["intensity"], "标准");
    // 切到「标准」不得按遗留的冲刺缩小（20 → 17 是旧行为）
    let updated = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"标准","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(updated["weeklyMinutes"], 20);
    assert_eq!(updated["intensity"], "标准");
    assert_eq!(store.collection("tasks").unwrap().value[0]["duration"], 20);
    let plan = crate::backend::learning::get(&learning, "plan-1").unwrap();
    assert_eq!(plan.daily_minutes, 30);
    assert_eq!(plan.result.unwrap().schedule.total_minutes, 20);
}

#[test]
fn intensity_switch_records_the_plan_own_intensity_for_the_next_switch() {
    // P-002：强度切换把生效强度记到方案自身，下一次切换按它换算——
    // 往返切换必须回到原基数，不得在错误基数上累积漂移
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    let store = learning.store().unwrap();
    let up = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"冲刺","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(up["weeklyMinutes"], 24);
    assert_eq!(up["intensity"], "冲刺");
    let input: Value = serde_json::from_str(
        &store
            .conn
            .query_row(
                "SELECT input_json FROM learning_plans WHERE id = 1",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap(),
    )
    .unwrap();
    assert_eq!(input["intensity"], "冲刺");
    let down = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"标准","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(down["weeklyMinutes"], 20);
    assert_eq!(down["intensity"], "标准");
    assert_eq!(store.collection("tasks").unwrap().value[0]["duration"], 20);
}

#[test]
fn legacy_plan_without_intensity_record_declares_the_stored_minutes_as_the_base() {
    // P-002：强度记录（input_json.intensity）引入前创建的方案，库内没有
    // 任何字段记录旧版 apply_intensity 当时用的缩放因子——真实基数不可反推。
    // 口径显式声明为「库内现有分钟即基数」：总览标记 intensity_legacy 供
    // 界面明示，首次切换以库内现有分钟换算并把生效强度写回方案自身，
    // 之后不再在未知因子上累积
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    let store = learning.store().unwrap();
    // insert_plan 写入的 input_json 无 intensity 键 = 强度记录引入前的形态
    let overview = ipc(&app, "get_plan_overview", json!({"weekStart":"2026-11-02"})).unwrap();
    assert_eq!(overview["intensity"], "标准");
    assert_eq!(
        overview["intensityLegacy"], true,
        "无强度记录的方案必须标记为遗留基数"
    );
    // 全局偏好只是遗留值：遗留基数的首次切换以库内现有分钟为基准（20 → 24），
    // 不按全局遗留强度错缩
    let record = store.collection("preferences").unwrap();
    let mut preferences = record.value;
    preferences["planIntensity"] = json!("冲刺");
    store
        .replace_collection("preferences", record.revision, &preferences)
        .unwrap();
    let updated = ipc(
        &app,
        "apply_plan_intensity",
        json!({"intensity":"冲刺","weekStart":"2026-11-02"}),
    )
    .unwrap();
    assert_eq!(updated["weeklyMinutes"], 24);
    assert_eq!(
        updated["intensityLegacy"], false,
        "切换后强度写回方案自身，基数口径转为显式"
    );
    let input: Value = serde_json::from_str(
        &store
            .conn
            .query_row(
                "SELECT input_json FROM learning_plans WHERE id = 1",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap(),
    )
    .unwrap();
    assert_eq!(input["intensity"], "冲刺");
    // 新建方案的 input_json 带 intensity 键（null = 从未缩放过）：不是遗留基数
    store
        .conn
        .execute(
            "UPDATE learning_plans SET input_json = ?1 WHERE id = 1",
            params![json!({"goal":"真实目标","level":"部分掌握","dailyMinutes":30,"deadline":"2099-01-01","materialIds":[],"intensity":null}).to_string()],
        )
        .unwrap();
    let fresh = ipc(&app, "get_plan_overview", json!({"weekStart":"2026-11-02"})).unwrap();
    assert_eq!(fresh["intensityLegacy"], false);
}

#[test]
fn out_of_range_schedule_date_is_rejected_before_anything_is_saved() {
    // P-133：assign_task_date 原先先落库再计算周视图——边界周日期越界时
    // 用户看到「排期失败」，实际排期已经保存，且按同日期重试永远得到
    // 同样错误
    let (app, learning) = setup();
    insert_plan(&learning.store().unwrap());
    let error = ipc(
        &app,
        "assign_task_date",
        json!({"taskId":2,"dateValue":"9999-12-31"}),
    )
    .unwrap_err();
    assert_eq!(error["code"], "invalid_schedule_date");
    let rows: i64 = learning
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM learning_plan_schedule_tasks WHERE plan_id = 1 AND task_id = 2",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(rows, 0, "越界日期不得留下已保存的排期行");
}
