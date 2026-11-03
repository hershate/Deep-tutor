use super::providers::test_support::{response, server};
use super::tests::{config, create_provider, ipc, setup, wait_job};
use super::{scheduler, SyncService};
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

fn request_path(request: &str) -> &str {
    request.split(' ').nth(1).unwrap_or_default()
}
fn body(request: &str) -> &str {
    request.split("\r\n\r\n").nth(1).unwrap_or_default()
}

fn webdav_remote() -> (
    super::super::providers::test_support::TestServer,
    Arc<Mutex<BTreeMap<String, String>>>,
) {
    let objects = Arc::new(Mutex::new(BTreeMap::<String, String>::new()));
    let remote = objects.clone();
    let server = server(move |request, stream| {
        let method = request.split(' ').next().unwrap_or_default();
        let path = request_path(request).trim_start_matches('/').to_string();
        match method {
            "PUT" => {
                remote
                    .lock()
                    .unwrap()
                    .insert(path, body(request).to_string());
                response(stream, "201 Created", "");
            }
            "GET" => match remote.lock().unwrap().get(&path).cloned() {
                Some(content) => response(stream, "200 OK", &content),
                None => response(stream, "404 Not Found", ""),
            },
            "DELETE" => {
                remote.lock().unwrap().remove(&path);
                response(stream, "204 No Content", "");
            }
            "PROPFIND" => {
                let list = remote.lock().unwrap().iter().map(|(path, content)| format!("<D:response><D:href>/{path}</D:href><D:getcontentlength>{}</D:getcontentlength><D:getlastmodified>2026-11-03T00:00:00Z</D:getlastmodified></D:response>", content.len())).collect::<String>();
                response(
                    stream,
                    "207 Multi-Status",
                    &format!("<D:multistatus xmlns:D=\"DAV:\">{list}</D:multistatus>"),
                );
            }
            _ => response(stream, "405 Method Not Allowed", ""),
        }
    });
    (server, objects)
}

#[test]
fn scheduled_upload_retains_ten_real_versions_and_restores_one() {
    let _lock = super::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let _reset = super::tests::ResetDefer;
    let (server, objects) = webdav_remote();
    let (app, learning) = setup();
    create_provider(&app, &learning, &server.url());
    let mut input = config("webdav", &server.url());
    input.auto_sync = true;
    ipc(&app, "save_sync_config", json!({"input": input})).unwrap();
    let service = SyncService::new(&learning);
    assert!(scheduler::run_due(app.handle(), &service)
        .unwrap()
        .is_none());
    assert!(objects.lock().unwrap().is_empty());
    learning
        .store()
        .unwrap()
        .conn
        .execute(
            "UPDATE sync_config SET updated_at = '2000-01-01T00:00:00Z'",
            [],
        )
        .unwrap();
    let saved_config = learning
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT config_json FROM sync_config WHERE id = 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .unwrap();
    assert!(saved_config.contains("\"autoSync\":true"), "{saved_config}");
    let existing_uploads = learning
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT COUNT(*) FROM sync_jobs WHERE kind = 'upload'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap();
    assert_eq!(existing_uploads, 0);
    let mut first_history = String::new();
    let mut latest_history = String::new();
    for index in 0..11 {
        let accepted = scheduler::run_due(app.handle(), &service)
            .unwrap()
            .expect(&format!(
                "scheduled upload must be due at iteration {index}"
            ));
        let id = accepted.id.clone();
        let completed = wait_job(&service, &id);
        assert_eq!(completed.status, "done", "{:?}", completed.error);
        let history = completed.result.as_ref().unwrap()["historyPath"]
            .as_str()
            .unwrap()
            .to_string();
        if index == 0 {
            first_history = history.clone();
        }
        latest_history = history;
        learning
            .store()
            .unwrap()
            .conn
            .execute(
                "UPDATE sync_jobs SET finished_at = '2000-01-01T00:00:00Z' WHERE id = ?1",
                rusqlite::params![id],
            )
            .unwrap();
    }
    let versions = ipc(&app, "get_sync_versions", json!({})).unwrap();
    let versions = versions.as_array().unwrap();
    assert_eq!(versions.len(), 10);
    assert!(!objects.lock().unwrap().contains_key(&first_history));
    assert!(objects.lock().unwrap().contains_key(&latest_history));
    let remote_objects = objects.lock().unwrap();
    assert_eq!(
        remote_objects["deep-tutor/provider-config.json"],
        remote_objects[&latest_history]
    );
    drop(remote_objects);
    let path = versions[0]["path"].as_str().unwrap();
    let restored = ipc(
        &app,
        "restore_sync_version",
        json!({"versionPath": path, "destination": null}),
    )
    .unwrap();
    assert_eq!(
        wait_job(&service, restored["id"].as_str().unwrap()).status,
        "done"
    );
    assert_eq!(
        ipc(
            &app,
            "restore_sync_version",
            json!({"versionPath": "../outside.json", "destination": null})
        )
        .unwrap_err()["code"],
        "invalid_sync_version"
    );
}

#[test]
fn s3_version_listing_uses_signed_list_objects_v2_and_real_keys() {
    let _lock = super::tests::TEST_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let _reset = super::tests::ResetDefer;
    let objects = Arc::new(Mutex::new(BTreeMap::<String, String>::new()));
    let remote = objects.clone();
    let queries = Arc::new(Mutex::new(Vec::new()));
    let seen = queries.clone();
    let server = server(move |request, stream| {
        let method = request.split(' ').next().unwrap_or_default();
        let path = request_path(request).to_string();
        if method == "GET" && path.starts_with("/test-bucket?") {
            seen.lock().unwrap().push(path.clone());
            let contents = remote.lock().unwrap().iter().filter(|(path, _)| path.contains("/versions/")).map(|(path, content)| format!("<Contents><Key>{path}</Key><Size>{}</Size><ETag>\"etag-value\"</ETag><LastModified>2026-11-03T00:00:00Z</LastModified></Contents>", content.len())).collect::<String>();
            response(stream, "200 OK", &format!("<ListBucketResult>{contents}<IsTruncated>false</IsTruncated></ListBucketResult>"));
            return;
        }
        match method {
            "PUT" => {
                remote
                    .lock()
                    .unwrap()
                    .insert(path, body(request).to_string());
                response(stream, "201 Created", "");
            }
            "GET" => match remote.lock().unwrap().get(&path).cloned() {
                Some(content) => response(stream, "200 OK", &content),
                None => response(stream, "404 Not Found", ""),
            },
            "DELETE" => {
                remote.lock().unwrap().remove(&path);
                response(stream, "204 No Content", "");
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
    let accepted = ipc(
        &app,
        "start_sync_job",
        json!({"kind": "upload", "destination": null}),
    )
    .unwrap();
    let service = SyncService::new(&learning);
    assert_eq!(
        wait_job(&service, accepted["id"].as_str().unwrap()).status,
        "done"
    );
    let versions = ipc(&app, "get_sync_versions", json!({})).unwrap();
    assert_eq!(versions.as_array().unwrap().len(), 1);
    assert_eq!(versions[0]["etag"], "etag-value");
    assert!(versions[0]["size"].as_u64().unwrap() > 0);
    let queries = queries.lock().unwrap();
    assert_eq!(queries.len(), 3);
    assert!(queries.iter().all(|query| {
        let url = reqwest::Url::parse(&format!("http://localhost{query}")).unwrap();
        let parameters = url
            .query_pairs()
            .collect::<std::collections::HashMap<_, _>>();
        url.path() == "/test-bucket"
            && parameters.get("list-type").map(|value| value.as_ref()) == Some("2")
            && parameters.get("prefix").map(|value| value.as_ref())
                == Some("deep-tutor/provider-config/versions/")
    }));
}
