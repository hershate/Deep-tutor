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
    let _lock = TEST_LOCK.lock().unwrap();
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
    app.listen("backend://event", move |event| {
        let payload: Value = serde_json::from_str(event.payload()).unwrap();
        if payload["kind"] != "sync.completed" {
            return;
        }
        let id = payload["payload"]["jobId"].as_str().unwrap();
        if job(&service_for_event, id).unwrap().status == "done" {
            committed_event.store(true, Ordering::Release);
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
    let downloaded = wait_job(&service, downloaded["id"].as_str().unwrap());
    assert_eq!(downloaded.status, "done");
    assert_eq!(downloaded.result.unwrap()["profiles"], 1);
    assert!(std::fs::read_to_string(destination)
        .unwrap()
        .contains("真实本地模型"));
    let imported = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "import", "destination": null}),
    )
    .unwrap();
    assert_eq!(
        wait_job(&service, imported["id"].as_str().unwrap()).status,
        "done"
    );
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
    let _lock = TEST_LOCK.lock().unwrap();
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
    let _lock = TEST_LOCK.lock().unwrap();
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
    let _lock = TEST_LOCK.lock().unwrap();
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
