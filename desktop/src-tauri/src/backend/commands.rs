use super::error::Result;
use super::store::{BackendEvent, Collection, EventPage, Mutation, Store};
use super::BackendState;
use serde_json::Value;
use std::sync::MutexGuard;
use tauri::{AppHandle, Emitter, Runtime, State};

fn store<'a>(state: &'a State<'_, BackendState>) -> Result<MutexGuard<'a, Store>> {
    state
        .store
        .lock()
        .map_err(|_| super::error::BackendError::new("store_unavailable", "本地数据层暂不可用"))
}

fn finish<R: Runtime>(app: &AppHandle<R>, mutation: Mutation) -> Result<Value> {
    if let Some(event) = mutation.event {
        emit(app, &event);
    }
    Ok(mutation.state)
}

pub(crate) fn emit<R: Runtime>(app: &AppHandle<R>, event: &BackendEvent) {
    if let Err(error) = app.emit("backend://event", event) {
        eprintln!("backend event {} delivery failed: {error}", event.kind);
    }
}

#[tauri::command]
pub fn bootstrap(state: State<'_, BackendState>) -> Result<Value> {
    store(&state)?.bootstrap()
}

#[tauri::command]
pub fn get_app_state(state: State<'_, BackendState>) -> Result<Value> {
    store(&state)?.app_state()
}

#[tauri::command]
pub fn get_collection(state: State<'_, BackendState>, namespace: String) -> Result<Collection> {
    store(&state)?.collection(&namespace)
}

#[tauri::command]
pub fn replace_collection<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    namespace: String,
    expected_revision: i64,
    value: Value,
) -> Result<Value> {
    let mutation = store(&state)?.replace_collection(&namespace, expected_revision, &value)?;
    finish(&app, mutation)
}

#[tauri::command]
pub fn save_draft<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
    value: Value,
) -> Result<Value> {
    let mutation = store(&state)?.put_draft(&id, &value)?;
    finish(&app, mutation)
}

#[tauri::command]
pub fn get_draft(state: State<'_, BackendState>, id: String) -> Result<Collection> {
    store(&state)?.draft(&id)
}

#[tauri::command]
pub fn delete_draft<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    id: String,
) -> Result<Value> {
    let mutation = store(&state)?.delete_draft(&id)?;
    finish(&app, mutation)
}

#[tauri::command]
pub fn reset_application<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
) -> Result<Value> {
    super::learning::stop(&state.learning)?;
    super::providers::stop_chats(&state.providers)?;
    let provider_ids = super::providers::credential_ids(&state.providers)?;
    let backup = super::materials::prepare_reset(&state.materials)?;
    let result = { store(&state)?.reset() };
    match result {
        Ok(mutation) => {
            let file_result = super::materials::finish_reset(&state.materials, backup, true);
            let credential_result =
                super::providers::purge_credentials(&state.providers, &provider_ids);
            file_result?;
            credential_result?;
            finish(&app, mutation)
        }
        Err(error) => {
            super::materials::finish_reset(&state.materials, backup, false)?;
            Err(error)
        }
    }
}

#[tauri::command]
pub fn export_backup(state: State<'_, BackendState>) -> Result<Value> {
    store(&state)?.export_backup()
}

#[tauri::command]
pub fn preview_backup(state: State<'_, BackendState>, backup: Value) -> Result<Value> {
    store(&state)?.preview_backup(&backup)
}

#[tauri::command]
pub fn import_backup<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, BackendState>,
    backup: Value,
) -> Result<Value> {
    let mutation = store(&state)?.import_backup(&backup)?;
    finish(&app, mutation)
}

#[tauri::command]
pub fn events_since(state: State<'_, BackendState>, last_id: i64) -> Result<EventPage> {
    store(&state)?.events_since(last_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::{Arc, Mutex};
    use tauri::ipc::{CallbackFn, InvokeBody};
    use tauri::webview::InvokeRequest;
    use tauri::{Listener, Manager, WebviewWindowBuilder};

    fn test_app() -> tauri::App<tauri::test::MockRuntime> {
        let materials = crate::backend::materials::MaterialState::for_tests().unwrap();
        let app = tauri::test::mock_builder()
            .manage(BackendState {
                store: Mutex::new(Store::new(materials.test_database_path()).unwrap()),
                materials,
                providers: crate::backend::providers::ProviderState::for_tests().unwrap(),
                learning: crate::backend::learning::LearningState::for_tests().unwrap(),
            })
            .invoke_handler(tauri::generate_handler![
                bootstrap,
                get_app_state,
                replace_collection,
                events_since,
                reset_application,
                crate::backend::materials::commands::import_material_files,
                crate::backend::materials::commands::list_materials,
                crate::backend::materials::commands::get_material,
                crate::backend::materials::commands::enqueue_material_parse,
                crate::backend::materials::commands::delete_material,
                crate::backend::materials::commands::export_material_original,
                crate::backend::materials::commands::export_material_text,
            ])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        app
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
                cmd: command.to_string(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: "http://tauri.localhost".parse().unwrap(),
                body: InvokeBody::Json(arguments),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        )
        .map(|body| body.deserialize::<Value>().unwrap())
    }

    #[test]
    fn ipc_emits_committed_events_and_typed_errors() {
        let app = test_app();
        assert_eq!(
            ipc(&app, "bootstrap", json!({})).unwrap()["tasks"],
            json!([])
        );
        let received = Arc::new(Mutex::new(Vec::new()));
        let received_events = Arc::clone(&received);
        let event_app = app.handle().clone();
        app.listen("backend://event", move |message| {
            let event: Value = serde_json::from_str(message.payload()).unwrap();
            let state = event_app.state::<BackendState>();
            let tasks = state
                .store
                .lock()
                .unwrap()
                .collection("tasks")
                .unwrap()
                .value;
            received_events
                .lock()
                .unwrap()
                .push(json!({"event": event, "tasks": tasks}));
        });
        let task = json!({"id": 1, "title": "IPC 任务", "subject": "数学", "duration": 20, "done": false, "kind": "学习"});
        let state = ipc(
            &app,
            "replace_collection",
            json!({
                "namespace": "tasks", "expectedRevision": 0, "value": [task]
            }),
        )
        .unwrap();
        assert_eq!(state["tasks"][0]["title"], "IPC 任务");
        assert_eq!(state["lastEventId"], 1);
        let observed = received.lock().unwrap();
        assert_eq!(observed.len(), 1);
        assert_eq!(observed[0]["event"]["id"], 1);
        assert_eq!(observed[0]["event"]["kind"], "state.changed");
        assert_eq!(observed[0]["event"]["payload"]["namespace"], "tasks");
        assert_eq!(observed[0]["tasks"][0]["title"], "IPC 任务");
        drop(observed);

        let page = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
        assert_eq!(page["events"].as_array().unwrap().len(), 1);
        assert_eq!(page["events"][0]["id"], 1);
        assert_eq!(page["hasMore"], false);
        let error = ipc(
            &app,
            "replace_collection",
            json!({
                "namespace": "tasks", "expectedRevision": 0, "value": []
            }),
        )
        .unwrap_err();
        assert_eq!(error["code"], "revision_conflict");
        assert_eq!(received.lock().unwrap().len(), 1);
        assert_eq!(
            ipc(&app, "events_since", json!({"lastId": 1})).unwrap()["events"],
            json!([])
        );

        let reset = ipc(&app, "reset_application", json!({})).unwrap();
        assert_eq!(reset["tasks"], json!([]));
        assert_eq!(reset["lastEventId"], 2);
        assert_eq!(received.lock().unwrap().len(), 2);
    }

    #[test]
    fn ipc_material_commands_share_state_and_emit_real_results() {
        let app = test_app();
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("study.md");
        std::fs::write(&source, "# 原生资料").unwrap();
        let imported = ipc(
            &app,
            "import_material_files",
            json!({"paths": [source.to_string_lossy()]}),
        )
        .unwrap();
        assert_eq!(imported[0]["error"], Value::Null);
        let id = imported[0]["material"]["id"].as_i64().unwrap();
        let materials = ipc(&app, "list_materials", json!({})).unwrap();
        assert_eq!(materials.as_array().unwrap().len(), 1);
        assert_eq!(materials[0]["status"], "staged");
        let state = ipc(&app, "get_app_state", json!({})).unwrap();
        assert_eq!(state["resources"][0]["title"], "study.md");
        ipc(&app, "enqueue_material_parse", json!({"id": id})).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let detail = loop {
            let detail = ipc(&app, "get_material", json!({"id": id})).unwrap();
            if detail["status"] == "parsed" {
                break detail;
            }
            assert_ne!(detail["status"], "failed", "{detail}");
            assert!(
                std::time::Instant::now() < deadline,
                "parse did not finish: {detail}"
            );
            std::thread::sleep(std::time::Duration::from_millis(5));
        };
        assert_eq!(detail["text"], "# 原生资料");
        let events = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
        assert!(events["events"].as_array().unwrap().iter().any(|event| {
            event["kind"] == "material.parsed" && event["payload"]["materialId"] == id
        }));
        let destination = directory.path().join("parsed.txt");
        ipc(
            &app,
            "export_material_text",
            json!({"id": id, "destination": destination.to_string_lossy()}),
        )
        .unwrap();
        assert_eq!(std::fs::read_to_string(destination).unwrap(), "# 原生资料");
        let original = app
            .state::<BackendState>()
            .materials
            .test_database_path()
            .parent()
            .unwrap()
            .join("materials")
            .join("originals")
            .join(format!(
                "{}.md",
                imported[0]["material"]["sha256"].as_str().unwrap()
            ));
        assert!(original.is_file());
        let deleted = ipc(&app, "delete_material", json!({"id": id})).unwrap();
        assert_eq!(deleted["resources"], json!([]));
        assert!(!original.exists());
    }

    #[test]
    fn real_shell_allows_native_event_listeners() {
        let capability: Value =
            serde_json::from_str(include_str!("../../capabilities/default.json")).unwrap();
        assert!(capability["windows"]
            .as_array()
            .unwrap()
            .contains(&json!("main")));
        let permissions = capability["permissions"].as_array().unwrap();
        for permission in [
            "core:event:allow-listen",
            "core:event:allow-unlisten",
            "dialog:allow-open",
            "dialog:allow-save",
        ] {
            assert!(
                permissions.contains(&json!(permission)),
                "missing {permission}"
            );
        }
    }
}
