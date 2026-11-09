use super::error::Result;
use super::store::{BackendEvent, Collection, EventPage, Mutation, Store};
use super::BackendState;
use serde_json::Value;
use std::sync::MutexGuard;
use tauri::{AppHandle, Emitter, Runtime, State};

fn store<'a>(state: &'a State<'_, BackendState>) -> Result<MutexGuard<'a, Store>> {
    // 73#16：锁中毒（持锁线程 panic unwind）恢复而非永久拒绝——rusqlite
    // 事务在 unwind 时 Drop 回滚，连接保持一致，可安全复用
    Ok(state
        .store
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()))
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

// 72#2：资源管理器定位从前端插件权限收回后端——原先 WebView 持有
// opener:allow-reveal-item-in-dir（无 scope），被注入脚本可定位本机任意
// 路径。现仅放行两类目标：当前工作区数据根（设置页入口）与本进程近期
// 真实写出过的导出回执（reveal::TTL 24h/上限 64 条）
#[tauri::command]
pub fn reveal_path<R: Runtime>(
    app: AppHandle<R>,
    startup: State<'_, crate::startup::StartupState>,
    path: String,
) -> Result<()> {
    use tauri_plugin_opener::OpenerExt;
    let target = super::lexical_absolute(std::path::Path::new(&path));
    let data_root = super::lexical_absolute(startup.data_root());
    if !super::reveal::is_known(&target) && target != data_root {
        return Err(super::error::BackendError::invalid(
            "reveal_not_allowed",
            "只能定位数据目录或本应用刚导出的文件",
        ));
    }
    app.opener()
        .reveal_item_in_dir(&target)
        .map_err(|_| super::error::BackendError::new("reveal_failed", "资源管理器定位失败"))
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

// 73#10/73#14：停机集合改用 guard::stop_all（原先六个串行 `?` 首败即弃、
// 漏 materials/focus 两域且与 guard 注释宣称的「同一实现」不符）；
// reset 大事务 + remove_dir_all 整目录删除（上限 ~10GB）移后台线程，
// 不再在主线程冻结秒到数十秒
#[tauri::command(async)]
pub async fn reset_application<R: Runtime + 'static>(app: AppHandle<R>) -> Result<Value> {
    use tauri::Manager;
    let principal = super::security::session_principal(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        super::security::with_principal(principal, || {
            let state = app.state::<BackendState>();
            super::security::guard::stop_all(&state)?;
            let provider_ids = super::providers::credential_ids(&state.providers)?;
            let backup = super::materials::prepare_reset(&state.materials)?;
            let result = {
                state
                    .store
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .reset()
            };
            match result {
                Ok(mutation) => {
                    let file_result =
                        super::materials::finish_reset(&state.materials, backup, true);
                    let credential_result =
                        super::providers::purge_credentials(&state.providers, &provider_ids);
                    file_result?;
                    credential_result?;
                    super::plugins::reset_files(&state.plugins)?;
                    // 65#2：reset 清空了 agent/sync 两域表行，但两目录的文件原先
                    // 不在任何清理面——「数据已清空」后 scratch 产物与同步副本仍
                    // 在盘上。成功路径尽力删除两目录；失败的残留由孤儿扫描兜底
                    // 73#10：目录删除（上限 ~10GB）移后台一次性线程——reset
                    // 返回不等删除完成，失败仅孤儿扫描兜底
                    if let Some(user_root) = state.learning.workspace_path().parent() {
                        let agent_dir = user_root.join("agent-workspaces");
                        let sync_dir = user_root.join("sync");
                        std::thread::spawn(move || {
                            let _ = std::fs::remove_dir_all(agent_dir);
                            let _ = std::fs::remove_dir_all(sync_dir);
                        });
                    }
                    finish(&app, mutation)
                }
                Err(error) => {
                    super::materials::finish_reset(&state.materials, backup, false)?;
                    Err(error)
                }
            }
        })
    })
    .await
    .map_err(|_| super::error::BackendError::new("internal_error", "重置任务执行异常终止"))?
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
                agent: crate::backend::agent::AgentState::for_tests().unwrap(),
                practice: crate::backend::practice::PracticeState::for_tests().unwrap(),
                review: crate::backend::review::ReviewState::for_tests().unwrap(),
                knowledge: crate::backend::knowledge::KnowledgeState::for_tests().unwrap(),
                community: crate::backend::community::CommunityState::for_tests().unwrap(),
                plugins: crate::backend::plugins::PluginState::for_tests().unwrap(),
                sharing: crate::backend::sharing::SharingState::for_tests().unwrap(),
            })
            .invoke_handler(tauri::generate_handler![
                bootstrap,
                get_app_state,
                replace_collection,
                events_since,
                reset_application,
                crate::backend::materials::commands::import_material_files,
                crate::backend::materials::commands::import_material_archive,
                crate::backend::materials::commands::import_material_text,
                crate::backend::materials::commands::list_materials,
                crate::backend::materials::commands::get_material,
                crate::backend::materials::commands::get_material_media_info,
                crate::backend::materials::commands::read_material_media_chunk,
                crate::backend::materials::commands::verify_material_media,
                crate::backend::materials::commands::enqueue_material_parse,
                crate::backend::materials::commands::cancel_material_parse,
                crate::backend::materials::commands::delete_material,
                crate::backend::materials::commands::export_material_original,
                crate::backend::materials::commands::export_material_text,
                crate::backend::engagement::commands::get_learning_profile,
                crate::backend::engagement::commands::save_learning_profile,
                crate::backend::providers::commands::export_usage_csv_file,
                crate::backend::plugins::commands::save_plugin_asset,
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

    /// 46#2：二进制应答命令（tauri::ipc::Response）的 IPC 级读取——
    /// mock 通道返回 InvokeBody::Raw，JSON 反序列化只得 Null
    fn ipc_raw(
        app: &tauri::App<tauri::test::MockRuntime>,
        command: &str,
        arguments: Value,
    ) -> std::result::Result<Vec<u8>, Value> {
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
        .map(|body| match body {
            tauri::ipc::InvokeResponseBody::Raw(bytes) => bytes,
            tauri::ipc::InvokeResponseBody::Json(value) => serde_json::to_vec(&value).unwrap(),
        })
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

    // 65#2：reset 成功后 agent-workspaces 与 sync 目录的文件证据一并清除
    #[test]
    fn reset_application_clears_agent_and_sync_directories() {
        let app = test_app();
        let user_root = app
            .state::<BackendState>()
            .learning
            .workspace_path()
            .parent()
            .unwrap()
            .to_path_buf();
        std::fs::create_dir_all(user_root.join("agent-workspaces/9/scratch")).unwrap();
        std::fs::write(user_root.join("agent-workspaces/9/scratch/a.json"), "{}").unwrap();
        std::fs::create_dir_all(user_root.join("sync")).unwrap();
        std::fs::write(user_root.join("sync/sync-1-0.json"), "{}").unwrap();
        ipc(&app, "reset_application", json!({})).unwrap();
        // 73#10：目录删除移后台一次性线程（上限 ~10GB，不再阻塞命令返回）——
        // 有界轮询等待删除完成
        let mut cleared = false;
        for _ in 0..200 {
            if !user_root.join("agent-workspaces").exists() && !user_root.join("sync").exists() {
                cleared = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(cleared, "后台目录删除未在限时内完成");
        assert!(!user_root.join("agent-workspaces").exists());
        assert!(!user_root.join("sync").exists());
    }

    // 65#6：被项目版本引用的资料删除须友好拒绝，不得静默 SET NULL 断链
    #[test]
    fn delete_material_referenced_by_project_version_is_rejected() {
        let app = test_app();
        let created = ipc(
            &app,
            "import_material_text",
            json!({"title": "被项目引用", "content": "版本证据"}),
        )
        .unwrap();
        let id = created["material"]["id"].as_i64().unwrap();
        {
            let state = app.state::<BackendState>();
            let store = state.store.lock().unwrap();
            store
                .conn
                .execute(
                    "INSERT INTO project_versions (project_id, version_number, content, material_id, status, attempt, provider_id, created_at, updated_at) VALUES (1, 1, '版本', ?1, 'ready', 1, 'p1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    rusqlite::params![id],
                )
                .unwrap();
        }
        let error = ipc(&app, "delete_material", json!({"id": id})).unwrap_err();
        assert_eq!(error["code"], "material_in_use");
        assert!(error["message"].as_str().unwrap().contains("项目版本"));
    }

    // 65#5：导出目的地位于当前数据根或旧默认目录内均拒绝（迁移后旧默认
    // 目录仍存历史库与原件）
    #[test]
    fn export_destination_inside_default_root_is_rejected() {
        let app = test_app();
        let data_root = tempfile::tempdir().unwrap();
        let default_root = tempfile::tempdir().unwrap();
        app.manage(crate::startup::StartupState::for_tests(
            data_root.path(),
            default_root.path(),
        ));
        let error = ipc(
            &app,
            "export_usage_csv_file",
            json!({"destination": data_root.path().join("usage.csv").to_string_lossy()}),
        )
        .unwrap_err();
        assert_eq!(error["code"], "unsafe_export_destination");
        let error = ipc(
            &app,
            "export_usage_csv_file",
            json!({"destination": default_root.path().join("usage.csv").to_string_lossy()}),
        )
        .unwrap_err();
        assert_eq!(error["code"], "unsafe_export_destination");
        let outside = tempfile::tempdir().unwrap();
        ipc(
            &app,
            "export_usage_csv_file",
            json!({"destination": outside.path().join("usage.csv").to_string_lossy()}),
        )
        .unwrap();
    }

    // 64#1：取消解析的 IPC 全链——queued 态取消立即转 cancelled，队列恢复
    // 后不复活；无任务时类型化拒绝
    #[test]
    fn cancel_material_parse_ipc_handles_queued_and_idle_states() {
        let app = test_app();
        let created = ipc(
            &app,
            "import_material_text",
            json!({"title": "待取消.txt", "content": "取消正文"}),
        )
        .unwrap();
        let id = created["material"]["id"].as_i64().unwrap();
        {
            let state = app.state::<BackendState>();
            crate::backend::materials::pause_queue_for_tests(&state.materials, true);
        }
        ipc(&app, "enqueue_material_parse", json!({"id": id})).unwrap();
        let cancelling = ipc(&app, "cancel_material_parse", json!({"id": id})).unwrap();
        // 任务已注册运行旗标（worker 暂停在 pause 上）——取消先落 cancelling，
        // 由 worker 恢复执行时确认旗标并转终态
        assert_eq!(cancelling["status"], "cancelling");
        {
            let state = app.state::<BackendState>();
            crate::backend::materials::pause_queue_for_tests(&state.materials, false);
        }
        crate::backend::materials::wait_for_idle(&app.state::<BackendState>().materials).unwrap();
        let detail = ipc(&app, "get_material", json!({"id": id})).unwrap();
        assert_eq!(detail["status"], "cancelled");
        let error = ipc(&app, "cancel_material_parse", json!({"id": id})).unwrap_err();
        assert_eq!(error["code"], "no_active_job");
    }

    // 64#2：原件导出 IPC 链——回执与落盘一致、目的地遏制、原件被同长度
    // 篡改后哈希复核拒绝
    #[test]
    fn export_material_original_ipc_checks_destination_and_hash() {
        let app = test_app();
        let created = ipc(
            &app,
            "import_material_text",
            json!({"title": "原件导出.txt", "content": "原件证据"}),
        )
        .unwrap();
        let id = created["material"]["id"].as_i64().unwrap();
        let sha = created["material"]["sha256"].as_str().unwrap().to_string();
        let guard = tempfile::tempdir().unwrap();
        let destination = guard.path().join("original.txt");
        let receipt = ipc(
            &app,
            "export_material_original",
            json!({"id": id, "destination": destination.to_string_lossy()}),
        )
        .unwrap();
        let written = std::fs::read(&destination).unwrap();
        assert_eq!(written, "原件证据".as_bytes());
        assert_eq!(receipt["bytes"], json!(written.len()));
        assert_eq!(receipt["sha256"], json!(sha));
        // 二道遏制：数据根内拒绝（guard 尚未管理 StartupState 时首段已验证直通）
        let data_root = tempfile::tempdir().unwrap();
        let default_root = tempfile::tempdir().unwrap();
        app.manage(crate::startup::StartupState::for_tests(
            data_root.path(),
            default_root.path(),
        ));
        let error = ipc(
            &app,
            "export_material_original",
            json!({"id": id, "destination": data_root.path().join("copy.txt").to_string_lossy()}),
        )
        .unwrap_err();
        assert_eq!(error["code"], "unsafe_export_destination");
        // 原件篡改（同长度）→ SHA-256 复核拒绝导出
        let original = app
            .state::<BackendState>()
            .materials
            .test_database_path()
            .parent()
            .unwrap()
            .join("materials")
            .join("originals")
            .join(format!("{sha}.txt"));
        std::fs::write(&original, "原件篡改!").unwrap();
        let error = ipc(
            &app,
            "export_material_original",
            json!({"id": id, "destination": guard.path().join("copy2.txt").to_string_lossy()}),
        )
        .unwrap_err();
        assert_eq!(error["code"], "original_changed");
    }

    // 64#3：档案保存-读取 IPC 往返——原先 save 有测而 get 零断言，字段
    // 增删/序列化默认值破坏将在此暴露
    #[test]
    fn learning_profile_save_and_get_round_trip() {
        let app = test_app();
        let initial = ipc(&app, "get_learning_profile", json!({})).unwrap();
        assert!(initial["language"].is_string(), "默认档案必须完整可读");
        let input = json!({
            "name": "往返测试者", "role": "自学者", "goal": "往返目标",
            "language": "zh-CN", "weeklyHours": 8,
            "learningStyle": "案例与练习结合", "explanationStyle": "先追问，再针对讲解",
            "quietStart": "23:58", "quietEnd": "23:59", "quietEnabled": true,
            "diagnosticStartedAt": null
        });
        let saved = ipc(&app, "save_learning_profile", json!({"input": input})).unwrap();
        assert_eq!(saved["name"], "往返测试者");
        let reread = ipc(&app, "get_learning_profile", json!({})).unwrap();
        assert_eq!(reread, saved, "读取必须与保存回执逐字段一致");
    }

    #[test]
    fn blob_export_commands_write_to_chosen_destination() {
        // 37#5：原前端 Blob 静默下载的内容改由原生后端写盘——回执字节数与
        // SHA-256 必须和实际落盘内容一致，且目的地必须是文件
        use sha2::{Digest, Sha256};
        let app = test_app();
        let guard = tempfile::tempdir().unwrap();
        let csv_path = guard.path().join("usage.csv");
        let receipt = ipc(
            &app,
            "export_usage_csv_file",
            json!({"destination": csv_path.to_string_lossy()}),
        )
        .unwrap();
        let written = std::fs::read_to_string(&csv_path).unwrap();
        assert!(!written.is_empty());
        assert_eq!(receipt["bytes"], json!(written.len()));
        assert_eq!(
            receipt["sha256"],
            json!(format!("{:x}", Sha256::digest(written.as_bytes())))
        );
        let asset_path = guard.path().join("native-operations.md");
        let asset = ipc(
            &app,
            "save_plugin_asset",
            json!({"id": "native-operations", "destination": asset_path.to_string_lossy()}),
        )
        .unwrap();
        let asset_written = std::fs::read(&asset_path).unwrap();
        assert!(!asset_written.is_empty());
        assert_eq!(asset["bytes"], json!(asset_written.len()));
        assert_eq!(
            asset["sha256"],
            json!(format!("{:x}", Sha256::digest(&asset_written)))
        );
        // 目的地没有文件名（磁盘根）被类型化拒绝；目录目的地的 rename 语义
        // 属 atomic_export 既有行为，实际 UI 目的地恒来自原生保存对话框
        let root = if cfg!(windows) { "C:\\" } else { "/" };
        let error = ipc(&app, "export_usage_csv_file", json!({"destination": root})).unwrap_err();
        assert_eq!(error["code"], "invalid_destination");
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
    fn ipc_text_import_preserves_real_content_and_deduplicates() {
        let app = test_app();
        let created = ipc(
            &app,
            "import_material_text",
            json!({"title": "粘贴章节", "content": "先验、似然与后验"}),
        )
        .unwrap();
        assert_eq!(created["material"]["title"], json!("粘贴章节"));
        assert_eq!(created["material"]["originalAvailable"], json!(true));
        assert_eq!(created["deduplicated"], json!(false));
        let repeated = ipc(
            &app,
            "import_material_text",
            json!({"title": "另一标题", "content": "先验、似然与后验"}),
        )
        .unwrap();
        assert_eq!(repeated["deduplicated"], json!(true));
        assert_eq!(repeated["material"]["id"], created["material"]["id"]);
        assert_eq!(repeated["material"]["title"], json!("粘贴章节"));
        let materials = ipc(&app, "list_materials", json!({})).unwrap();
        assert_eq!(materials.as_array().unwrap().len(), 1);
        let events = ipc(&app, "events_since", json!({"lastId": 0})).unwrap();
        assert_eq!(events["events"].as_array().unwrap().len(), 1);
        assert_eq!(events["events"][0]["kind"], json!("material.staged"));
    }

    #[test]
    fn native_media_import_hash_and_chunk_reads_preserve_real_bytes() {
        let app = test_app();
        let directory = tempfile::tempdir().unwrap();
        let data = b"\0\0\0\x18ftypisom\0\0\0\0actual-video";
        let source = directory.path().join("lesson.mp4");
        std::fs::write(&source, data).unwrap();
        let imported = ipc(
            &app,
            "import_material_files",
            json!({"paths":[source.to_string_lossy()]}),
        )
        .unwrap();
        let id = imported[0]["material"]["id"].as_i64().unwrap();
        assert_eq!(
            ipc(&app, "get_app_state", json!({})).unwrap()["resources"][0]["type"],
            "视频"
        );
        let info = ipc(&app, "get_material_media_info", json!({"id":id})).unwrap();
        assert_eq!(info["total"], data.len());
        assert_eq!(info["mime"], "video/mp4");
        assert_eq!(info["sha256"], imported[0]["material"]["sha256"]);
        // 46#2：chunk 走二进制应答，字节原样到达；total/mime 由 info() 推导
        let chunk = ipc_raw(
            &app,
            "read_material_media_chunk",
            json!({"id":id,"offset":4,"length":8}),
        )
        .unwrap();
        assert_eq!(chunk, data[4..12].to_vec());
        assert_eq!(
            ipc(
                &app,
                "read_material_media_chunk",
                json!({"id":id,"offset":data.len() as u64 + 1,"length":8})
            )
            .unwrap_err()["code"],
            "invalid_media_range"
        );
        // 46#2：传输完成后 Rust 侧整文件复核通过
        ipc(&app, "verify_material_media", json!({"id":id})).unwrap();
        assert_eq!(
            ipc(&app, "enqueue_material_parse", json!({"id":id})).unwrap_err()["code"],
            "media_analysis_unsupported"
        );
        let fake = directory.path().join("fake.mp4");
        std::fs::write(&fake, b"not a video").unwrap();
        let rejected = ipc(
            &app,
            "import_material_files",
            json!({"paths":[fake.to_string_lossy()]}),
        )
        .unwrap();
        assert_eq!(rejected[0]["error"]["code"], "invalid_media_format");
        assert_eq!(
            ipc(&app, "list_materials", json!({}))
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
        // 64#7：拒绝路径——同长度字节替换绕过文件名，verify 必须按哈希
        // 拒绝（media_integrity_failed），篡改内容不得以旧身份进预览
        let original = app
            .state::<BackendState>()
            .materials
            .test_database_path()
            .parent()
            .unwrap()
            .join("materials")
            .join("originals")
            .join(format!(
                "{}.mp4",
                imported[0]["material"]["sha256"].as_str().unwrap()
            ));
        let tampered: Vec<u8> = data.iter().map(|byte| byte.wrapping_add(1)).collect();
        std::fs::write(&original, &tampered).unwrap();
        assert_eq!(
            ipc(&app, "verify_material_media", json!({"id":id})).unwrap_err()["code"],
            "media_integrity_failed"
        );
        // info 在传输前流式校验——同样拒绝（chunk 本体只做范围检查，
        // 完整性由 info/verify 两端把守）
        assert_eq!(
            ipc(&app, "get_material_media_info", json!({"id":id})).unwrap_err()["code"],
            "media_integrity_failed"
        );
    }

    #[test]
    fn native_archive_import_validates_before_staging_real_entries() {
        let app = test_app();
        let directory = tempfile::tempdir().unwrap();
        let write_zip = |path: &std::path::Path, entries: Vec<(&str, &[u8])>, deflate: bool| {
            let mut writer = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
            let options = zip::write::SimpleFileOptions::default().compression_method(if deflate {
                zip::CompressionMethod::Deflated
            } else {
                zip::CompressionMethod::Stored
            });
            for (name, content) in entries {
                writer.start_file(name, options).unwrap();
                std::io::Write::write_all(&mut writer, content).unwrap();
            }
            writer.finish().unwrap();
        };
        let path = directory.path().join("materials.zip");
        write_zip(
            &path,
            vec![
                ("chapter.txt", "真实资料正文".as_bytes()),
                ("lesson.mp3", b"ID3actual-audio"),
                ("ignored.bin", b"ignored"),
            ],
            false,
        );
        let result = ipc(
            &app,
            "import_material_archive",
            json!({"path":path.to_string_lossy()}),
        )
        .unwrap();
        assert_eq!(result["outcomes"].as_array().unwrap().len(), 2);
        assert_eq!(result["skipped"], json!(["ignored.bin"]));
        let resources = ipc(&app, "get_app_state", json!({})).unwrap()["resources"].clone();
        assert_eq!(resources.as_array().unwrap().len(), 2);
        assert_eq!(resources[1]["type"], "音频");
        let media = ipc(&app, "get_material_media_info", json!({"id":2})).unwrap();
        assert_eq!(media["mime"], "audio/mpeg");
        assert_eq!(media["total"], b"ID3actual-audio".len());
        ipc(&app, "enqueue_material_parse", json!({"id":1})).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        loop {
            let detail = ipc(&app, "get_material", json!({"id":1})).unwrap();
            if detail["status"] == "parsed" {
                assert_eq!(detail["text"], "真实资料正文");
                break;
            }
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let repeated = ipc(
            &app,
            "import_material_archive",
            json!({"path":path.to_string_lossy()}),
        )
        .unwrap();
        assert!(repeated["outcomes"]
            .as_array()
            .unwrap()
            .iter()
            .all(|outcome| outcome["deduplicated"] == true));
        let before = ipc(&app, "get_app_state", json!({})).unwrap();
        let unsafe_path = directory.path().join("unsafe.zip");
        write_zip(
            &unsafe_path,
            vec![
                ("../escape.txt", b"not allowed"),
                ("valid.txt", b"also not staged"),
            ],
            false,
        );
        assert_eq!(
            ipc(
                &app,
                "import_material_archive",
                json!({"path":unsafe_path.to_string_lossy()})
            )
            .unwrap_err()["code"],
            "unsafe_archive_path"
        );
        let nested = directory.path().join("nested.zip");
        write_zip(&nested, vec![("nested.zip", b"nested")], false);
        assert_eq!(
            ipc(
                &app,
                "import_material_archive",
                json!({"path":nested.to_string_lossy()})
            )
            .unwrap_err()["code"],
            "nested_archive_blocked"
        );
        let bomb = directory.path().join("bomb.zip");
        let large = vec![b'a'; 1024 * 1024];
        write_zip(&bomb, vec![("large.txt", large.as_slice())], true);
        assert_eq!(
            ipc(
                &app,
                "import_material_archive",
                json!({"path":bomb.to_string_lossy()})
            )
            .unwrap_err()["code"],
            "archive_entry_too_large"
        );
        let invalid = directory.path().join("invalid.zip");
        std::fs::write(&invalid, b"not a zip").unwrap();
        assert_eq!(
            ipc(
                &app,
                "import_material_archive",
                json!({"path":invalid.to_string_lossy()})
            )
            .unwrap_err()["code"],
            "invalid_archive"
        );
        let after = ipc(&app, "get_app_state", json!({})).unwrap();
        assert_eq!(after["resources"], before["resources"]);
        assert_eq!(after["lastEventId"], before["lastEventId"]);
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
        // 72#2：reveal 权限不再授予前端——资源管理器定位经后端 reveal_path
        // 命令白名单（数据根 + 本进程导出回执）校验，被注入脚本无法定位
        // 本机任意路径
        assert!(
            !permissions.contains(&json!("opener:allow-reveal-item-in-dir")),
            "reveal permission must not be granted to the webview"
        );
        // open-url 以带 scope 的对象条目授权：仅放行 https://**，
        // 不授予任意协议/scheme 的打开能力
        let open_url = permissions
            .iter()
            .find(|entry| {
                entry.get("identifier").and_then(Value::as_str) == Some("opener:allow-open-url")
            })
            .expect("missing opener:allow-open-url entry");
        assert_eq!(
            open_url["allow"][0]["url"],
            json!("https://**"),
            "open-url scope must be limited to https"
        );
    }
}
