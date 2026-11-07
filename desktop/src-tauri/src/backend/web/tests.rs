use super::*;
use crate::backend::providers::test_support::{server, TestServer};
use std::io::Write;
use std::net::TcpStream;
use tauri::Manager;

fn ipc_app() -> (
    tauri::App<tauri::test::MockRuntime>,
    crate::backend::learning::LearningState,
) {
    let learning = crate::backend::learning::LearningState::for_tests().unwrap();
    let path = learning.database_path().to_path_buf();
    let providers = learning.provider_state();
    let app = tauri::test::mock_builder()
        .manage(crate::backend::BackendState {
            store: std::sync::Mutex::new(learning.store().unwrap()),
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
        .manage(crate::backend::security::SecurityState::for_tests_at(&path))
        .invoke_handler(tauri::generate_handler![
            commands::fetch_webpage,
            commands::import_webpage,
            crate::backend::materials::commands::enqueue_material_parse,
            crate::backend::materials::commands::get_material,
            crate::backend::commands::get_app_state,
            crate::backend::commands::events_since,
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

fn respond(stream: &mut TcpStream, status: &str, content_type: &str, body: &str, extra: &str) {
    let response = format!("HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}", body.len());
    stream.write_all(response.as_bytes()).unwrap();
}

fn web_server() -> TestServer {
    server(|request, stream| {
        if request.starts_with("GET /search") {
            respond(stream, "200 OK", "text/html; charset=utf-8", "<html><a rel=\"nofollow\" class=\"result__a\" href=\"/article\">候选标题</a><span class=\"result__snippet\">真实 &amp; 摘要</span></html>", "");
        } else if request.starts_with("GET /article") || request.starts_with("GET /final") {
            respond(stream, "200 OK", "text/html", "<html><head><title>实际文章标题</title><style>.hidden{}</style></head><body><script>throw new Error()</script><main>真实正文 &amp; 证据</main></body></html>", "");
        } else if request.starts_with("GET /redirect") {
            respond(
                stream,
                "302 Found",
                "text/plain",
                "",
                "Location: /final\r\n",
            );
        } else if request.starts_with("GET /private-redirect") {
            respond(
                stream,
                "302 Found",
                "text/plain",
                "",
                "Location: https://user:password@example.com/private\r\n",
            );
        } else if request.starts_with("GET /binary") {
            respond(stream, "200 OK", "image/png", "not-an-image", "");
        } else if request.starts_with("GET /empty-search") {
            respond(
                stream,
                "200 OK",
                "text/html",
                "<html><p>没有结果</p></html>",
                "",
            );
        } else {
            respond(stream, "404 Not Found", "text/plain", "missing", "");
        }
    })
}

#[test]
fn search_returns_only_actually_fetched_results_and_fetch_extracts_real_text() {
    let server = web_server();
    let _search_lock = crate::backend::web::SEARCH_ENDPOINT_LOCK.lock().unwrap();
    let _guard = set_search_endpoint_for_tests(Some(format!("{}/search", server.url())));
    let result = search("真实检索", 3).unwrap();
    assert_eq!(result["provider"], "DuckDuckGo HTML");
    assert_eq!(result["results"].as_array().unwrap().len(), 1);
    assert_eq!(
        result["results"][0]["url"],
        format!("{}/article", server.url())
    );
    assert_eq!(result["results"][0]["title"], "实际文章标题");
    assert_eq!(result["results"][0]["snippet"], "真实 & 摘要");
    assert_eq!(result["results"][0]["verified"], true);
    assert_eq!(server.request_count(), 2);
    let page = fetch(&format!("{}/redirect", server.url())).unwrap();
    assert_eq!(page.url, format!("{}/final", server.url()));
    assert_eq!(page.title, "实际文章标题");
    assert!(page.text.contains("真实正文 & 证据"));
    assert!(!page.text.contains("throw"));
    assert!(!page.text.contains("hidden"));
    assert_eq!(page.sha256, format!("{:x}", Sha256::digest("<html><head><title>实际文章标题</title><style>.hidden{}</style></head><body><script>throw new Error()</script><main>真实正文 &amp; 证据</main></body></html>".as_bytes())));
}

#[test]
fn production_url_policy_rejects_private_targets_credentials_and_unsupported_formats() {
    assert_eq!(
        resolve(Url::parse("http://127.0.0.1/").unwrap(), false)
            .unwrap_err()
            .code,
        "ssrf_blocked"
    );
    assert_eq!(
        resolve(Url::parse("http://192.168.1.2/").unwrap(), false)
            .unwrap_err()
            .code,
        "ssrf_blocked"
    );
    assert_eq!(
        fetch("file:///tmp/private").unwrap_err().code,
        "invalid_web_url"
    );
    assert_eq!(
        fetch("https://user:password@example.com/")
            .unwrap_err()
            .code,
        "invalid_web_url"
    );
    assert_eq!(
        fetch("https://example.com/?token=not-a-real-secret")
            .unwrap_err()
            .code,
        "web_credentials_blocked"
    );
    assert_eq!(search("", 3).unwrap_err().code, "invalid_search_query");
    assert_eq!(search("query", 0).unwrap_err().code, "invalid_search_query");
}

#[test]
fn redirects_content_types_and_empty_search_return_real_errors() {
    let server = web_server();
    assert_eq!(
        fetch(&format!("{}/private-redirect", server.url()))
            .unwrap_err()
            .code,
        "invalid_web_url"
    );
    assert_eq!(
        fetch(&format!("{}/binary", server.url())).unwrap_err().code,
        "web_content_type_blocked"
    );
    assert_eq!(
        fetch(&format!("{}/missing", server.url()))
            .unwrap_err()
            .code,
        "web_http_error"
    );
    let oversized = crate::backend::providers::test_support::server(|_request, stream| {
        stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1048577\r\nConnection: close\r\n\r\n").unwrap();
    });
    assert_eq!(
        fetch(&oversized.url()).unwrap_err().code,
        "web_response_too_large"
    );
    let _search_lock = crate::backend::web::SEARCH_ENDPOINT_LOCK.lock().unwrap();
    let _guard = set_search_endpoint_for_tests(Some(format!("{}/empty-search", server.url())));
    assert_eq!(
        search("没有结果", 3).unwrap_err().code,
        "search_unavailable"
    );
}

#[test]
fn html_parser_handles_quoted_angle_brackets_and_separate_snippets() {
    let parsed = html::parse("<title>真实 &amp; 标题</title><template>不读取</template><p>正文 &gt; 文本</p><a data-x=\"1 > 0\" class=\"result__a\" href=\"/article\">标题</a><a class=\"result__snippet\">分离的摘要</a>", true).unwrap();
    assert_eq!(parsed.title, "真实 & 标题");
    assert!(parsed.text.contains("正文 > 文本"));
    assert!(!parsed.text.contains("不读取"));
    assert_eq!(parsed.candidates.len(), 1);
    assert_eq!(parsed.candidates[0].title, "标题");
    assert_eq!(parsed.candidates[0].snippet, "分离的摘要");
}

#[test]
fn webpage_import_preserves_source_parses_real_text_and_deduplicates_through_ipc() {
    let (app, learning) = ipc_app();
    let server = web_server();
    let url = format!("{}/article", server.url());
    let page = ipc(&app, "fetch_webpage", json!({"url":url})).unwrap();
    assert_eq!(page["title"], "实际文章标题");
    assert_eq!(page["text"], "真实正文 & 证据");
    let imported = ipc(&app, "import_webpage", json!({"url":url})).unwrap();
    assert_eq!(imported["deduplicated"], false);
    assert_eq!(imported["material"]["title"], "实际文章标题");
    assert_eq!(imported["material"]["sourceUrl"], url);
    assert_eq!(imported["material"]["originalAvailable"], true);
    let id = imported["material"]["id"].as_i64().unwrap();
    let state = ipc(&app, "get_app_state", json!({})).unwrap();
    assert_eq!(state["resources"][0]["type"], "网页");
    assert!(state["resources"][0]["meta"]
        .as_str()
        .unwrap()
        .contains(&url));
    ipc(&app, "enqueue_material_parse", json!({"id":id})).unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let detail = loop {
        let detail = ipc(&app, "get_material", json!({"id":id})).unwrap();
        if detail["status"] == "parsed" {
            break detail;
        }
        assert_ne!(detail["status"], "failed");
        assert!(
            std::time::Instant::now() < deadline,
            "网页资料未在限时内完成原生解析"
        );
        std::thread::sleep(std::time::Duration::from_millis(5));
    };
    assert_eq!(detail["text"], "真实正文 & 证据");
    assert_eq!(detail["sourceUrl"], url);
    let source: String = learning
        .store()
        .unwrap()
        .conn
        .query_row(
            "SELECT source_meta FROM materials WHERE id = ?1",
            rusqlite::params![id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(source, url);
    let events = ipc(&app, "events_since", json!({"lastId":0})).unwrap()["events"]
        .as_array()
        .unwrap()
        .clone();
    let staged = events
        .iter()
        .position(|event| event["kind"] == "material.staged")
        .unwrap();
    let parsed = events
        .iter()
        .position(|event| event["kind"] == "material.parsed")
        .unwrap();
    assert!(staged < parsed);
    let duplicate = ipc(&app, "import_webpage", json!({"url":url})).unwrap();
    assert_eq!(duplicate["deduplicated"], true);
    assert_eq!(duplicate["material"]["id"], id);
    assert_eq!(
        ipc(&app, "get_app_state", json!({})).unwrap()["resources"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
