use super::*;
use crate::backend::BackendState;
use std::sync::Mutex;

fn setup() -> (tauri::App<tauri::test::MockRuntime>, CommunityState) {
    let state = CommunityState::for_tests().unwrap();
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
            community: CommunityState::new(&path),
            plugins: crate::backend::plugins::PluginState::new(&path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            super::commands::get_community_overview,
            super::commands::create_community_post,
            super::commands::reply_community_post,
            super::commands::accept_community_reply,
            super::commands::toggle_community_reaction,
            super::commands::create_community_group,
            super::commands::toggle_community_group,
            super::commands::create_community_room,
            super::commands::reserve_community_room,
            super::commands::cancel_community_reservation,
            super::commands::cancel_community_room,
            super::commands::claim_community_reminders,
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

#[test]
fn posts_projects_replies_reactions_and_stats_are_real_and_persistent() {
    let (app, state) = setup();
    state.store().unwrap().replace_collection("projects", 0, &json!([{
        "id": 1, "title": "真实分类器项目", "description": "检查真实评估指标", "due": "2099-01-01",
        "progress": 25, "tag": "项目", "milestones": [{"id": 1, "title": "评估", "done": false}],
    }])).unwrap();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let discussion = ipc(&window, "create_community_post", json!({"input": {
        "title": "如何复习概率", "body": "分享主动回忆的安排", "kind": "discussion", "projectId": null,
    }})).unwrap();
    let discussion_id = discussion["id"].as_i64().unwrap();
    let review = create_post(
        app.handle(),
        &state,
        PostInput {
            title: "项目指标互评".into(),
            body: "请检查精确率和召回率的解释".into(),
            kind: "peer_review".into(),
            project_id: Some(1),
        },
    )
    .unwrap();
    assert_eq!(review.project_title.as_deref(), Some("真实分类器项目"));
    assert_eq!(review.project_id, Some(1));
    assert_eq!(
        create_post(
            app.handle(),
            &state,
            PostInput {
                title: "无效互评".into(),
                body: "没有真实项目".into(),
                kind: "peer_review".into(),
                project_id: Some(999),
            }
        )
        .unwrap_err()
        .code,
        "project_not_found"
    );
    let first = reply(
        app.handle(),
        &state,
        discussion_id,
        "可以比较每天与每周的复习安排".into(),
    )
    .unwrap();
    let second = reply(
        app.handle(),
        &state,
        discussion_id,
        "还可以结合真实错题复习".into(),
    )
    .unwrap();
    assert!(!first.accepted);
    accept_reply(app.handle(), &state, first.id, true).unwrap();
    accept_reply(app.handle(), &state, second.id, true).unwrap();
    assert!(
        !reply_row(&state.store().unwrap().conn, first.id)
            .unwrap()
            .accepted
    );
    assert!(
        reply_row(&state.store().unwrap().conn, second.id)
            .unwrap()
            .accepted
    );
    assert_eq!(
        toggle_reaction(app.handle(), &state, discussion_id, "like".into())
            .unwrap()
            .likes,
        1
    );
    assert!(
        toggle_reaction(app.handle(), &state, discussion_id, "bookmark".into())
            .unwrap()
            .bookmarked
    );
    assert!(
        !toggle_reaction(app.handle(), &state, discussion_id, "like".into())
            .unwrap()
            .liked
    );
    let filtered = overview(&state, Some("peer_review"), Some("指标")).unwrap();
    assert_eq!(filtered.posts.len(), 1);
    assert_eq!(filtered.posts[0].id, review.id);
    assert!(overview(&state, None, Some("不存在的内容"))
        .unwrap()
        .posts
        .is_empty());
    let stats = overview(&state, None, None).unwrap().stats;
    assert_eq!(stats.posts, 2);
    assert_eq!(stats.replies, 2);
    assert_eq!(stats.reviews, 1);
    assert_eq!(stats.accepted_replies, 1);
    assert_eq!(stats.likes, 0);
    assert_eq!(stats.bookmarks, 1);
    let persisted = CommunityState::new(&state.database_path);
    assert_eq!(
        overview(&persisted, None, None)
            .unwrap()
            .posts
            .iter()
            .find(|post| post.id == discussion_id)
            .unwrap()
            .replies
            .len(),
        2
    );
    assert_eq!(
        post_row(&persisted.store().unwrap().conn, discussion_id)
            .unwrap()
            .author,
        "本机用户"
    );
}

#[test]
fn groups_rooms_reservations_reminders_and_reset_use_real_state() {
    let (app, state) = setup();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let group = ipc(
        &window,
        "create_community_group",
        json!({"input": {
            "name": "概率共学", "topic": "交流真实错题", "schedule": "每周三 20:00",
        }}),
    )
    .unwrap();
    let group_id = group["id"].as_i64().unwrap();
    assert_eq!(group["memberCount"], 0);
    assert_eq!(group["joined"], false);
    let start = OffsetDateTime::now_utc() + Duration::hours(2);
    let start_at = start.format(&Rfc3339).unwrap();
    let room = create_room(
        app.handle(),
        &state,
        RoomInput {
            group_id: Some(group_id),
            title: "错题互讲".into(),
            topic: "每人带一道真实错题".into(),
            start_at: start_at.clone(),
            duration_minutes: 45,
        },
    )
    .unwrap();
    assert_eq!(
        reserve(app.handle(), &state, room.id).unwrap_err().code,
        "group_not_joined"
    );
    assert!(
        toggle_group(app.handle(), &state, group_id, true)
            .unwrap()
            .joined
    );
    assert_eq!(
        group_row(&state.store().unwrap().conn, group_id)
            .unwrap()
            .member_count,
        1
    );
    let reserved = ipc(&window, "reserve_community_room", json!({"id": room.id})).unwrap();
    assert_eq!(reserved["reserved"], true);
    assert_eq!(reserved["reservationCount"], 1);
    let reminder_at = reserved["reminderAt"].as_str().unwrap();
    assert_eq!(
        (parse_time(reminder_at).unwrap() - start).whole_minutes(),
        -15
    );
    assert!(claim_due_reminders(app.handle(), &state)
        .unwrap()
        .is_empty());
    state
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE community_reminders SET remind_at = '2000-01-01T00:00:00Z' WHERE room_id = ?1",
            params![room.id],
        )
        .unwrap();
    let due = ipc(&window, "claim_community_reminders", json!({})).unwrap();
    assert_eq!(due.as_array().unwrap().len(), 1);
    assert_eq!(due[0]["title"], "错题互讲");
    assert!(due[0]["deliveredAt"].is_string());
    assert!(claim_due_reminders(app.handle(), &state)
        .unwrap()
        .is_empty());
    assert!(
        !cancel_reservation(app.handle(), &state, room.id)
            .unwrap()
            .reserved
    );
    assert_eq!(
        room_row(&state.store().unwrap().conn, room.id)
            .unwrap()
            .reminder_at,
        None
    );
    assert_eq!(
        cancel_room(app.handle(), &state, room.id).unwrap().status,
        "cancelled"
    );
    assert_eq!(
        reserve(app.handle(), &state, room.id).unwrap_err().code,
        "room_not_available"
    );
    assert!(
        !toggle_group(app.handle(), &state, group_id, false)
            .unwrap()
            .joined
    );
    assert_eq!(
        create_room(
            app.handle(),
            &state,
            RoomInput {
                group_id: None,
                title: "过期房间".into(),
                topic: "不应接受".into(),
                start_at: "2000-01-01T00:00:00Z".into(),
                duration_minutes: 45,
            }
        )
        .unwrap_err()
        .code,
        "invalid_room_time"
    );
    // 03#13-12：同一时段重叠的房间被拒绝，错开时段可创建
    let fresh_start = (OffsetDateTime::now_utc() + Duration::hours(3))
        .format(&Rfc3339)
        .unwrap();
    create_room(
        app.handle(),
        &state,
        RoomInput {
            group_id: None,
            title: "时段占用".into(),
            topic: "占住该时段".into(),
            start_at: fresh_start.clone(),
            duration_minutes: 45,
        },
    )
    .unwrap();
    assert_eq!(
        create_room(
            app.handle(),
            &state,
            RoomInput {
                group_id: None,
                title: "重叠房间".into(),
                topic: "不应接受".into(),
                start_at: fresh_start.clone(),
                duration_minutes: 10,
            }
        )
        .unwrap_err()
        .code,
        "room_time_conflict"
    );
    let later = (OffsetDateTime::now_utc() + Duration::hours(4))
        .format(&Rfc3339)
        .unwrap();
    assert!(create_room(
        app.handle(),
        &state,
        RoomInput {
            group_id: None,
            title: "错开时段".into(),
            topic: "应接受".into(),
            start_at: later,
            duration_minutes: 30,
        },
    )
    .is_ok());
    // 预约同样受冲突约束：直接落库一条与“时段占用”重叠的旧排期再预约
    let store = state.store().unwrap();
    store
        .conn
        .execute(
            "INSERT INTO community_rooms (group_id, title, topic, start_at, duration_minutes, status, created_at) VALUES (NULL, '旧排期', '绕过校验的历史数据', ?1, 30, 'scheduled', ?2)",
            params![
                fresh_start,
                OffsetDateTime::now_utc().format(&Rfc3339).unwrap()
            ],
        )
        .unwrap();
    let stale_id = store.conn.last_insert_rowid();
    drop(store);
    assert_eq!(
        reserve(app.handle(), &state, stale_id).unwrap_err().code,
        "room_time_conflict"
    );
    assert_eq!(
        overview(&state, None, None)
            .unwrap()
            .stats
            .active_reservations,
        0
    );
    state.store().unwrap().reset().unwrap();
    let cleared = overview(&state, None, None).unwrap();
    assert!(cleared.posts.is_empty() && cleared.groups.is_empty() && cleared.rooms.is_empty());
    assert_eq!(cleared.stats.joined_groups, 0);
}

#[test]
fn community_overview_reads_real_rows_and_filters_through_ipc() {
    let (app, state) = setup();
    state.store().unwrap().replace_collection("projects", 0, &json!([{
        "id": 1, "title": "真实分类器项目", "description": "检查真实评估指标", "due": "2099-01-01",
        "progress": 25, "tag": "项目", "milestones": [{"id": 1, "title": "评估", "done": false}],
    }])).unwrap();
    let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    ipc(
        &window,
        "create_community_post",
        json!({"input": {
            "title": "如何复习概率", "body": "分享主动回忆的安排", "kind": "discussion", "projectId": null,
        }}),
    )
    .unwrap();
    let overview = ipc(&window, "get_community_overview", json!({})).unwrap();
    assert_eq!(overview["stats"]["posts"], 1);
    assert_eq!(overview["posts"].as_array().unwrap().len(), 1);
    // kind / query 过滤必须落到真实 SQL 与行级匹配，而不是前端兜底
    let filtered = ipc(
        &window,
        "get_community_overview",
        json!({"kind": "peer_review"}),
    )
    .unwrap();
    assert!(filtered["posts"].as_array().unwrap().is_empty());
    let searched = ipc(&window, "get_community_overview", json!({"query": "概率"})).unwrap();
    assert_eq!(searched["posts"].as_array().unwrap().len(), 1);
    let missing = ipc(
        &window,
        "get_community_overview",
        json!({"query": "不存在的关键词"}),
    )
    .unwrap();
    assert!(missing["posts"].as_array().unwrap().is_empty());
    let invalid = ipc(
        &window,
        "get_community_overview",
        json!({"kind": "unknown"}),
    )
    .unwrap_err();
    assert_eq!(invalid["code"], "invalid_post_kind");
}
