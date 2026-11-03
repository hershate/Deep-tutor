use super::*;
use crate::backend::{learning::LearningState, security::SecurityState};
use serde_json::{json, Value};
use std::io::Write;
use std::sync::Mutex;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    webview::InvokeRequest,
    Manager,
};

fn setup() -> tauri::App<tauri::test::MockRuntime> {
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
            commands::import_notes,
            crate::backend::export::commands::export_notes_archive,
            crate::backend::commands::get_collection,
            crate::backend::commands::events_since
        ])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .unwrap();
    tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    app
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

fn archive(path: &Path, entries: &[(&str, &[u8])]) {
    let file = std::fs::File::create(path).unwrap();
    let mut writer = zip::ZipWriter::new(file);
    for (name, content) in entries {
        writer
            .start_file(*name, zip::write::SimpleFileOptions::default())
            .unwrap();
        writer.write_all(content).unwrap();
    }
    writer.finish().unwrap();
}

fn notes(app: &tauri::App<tauri::test::MockRuntime>) -> Value {
    ipc(app, "get_collection", json!({"namespace":"notes"})).unwrap()["value"].clone()
}

#[test]
fn native_archive_restores_metadata_appends_and_emits_committed_notes() {
    let app = setup();
    let directory = tempfile::tempdir().unwrap();
    let store = app.state::<BackendState>();
    store.store.lock().unwrap().replace_collection("notes", 0, &json!([{"id":1,"title":"保留原笔记","excerpt":"不覆盖","tag":"原标签","updated":"原时间","content":"原有正文"}])).unwrap();
    let path = directory.path().join("notes.zip");
    archive(
        &path,
        &[
            (
                "知舟笔记/001-导出文件.md",
                "# 原生标题\n\n标签：真实标签\n更新时间：原时间\n\n正文 **完整保留**".as_bytes(),
            ),
            ("notes/plain.txt", "纯文本正文".as_bytes()),
            ("manifest.json", b"{\"version\":1}"),
            ("assets/image.png", b"not a note"),
        ],
    );
    let result = ipc(&app, "import_notes", json!({"path":path.to_string_lossy()})).unwrap();
    assert_eq!(result["imported"], 2);
    assert_eq!(result["state"]["notes"].as_array().unwrap().len(), 3);
    let imported_notes = notes(&app);
    assert_eq!(imported_notes[0]["content"], "原有正文");
    assert_eq!(imported_notes[1]["title"], "原生标题");
    assert_eq!(imported_notes[1]["tag"], "真实标签");
    assert_eq!(imported_notes[1]["content"], "正文 **完整保留**");
    assert_eq!(imported_notes[2]["title"], "plain");
    assert_eq!(imported_notes[2]["content"], "纯文本正文");
    assert_ne!(imported_notes[1]["id"], imported_notes[2]["id"]);
    assert!(imported_notes[1]["updated"].as_str().unwrap().contains('T'));
    let events = ipc(&app, "events_since", json!({"lastId":0})).unwrap();
    assert_eq!(events["events"].as_array().unwrap().len(), 2);
    assert_eq!(events["events"][1]["kind"], "state.changed");
    assert_eq!(events["events"][1]["payload"]["namespace"], "notes");
    assert_eq!(events["events"][1]["payload"]["imported"], 2);
    let repeated = ipc(&app, "import_notes", json!({"path":path.to_string_lossy()})).unwrap();
    assert_eq!(repeated["imported"], 2);
    assert_eq!(notes(&app).as_array().unwrap().len(), 5);
    assert_eq!(notes(&app)[0]["content"], "原有正文");
}

#[test]
fn unsafe_oversized_and_invalid_sources_never_partially_import() {
    let app = setup();
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("unsafe.zip");
    archive(
        &path,
        &[
            ("../escape.md", "不能导入".as_bytes()),
            ("valid.md", "也不能部分保存".as_bytes()),
        ],
    );
    assert_eq!(
        ipc(&app, "import_notes", json!({"path":path.to_string_lossy()})).unwrap_err()["code"],
        "unsafe_archive_path"
    );
    assert!(notes(&app).as_array().unwrap().is_empty());
    assert_eq!(
        ipc(&app, "events_since", json!({"lastId":0})).unwrap()["events"],
        json!([])
    );
    let oversized = directory.path().join("oversized.zip");
    let large = vec![b'a'; (MAX_NOTE_BYTES + 1) as usize];
    archive(&oversized, &[("large.md", &large)]);
    assert_eq!(
        ipc(
            &app,
            "import_notes",
            json!({"path":oversized.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "quota_exceeded"
    );
    let empty = directory.path().join("empty.zip");
    archive(&empty, &[("manifest.json", b"{}")]);
    assert_eq!(
        ipc(
            &app,
            "import_notes",
            json!({"path":empty.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "empty_notes_import"
    );
    let text = directory.path().join("invalid.txt");
    std::fs::write(&text, [0xff, 0xfe]).unwrap();
    assert_eq!(
        ipc(&app, "import_notes", json!({"path":text.to_string_lossy()})).unwrap_err()["code"],
        "invalid_note_encoding"
    );
    let blank = directory.path().join("blank.md");
    std::fs::write(&blank, " \n").unwrap();
    assert_eq!(
        ipc(
            &app,
            "import_notes",
            json!({"path":blank.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "empty_note_content"
    );
    let wrong = directory.path().join("not-notes.docx");
    std::fs::write(&wrong, b"not a zip").unwrap();
    assert_eq!(
        ipc(
            &app,
            "import_notes",
            json!({"path":wrong.to_string_lossy()})
        )
        .unwrap_err()["code"],
        "invalid_note_format"
    );
    assert!(notes(&app).as_array().unwrap().is_empty());
}

#[test]
fn real_exported_archive_round_trips_through_ipc_without_losing_note_content() {
    let app = setup();
    let directory = tempfile::tempdir().unwrap();
    let original = json!([{"id":7,"title":"往返笔记","excerpt":"真实摘要","tag":"原始标签","updated":"原始时间","content":"# 保留标题\n\n**加粗**、公式 $P(A|B)$ 与原始换行。"}]);
    app.state::<BackendState>()
        .store
        .lock()
        .unwrap()
        .replace_collection("notes", 0, &original)
        .unwrap();
    let path = directory.path().join("native.zip");
    let exported = ipc(
        &app,
        "export_notes_archive",
        json!({"destination":path.to_string_lossy()}),
    )
    .unwrap();
    assert_eq!(exported["count"], 1);
    let imported = ipc(&app, "import_notes", json!({"path":path.to_string_lossy()})).unwrap();
    assert_eq!(imported["imported"], 1);
    let notes = notes(&app);
    assert_eq!(notes.as_array().unwrap().len(), 2);
    assert_eq!(notes[0], original[0]);
    assert_eq!(notes[1]["title"], "往返笔记");
    assert_eq!(notes[1]["tag"], "原始标签");
    assert_eq!(notes[1]["content"], original[0]["content"]);
    assert_ne!(notes[1]["id"], 7);
    let events = ipc(&app, "events_since", json!({"lastId":0})).unwrap()["events"]
        .as_array()
        .unwrap()
        .clone();
    assert_eq!(events.last().unwrap()["kind"], "state.changed");
    assert_eq!(events.last().unwrap()["payload"]["namespace"], "notes");
}
