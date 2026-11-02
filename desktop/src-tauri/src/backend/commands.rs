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

fn emit<R: Runtime>(app: &AppHandle<R>, event: &BackendEvent) {
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
    let mutation = store(&state)?.reset()?;
    finish(&app, mutation)
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
        let app = tauri::test::mock_builder()
            .manage(BackendState {
                store: Mutex::new(Store::in_memory().unwrap()),
            })
            .invoke_handler(tauri::generate_handler![
                bootstrap,
                replace_collection,
                events_since,
                reset_application
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
    fn real_shell_allows_native_event_listeners() {
        let capability: Value =
            serde_json::from_str(include_str!("../../capabilities/default.json")).unwrap();
        assert!(capability["windows"]
            .as_array()
            .unwrap()
            .contains(&json!("main")));
        let permissions = capability["permissions"].as_array().unwrap();
        for permission in ["core:event:allow-listen", "core:event:allow-unlisten"] {
            assert!(
                permissions.contains(&json!(permission)),
                "missing {permission}"
            );
        }
    }
}
