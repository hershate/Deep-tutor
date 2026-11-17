use super::*;
use crate::backend::providers::test_support::{response, server};
use crate::backend::BackendState;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::Mutex;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Listener, Manager,
};

pub(crate) static TEST_LOCK: Mutex<()> = Mutex::new(());

pub(crate) struct ResetDefer;
impl Drop for ResetDefer {
    fn drop(&mut self) {
        defer(false);
    }
}

pub(crate) fn setup() -> (tauri::App<tauri::test::MockRuntime>, LearningState) {
    let learning = LearningState::for_tests().unwrap();
    let path = learning.database_path();
    let providers = learning.provider_state();
    let app = tauri::test::mock_builder()
        .manage(BackendState {
            store: Mutex::new(learning.store().unwrap()),
            materials: learning.material_state(),
            providers: providers.shared(),
            learning: learning.shared(),
            agent: crate::backend::agent::AgentState::new(path, &providers, &learning),
            practice: crate::backend::practice::PracticeState::new(path, &providers, &learning),
            review: crate::backend::review::ReviewState::new(path, &learning),
            knowledge: crate::backend::knowledge::KnowledgeState::new(
                path,
                &providers,
                &learning.material_state(),
            ),
            community: crate::backend::community::CommunityState::new(path),
            plugins: crate::backend::plugins::PluginState::new(path).unwrap(),
            sharing: crate::backend::sharing::SharingState::new(&learning),
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_sync_overview,
            commands::save_sync_config,
            commands::start_sync_job,
            commands::get_sync_job,
            commands::cancel_sync_job,
            commands::get_sync_versions,
            commands::restore_sync_version,
            crate::backend::commands::events_since
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    (app, learning)
}

pub(crate) fn ipc(
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

pub(crate) fn config(kind: &str, endpoint: &str) -> SyncConfigInput {
    SyncConfigInput {
        kind: kind.into(),
        endpoint: endpoint.into(),
        remote_path: "deep-tutor/provider-config.json".into(),
        region: if kind == "s3" {
            "test-region-1".into()
        } else {
            String::new()
        },
        bucket: if kind == "s3" {
            "test-bucket".into()
        } else {
            String::new()
        },
        auth_kind: if kind == "s3" {
            "aws4".into()
        } else {
            "none".into()
        },
        username: String::new(),
        allow_private: true,
        auto_sync: false,
        password: None,
        access_key: if kind == "s3" {
            Some("test-access".into())
        } else {
            None
        },
        secret_key: if kind == "s3" {
            Some("test-secret".into())
        } else {
            None
        },
    }
}

pub(crate) fn create_provider(
    app: &tauri::App<tauri::test::MockRuntime>,
    state: &LearningState,
    url: &str,
) {
    crate::backend::providers::upsert(
        app.handle(),
        &state.provider_state(),
        crate::backend::providers::ProfileInput {
            id: None,
            name: "真实本地模型".into(),
            preset: Some("custom".into()),
            kind: "本地模型".into(),
            protocol: "OpenAI Chat".into(),
            base_url: url.into(),
            default_model: "test-model".into(),
            models: vec!["test-model".into()],
            enabled: Some(true),
            note: None,
            api_key: None,
        },
    )
    .unwrap();
}

pub(crate) fn wait_job(service: &SyncService, id: &str) -> SyncJobView {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let job = job(service, id).unwrap();
        if ["done", "failed", "cancelled", "interrupted"].contains(&job.status.as_str()) {
            return job;
        }
        assert!(Instant::now() < deadline, "sync job did not finish: {id}");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn request_body(request: &str) -> &str {
    request.split("\r\n\r\n").nth(1).unwrap_or("")
}

#[test]
fn webdav_upload_download_import_and_terminal_events_are_real() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let _reset = ResetDefer;
    let uploaded = Arc::new(Mutex::new(String::new()));
    let remote = uploaded.clone();
    let server = server(move |request, stream| {
        let method = request.split(' ').next().unwrap_or_default();
        match method {
            "PUT" => {
                *remote.lock().unwrap() = request_body(request).to_string();
                response(stream, "201 Created", "");
            }
            "GET" => {
                let body = remote.lock().unwrap().clone();
                response(stream, "200 OK", &body);
            }
            "PROPFIND" => response(stream, "207 Multi-Status", ""),
            _ => response(stream, "200 OK", ""),
        }
    });
    let (app, learning) = setup();
    create_provider(&app, &learning, &server.url());
    let saved = ipc(
        &app,
        "save_sync_config",
        json!({"input": config("webdav", &server.url())}),
    )
    .unwrap();
    assert_eq!(saved["hasCredentials"], false);
    assert!(!saved.to_string().contains("password"));
    let committed = Arc::new(AtomicBool::new(false));
    let committed_event = committed.clone();
    let service = SyncService::new(&learning);
    let service_for_event = service.shared();
    let service_for_next = service.shared();
    let app_for_next = app.handle().clone();
    let download_id = Arc::new(Mutex::new(String::new()));
    let download_id_for_event = download_id.clone();
    let follow_up = Arc::new(Mutex::new(None));
    let follow_up_for_event = follow_up.clone();
    app.listen("backend://event", move |event| {
        let payload: Value = serde_json::from_str(event.payload()).unwrap();
        if payload["kind"] != "sync.completed" {
            return;
        }
        let id = payload["payload"]["jobId"].as_str().unwrap();
        if job(&service_for_event, id).unwrap().status == "done" {
            committed_event.store(true, Ordering::Release);
        }
        // 170#1：真实终态回调中立即接续下一任务，覆盖终态事件与 ACTIVE 释放竞态。
        if *download_id_for_event.lock().unwrap() == id {
            let next = start(&app_for_next, &service_for_next, "import".into(), None);
            *follow_up_for_event.lock().unwrap() = Some(next);
        }
    });
    let checked = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "check", "destination": null}),
    )
    .unwrap();
    assert_eq!(
        wait_job(&service, checked["id"].as_str().unwrap()).status,
        "done"
    );
    let uploaded_job = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "upload", "destination": null}),
    )
    .unwrap();
    let uploaded_job = wait_job(&service, uploaded_job["id"].as_str().unwrap());
    assert_eq!(uploaded_job.status, "done");
    assert_eq!(uploaded_job.result.unwrap()["remoteConfirmed"], true);
    assert!(uploaded.lock().unwrap().contains("真实本地模型"));
    assert!(!uploaded.lock().unwrap().contains("apiKey"));
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("download.json");
    let downloaded = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "download", "destination": destination.to_string_lossy()}),
    )
    .unwrap();
    *download_id.lock().unwrap() = downloaded["id"].as_str().unwrap().into();
    let downloaded = wait_job(&service, downloaded["id"].as_str().unwrap());
    assert_eq!(downloaded.status, "done");
    assert_eq!(downloaded.result.unwrap()["profiles"], 1);
    assert!(std::fs::read_to_string(&destination)
        .unwrap()
        .contains("真实本地模型"));
    // P-034：下载目的地须登记 reveal 回执——「在资源管理器中显示」只放行
    // 数据根目录与本进程近期真实写出的文件，下载落盘后未登记时按钮必然
    // 被白名单拒绝（提示与事实不符）
    assert!(
        crate::backend::reveal::is_known(&destination),
        "同步下载目的地未登记 reveal 回执"
    );
    let event_deadline = Instant::now() + Duration::from_secs(10);
    let imported = loop {
        if let Some(next) = follow_up.lock().unwrap().take() {
            break next.unwrap();
        }
        assert!(Instant::now() < event_deadline, "未收到下载终态回调");
        std::thread::sleep(Duration::from_millis(5));
    };
    assert_eq!(wait_job(&service, &imported.id).status, "done");
    assert!(committed.load(Ordering::Acquire));
    let events = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
    let kinds = events["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| event["kind"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(
        kinds.contains(&"sync.started")
            && kinds.contains(&"sync.progress")
            && kinds.contains(&"sync.completed")
    );
}

#[test]
fn s3_signs_real_requests_and_hmac_matches_the_standard_vector() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let _reset = ResetDefer;
    assert_eq!(
        transport::hmac(b"key", b"The quick brown fox jumps over the lazy dog")
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>(),
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    );
    let checks = Arc::new(Mutex::new(Vec::new()));
    let observed = checks.clone();
    let remote = Arc::new(Mutex::new(String::new()));
    let server = server(move |request, stream| {
        let method = request.split(' ').next().unwrap_or_default();
        let authorization = request
            .lines()
            .find(|line| line.to_ascii_lowercase().starts_with("authorization:"))
            .unwrap_or_default()
            .to_string();
        let body_hash = request
            .lines()
            .find(|line| {
                line.to_ascii_lowercase()
                    .starts_with("x-amz-content-sha256:")
            })
            .and_then(|line| line.split_once(": "))
            .map(|(_, value)| value.trim().to_string())
            .unwrap_or_default();
        let expected_hash = format!("{:x}", Sha256::digest(request_body(request).as_bytes()));
        observed.lock().unwrap().push(json!({"method": method, "path": request.split(' ').nth(1), "authorization": authorization, "hashMatches": body_hash == expected_hash}));
        match method {
            "PUT" => {
                *remote.lock().unwrap() = request_body(request).to_string();
                response(stream, "200 OK", "");
            }
            "GET" => {
                let body = remote.lock().unwrap().clone();
                response(stream, "200 OK", &body);
            }
            _ => response(stream, "200 OK", ""),
        }
    });
    let (app, learning) = setup();
    create_provider(&app, &learning, &server.url());
    ipc(
        &app,
        "save_sync_config",
        json!({"input": config("s3", &server.url())}),
    )
    .unwrap();
    let service = SyncService::new(&learning);
    let checked = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "check", "destination": null}),
    )
    .unwrap();
    assert_eq!(
        wait_job(&service, checked["id"].as_str().unwrap()).status,
        "done"
    );
    let uploaded = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "upload", "destination": null}),
    )
    .unwrap();
    assert_eq!(
        wait_job(&service, uploaded["id"].as_str().unwrap()).status,
        "done"
    );
    let checks = checks.lock().unwrap();
    assert_eq!(checks.len(), 7);
    assert!(checks
        .iter()
        .any(|check| check["path"] == "/test-bucket/deep-tutor/provider-config.json"));
    assert!(checks.iter().all(|check| check["hashMatches"] == true));
    assert!(checks
        .iter()
        .filter(|check| check["method"] == "PUT")
        .all(|check| {
            let auth = check["authorization"].as_str().unwrap();
            auth.contains("AWS4-HMAC-SHA256 Credential=test-access/")
                && auth.contains("/test-region-1/s3/aws4_request")
                && auth.contains("SignedHeaders=host;x-amz-content-sha256;x-amz-date")
                && auth.len() > 64
        }));
}

#[test]
fn invalid_remote_secrets_and_cancelled_jobs_never_claim_success() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let _reset = ResetDefer;
    let server = server(|_, stream| {
        response(
            stream,
            "200 OK",
            &json!({"apiKey": "not-a-real-secret-but-must-be-rejected"}).to_string(),
        )
    });
    let (app, learning) = setup();
    ipc(
        &app,
        "save_sync_config",
        json!({"input": config("webdav", &server.url())}),
    )
    .unwrap();
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("unsafe.json");
    let accepted = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "download", "destination": destination.to_string_lossy()}),
    )
    .unwrap();
    let service = SyncService::new(&learning);
    let failed = wait_job(&service, accepted["id"].as_str().unwrap());
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.error.unwrap().code, "sync_sensitive_payload");
    assert!(!destination.exists());
    defer(true);
    let accepted = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "check", "destination": null}),
    )
    .unwrap();
    let id = accepted["id"].as_str().unwrap().to_string();
    let cancelling = ipc(&app, "cancel_sync_job", json!({"id": id})).unwrap();
    assert_eq!(cancelling["status"], "cancelling");
    defer(false);
    assert_eq!(wait_job(&service, &id).status, "cancelled");
    assert_eq!(server.request_count(), 1);
}

#[test]
fn remote_hash_mismatch_is_a_failed_sync_not_a_success() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let _reset = ResetDefer;
    let server = server(|request, stream| {
        if request.starts_with("PUT ") {
            response(stream, "201 Created", "");
        } else {
            response(stream, "200 OK", "{}");
        }
    });
    let (app, learning) = setup();
    create_provider(&app, &learning, &server.url());
    ipc(
        &app,
        "save_sync_config",
        json!({"input": config("webdav", &server.url())}),
    )
    .unwrap();
    let accepted = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "upload", "destination": null}),
    )
    .unwrap();
    let service = SyncService::new(&learning);
    let failed = wait_job(&service, accepted["id"].as_str().unwrap());
    assert_eq!(failed.status, "failed");
    assert_eq!(failed.error.unwrap().code, "sync_verify_failed");
}

#[test]
fn stopping_another_database_does_not_cancel_the_current_job() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let _reset = ResetDefer;
    let server = server(|_, stream| response(stream, "207 Multi-Status", ""));
    let (app, learning) = setup();
    ipc(
        &app,
        "save_sync_config",
        json!({"input": config("webdav", &server.url())}),
    )
    .unwrap();
    defer(true);
    let accepted = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "check", "destination": null}),
    )
    .unwrap();
    let other = LearningState::for_tests().unwrap();
    stop(&SyncService::new(&other)).unwrap();
    defer(false);
    let service = SyncService::new(&learning);
    assert_eq!(
        wait_job(&service, accepted["id"].as_str().unwrap()).status,
        "done"
    );
}

#[test]
fn upload_anchor_uses_rowid_order_not_text_id_lexicographic_order() {
    // 94#1 回归：sync_jobs.id 为 TEXT 主键，字典序与时间序无关
    // （`"sync-9999-1" > "sync-1000-5"`、pid 变小等场景乱序）。锚点必须
    // 按 rowid（真实插入顺序）取最新一行，否则 auto_sync 每 60s 重复
    // 上传，retention 窗口被风暴版本挤满后真实历史不可逆销毁
    let learning = LearningState::for_tests().unwrap();
    let store = learning.store().unwrap();
    let insert = |id: &str, finished_at: &str| {
        store
            .conn
            .execute(
                "INSERT INTO sync_jobs (id, kind, status, config_json, remote_path, created_at, updated_at, finished_at)
                 VALUES (?1, 'upload', 'done', '{}', 'p', ?2, ?2, ?2)",
                params![id, finished_at],
            )
            .unwrap();
    };
    // 先插字典序最大但时间最旧的行，再插字典序较小、时间更新的行
    insert("sync-9999-1", "2026-01-01T00:00:00Z");
    insert("sync-1000-5", "2026-06-01T00:00:00Z");
    let (anchor, status) = scheduler::upload_anchor(&store.conn).unwrap().unwrap();
    assert_eq!(anchor.as_deref(), Some("2026-06-01T00:00:00Z"));
    assert_eq!(status.as_deref(), Some("done"));
    // download/kind 行不参与 upload 锚点
    store
        .conn
        .execute(
            "INSERT INTO sync_jobs (id, kind, status, config_json, remote_path, created_at, updated_at, finished_at)
             VALUES ('sync-1000-6', 'download', 'done', '{}', 'p', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')",
            [],
        )
        .unwrap();
    let (anchor, _) = scheduler::upload_anchor(&store.conn).unwrap().unwrap();
    assert_eq!(anchor.as_deref(), Some("2026-06-01T00:00:00Z"));
    // 124#4：最新 upload 行 finished_at 为 NULL（进行中）——按 Option 读出
    // 不再抛 InvalidColumnType，调度层按「本轮跳过」处理
    store
        .conn
        .execute(
            "INSERT INTO sync_jobs (id, kind, status, config_json, remote_path, created_at, updated_at, finished_at)
             VALUES ('sync-1000-7', 'upload', 'running', '{}', 'p', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', NULL)",
            [],
        )
        .unwrap();
    let (anchor, status) = scheduler::upload_anchor(&store.conn).unwrap().unwrap();
    assert_eq!(anchor, None);
    assert_eq!(status.as_deref(), Some("running"));
}

#[test]
fn due_treats_future_anchor_as_due_to_self_heal_clock_rollback() {
    // 94#4 回归：时钟回拨使 finished_at 落在未来时，照常比较会让
    // 「anchor+24h > now」在真实时间追上前恒成立，自动同步停摆数月；
    // 按到期处理立即同步一次，新锚点以当前时钟重写后调度自愈
    let now = OffsetDateTime::parse("2026-06-01T12:00:00Z", &Rfc3339).unwrap();
    let rollback_anchor = OffsetDateTime::parse("2026-08-01T00:00:00Z", &Rfc3339).unwrap();
    assert!(scheduler::due(rollback_anchor, false, now));
    // 常规未到期 / 到期 / 失败 1 小时退避口径不变
    let anchor = OffsetDateTime::parse("2026-06-01T00:00:00Z", &Rfc3339).unwrap();
    assert!(!scheduler::due(anchor, false, now));
    assert!(scheduler::due(anchor, true, now));
    let done_anchor = OffsetDateTime::parse("2026-05-31T11:00:00Z", &Rfc3339).unwrap();
    assert!(scheduler::due(done_anchor, false, now));
}

#[test]
fn next_due_at_follows_the_scheduler_anchor_not_the_recent_job_window() {
    // P-150：面板的「下次自动同步」必须与调度器同源——调度器锚点是全表
    // 最新 upload 行（rowid 倒序），而 overview 的 job 列表只含最近 50 行，
    // 前端在窗口内找不到 upload 行时无从推导真实锚点。此处固化后端口径：
    // 表内无 upload 行时退回配置的 updated_at（24 小时）；最新 upload 失败
    // 时按 1 小时短退避；最新 upload 仍在进行中（finished_at 为 NULL）时
    // 无到期时刻
    let learning = LearningState::for_tests().unwrap();
    let store = learning.store().unwrap();
    let config_updated_at = "2026-06-01T00:00:00Z";
    // 表内没有任何 upload 行——锚点退回配置的 updated_at
    assert_eq!(
        scheduler::next_due_at(&store.conn, config_updated_at)
            .unwrap()
            .as_deref(),
        Some("2026-06-02T00:00:00Z")
    );
    let insert = |id: &str, kind: &str, status: &str, finished_at: Option<&str>| {
        store
            .conn
            .execute(
                "INSERT INTO sync_jobs (id, kind, status, config_json, remote_path, created_at, updated_at, finished_at)
                 VALUES (?1, ?2, ?3, '{}', 'p', ?4, ?4, ?5)",
                params![id, kind, status, finished_at.unwrap_or("2026-06-05T00:00:00Z"), finished_at],
            )
            .unwrap();
    };
    // 最新 upload 成功——24 小时后到期
    insert("sync-1", "upload", "done", Some("2026-06-10T00:00:00Z"));
    assert_eq!(
        scheduler::next_due_at(&store.conn, config_updated_at)
            .unwrap()
            .as_deref(),
        Some("2026-06-11T00:00:00Z")
    );
    // 最新 upload 失败——1 小时短退避
    insert("sync-2", "upload", "failed", Some("2026-06-20T00:00:00Z"));
    assert_eq!(
        scheduler::next_due_at(&store.conn, config_updated_at)
            .unwrap()
            .as_deref(),
        Some("2026-06-20T01:00:00Z")
    );
    // 锚点不受 job 列表窗口限制：其后插入 60 条非 upload 任务（超过
    // overview 的 50 行窗口），锚点仍是最新 upload 行而非配置 updated_at
    for index in 0..60 {
        insert(
            &format!("sync-check-{index}"),
            "check",
            "done",
            Some("2026-06-21T00:00:00Z"),
        );
    }
    assert_eq!(
        scheduler::next_due_at(&store.conn, config_updated_at)
            .unwrap()
            .as_deref(),
        Some("2026-06-20T01:00:00Z")
    );
    // 最新 upload 仍在进行中（finished_at 为 NULL）——无到期时刻
    insert("sync-3", "upload", "running", None);
    assert_eq!(
        scheduler::next_due_at(&store.conn, config_updated_at).unwrap(),
        None
    );
}

#[test]
fn overview_reports_the_next_auto_sync_time_with_the_scheduler_anchor() {
    // P-150：overview 的 nextAutoSyncAt 与调度器同源——开启自动同步时按
    // 配置 updated_at + 24 小时给出；关闭自动同步时无到期时刻
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let server = server(|_, stream| response(stream, "200 OK", ""));
    let (app, learning) = setup();
    let service = SyncService::new(&learning);
    let mut input = config("webdav", &server.url());
    input.auto_sync = true;
    save_config(app.handle(), &service, input).unwrap();
    let view = overview(&service).unwrap();
    // 锚点取 overview 自己回传的 config.updated_at（sync_config 行的
    // updated_at）——save_config 返回的 config.updated_at 是 validate_input
    // 内取的另一时刻，两者相差毫秒级，不能混用
    let anchor =
        OffsetDateTime::parse(&view.config.as_ref().unwrap().config.updated_at, &Rfc3339).unwrap();
    let expected = (anchor + time::Duration::hours(24))
        .format(&Rfc3339)
        .unwrap();
    assert_eq!(view.next_auto_sync_at.as_deref(), Some(expected.as_str()));
    // 自动同步关闭——面板不显示到期时刻
    let mut input = config("webdav", &server.url());
    input.auto_sync = false;
    save_config(app.handle(), &service, input).unwrap();
    assert_eq!(overview(&service).unwrap().next_auto_sync_at, None);
}

#[test]
fn progress_and_completion_preserve_a_cancellation_request() {
    let _lock = TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
    let gate = Arc::new(AtomicBool::new(false));
    let server = {
        let gate = Arc::clone(&gate);
        server(move |_, stream| {
            let deadline = Instant::now() + Duration::from_secs(5);
            while !gate.load(Ordering::Acquire) {
                assert!(Instant::now() < deadline);
                std::thread::sleep(Duration::from_millis(5));
            }
            response(stream, "404 Not Found", "missing");
        })
    };
    let (app, learning) = setup();
    save_config(
        app.handle(),
        &SyncService::new(&learning),
        config("webdav", &server.url()),
    )
    .unwrap();
    let service = SyncService::new(&learning);
    let started = start(app.handle(), &service, "check".into(), None).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while server.request_count() == 0 {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    cancel(app.handle(), &service, &started.id).unwrap();
    stage(app.handle(), &service, &started.id, "running", "verify").unwrap();
    let after_progress = job(&service, &started.id).unwrap().status;
    gate.store(true, Ordering::Release);
    assert_eq!(after_progress, "cancelling");
    assert_eq!(wait_job(&service, &started.id).status, "cancelled");
}

// P-159：保存同步配置的路径含 endpoint() 的阻塞 DNS 解析（to_socket_addrs
// 在弱网或域名不可达时可阻塞数秒），原先是同步命令——按 Tauri 2 语义命令体
// 在主线程执行，点击保存后界面冻结至解析返回或超时。回归守卫盯住命令声明
// 本身：改回同步命令即视为回归（运行时无法从返回值观察执行线程）
#[test]
fn save_sync_config_command_is_declared_async() {
    let source = include_str!("commands.rs");
    let declaration = source
        .split("#[tauri::command")
        .find(|chunk| chunk.contains("pub async fn save_sync_config"))
        .expect("未找到 save_sync_config 的命令声明");
    assert!(
        declaration.starts_with("(async)]"),
        "save_sync_config 必须标注 #[tauri::command(async)]，否则按 Tauri 2 语义在主线程同步执行（阻塞 DNS 解析会冻结界面），实际：{declaration}"
    );
}
