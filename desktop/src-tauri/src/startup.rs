use crate::backend::{
    agent, community,
    error::{BackendError, Result},
    knowledge, learning, materials, plugins, practice, projects, providers, reports, review,
    security, sharing,
    store::Store,
    sync, BackendState,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::{AppHandle, Manager, Runtime};

static LOCATION_LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredLocation {
    version: u32,
    data_root: PathBuf,
}

pub struct StartupState {
    root: PathBuf,
    default_root: PathBuf,
    pointer: PathBuf,
    pending_root: Mutex<Option<PathBuf>>,
    failure: Mutex<Option<BackendError>>,
    running: std::sync::atomic::AtomicBool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupOverview {
    data_root: PathBuf,
    default_data_root: PathBuf,
    pending_data_root: Option<PathBuf>,
    ready: bool,
    initializing: bool,
    error: Option<BackendError>,
}

impl StartupState {
    /// 当前应用数据根目录：导出等写盘命令用它做第二道路径遏制（08#2）
    pub fn data_root(&self) -> &std::path::Path {
        &self.root
    }

    /// 默认数据根目录（未迁移时的位置）：迁移后旧默认目录仍存历史数据，
    /// 导出遏制须同时拦截（65#5）
    pub fn default_data_root(&self) -> &std::path::Path {
        &self.default_root
    }

    fn failure(&self) -> Result<Option<BackendError>> {
        self.failure
            .lock()
            .map(|failure| failure.clone())
            .map_err(|_| BackendError::new("startup_unavailable", "无法读取本地后端初始化状态"))
    }

    // 65#5 测试：直接构造指定 root/default_root 的状态，不触发目录装配
    #[cfg(test)]
    pub(crate) fn for_tests(root: &Path, default_root: &Path) -> Self {
        Self {
            root: root.to_path_buf(),
            default_root: default_root.to_path_buf(),
            pointer: root.join("storage-location.json"),
            pending_root: Mutex::new(None),
            failure: Mutex::new(None),
            running: std::sync::atomic::AtomicBool::new(false),
        }
    }

    fn set_failure(&self, next: Option<BackendError>) {
        if let Ok(mut failure) = self.failure.lock() {
            *failure = next;
        }
    }

    fn running(&self) -> bool {
        self.running.load(std::sync::atomic::Ordering::Acquire)
    }

    fn pending(&self) -> Result<Option<PathBuf>> {
        self.pending_root
            .lock()
            .map(|pending| pending.clone())
            .map_err(|_| BackendError::new("startup_unavailable", "无法读取待生效的数据目录"))
    }

    fn set_pending(&self, root: Option<PathBuf>) -> Result<()> {
        *self
            .pending_root
            .lock()
            .map_err(|_| BackendError::new("startup_unavailable", "无法保存待生效的数据目录"))? =
            root;
        Ok(())
    }
}

fn invalid_location(message: &str) -> BackendError {
    BackendError::new("invalid_data_directory", message)
}

fn validate_location(path: &Path) -> Result<()> {
    if !path.is_absolute() || path.parent().is_none() {
        return Err(invalid_location(
            "数据目录必须是磁盘上的绝对目录，不能使用磁盘根目录",
        ));
    }
    if path
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err(invalid_location("数据目录不能包含 .. 路径段"));
    }
    #[cfg(windows)]
    {
        if path.components().any(|component| {
            matches!(
                component,
                std::path::Component::Prefix(prefix)
                    if matches!(
                        prefix.kind(),
                        std::path::Prefix::UNC(..) | std::path::Prefix::VerbatimUNC(..)
                    )
            )
        }) {
            return Err(invalid_location(
                "数据目录必须位于本机磁盘，不能使用网络共享目录",
            ));
        }
        // 25#18：`\\?\C:\Windows`（VerbatimDisk）与 `C:\Windows`（Disk）的
        // 组件前缀不同，Path::starts_with 恒 false 会绕过系统目录黑名单——
        // 先剥掉 verbatim 前缀再比较
        let candidate = {
            let text = path.to_string_lossy().to_lowercase();
            text.strip_prefix(r"\\?\")
                .map(str::to_string)
                .unwrap_or(text)
        };
        for name in [
            "SystemRoot",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "ProgramW6432",
        ] {
            if let Some(root) = std::env::var_os(name) {
                let root = root.to_string_lossy().to_lowercase();
                if Path::new(&candidate).starts_with(Path::new(&root)) {
                    return Err(invalid_location("数据目录不能位于 Windows 或程序安装目录"));
                }
            }
        }
    }
    Ok(())
}

fn inspect_location(path: &Path) -> Result<()> {
    match fs::metadata(path) {
        Ok(metadata) if metadata.is_dir() => Ok(()),
        Ok(_) => Err(invalid_location(
            "本地学习数据位置已被文件占用，请选择另一个目录",
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(BackendError::new(
            "data_directory_unavailable",
            "无法检查本地学习数据目录，请检查磁盘和目录权限",
        )),
    }
}

pub fn prepare_directory(path: &Path) -> Result<()> {
    validate_location(path)?;
    inspect_location(path)?;
    if !path.is_dir() {
        fs::create_dir_all(path).map_err(|_| {
            BackendError::new(
                "data_directory_unavailable",
                "无法创建本地学习数据目录，请检查磁盘空间和目录权限",
            )
        })?;
    }
    inspect_location(path)
}

pub fn configured_directory(pointer: &Path, default_root: &Path) -> Result<PathBuf> {
    let bytes = match fs::read(pointer) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(default_root.to_path_buf())
        }
        Err(_) => {
            return Err(BackendError::new(
                "storage_config_unavailable",
                "无法读取数据目录配置，请检查配置目录权限或恢复默认目录",
            ))
        }
    };
    if bytes.len() > 4096 {
        return Err(BackendError::new(
            "storage_config_invalid",
            "数据目录配置无效，请恢复默认目录或重新选择",
        ));
    }
    let stored: StoredLocation = serde_json::from_slice(&bytes).map_err(|_| {
        BackendError::new(
            "storage_config_invalid",
            "数据目录配置无效，请恢复默认目录或重新选择",
        )
    })?;
    if stored.version != 1 {
        return Err(BackendError::new(
            "storage_config_invalid",
            "数据目录配置版本不受支持，请重新选择目录",
        ));
    }
    validate_location(&stored.data_root)?;
    inspect_location(&stored.data_root)?;
    Ok(stored.data_root)
}

pub fn save_location(pointer: &Path, selected: &Path) -> Result<PathBuf> {
    let _lock = LOCATION_LOCK
        .lock()
        .map_err(|_| BackendError::new("storage_config_unavailable", "数据目录配置锁不可用"))?;
    prepare_directory(selected)?;
    Store::root(selected.join("deep-tutor.db"))?;
    let parent = pointer
        .parent()
        .ok_or_else(|| BackendError::new("storage_config_unavailable", "数据目录配置位置不可用"))?;
    fs::create_dir_all(parent)
        .map_err(|_| BackendError::new("storage_config_unavailable", "无法创建数据目录配置位置"))?;
    let contents = serde_json::to_vec(&StoredLocation {
        version: 1,
        data_root: selected.to_path_buf(),
    })
    .map_err(|_| invalid_location("数据目录包含无法保存的路径字符"))?;
    let temporary = pointer.with_extension(format!("{}.tmp", std::process::id()));
    let write = (|| -> Result<()> {
        let mut file = fs::File::create(&temporary)
            .map_err(|_| BackendError::new("storage_config_unavailable", "无法写入数据目录配置"))?;
        file.write_all(&contents)
            .map_err(|_| BackendError::new("storage_config_unavailable", "无法写入数据目录配置"))?;
        file.sync_all()
            .map_err(|_| BackendError::new("storage_config_unavailable", "无法保存数据目录配置"))?;
        fs::rename(&temporary, pointer)
            .map_err(|_| BackendError::new("storage_config_unavailable", "无法替换数据目录配置"))?;
        Ok(())
    })();
    if write.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write?;
    Ok(selected.to_path_buf())
}

pub fn reset_location(pointer: &Path) -> Result<()> {
    let _lock = LOCATION_LOCK
        .lock()
        .map_err(|_| BackendError::new("storage_config_unavailable", "数据目录配置锁不可用"))?;
    match fs::remove_file(pointer) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(BackendError::new(
            "storage_config_unavailable",
            "无法恢复默认数据目录，请检查配置目录权限",
        )),
    }
}

fn prepare<R: Runtime>(app: &AppHandle<R>, root: &Path, default_root: &Path, pointer: &Path) {
    if app.try_state::<StartupState>().is_none() {
        app.manage(StartupState {
            root: root.to_path_buf(),
            default_root: default_root.to_path_buf(),
            pointer: pointer.to_path_buf(),
            pending_root: Mutex::new(None),
            failure: Mutex::new(None),
            running: std::sync::atomic::AtomicBool::new(false),
        });
    }
}

pub fn initialize<R: Runtime>(
    app: &AppHandle<R>,
    root: &Path,
    default_root: &Path,
    pointer: &Path,
    configuration_error: Option<BackendError>,
) {
    prepare(app, root, default_root, pointer);
    let state = app.state::<StartupState>();
    state.set_failure(None);
    state
        .running
        .store(true, std::sync::atomic::Ordering::Release);
    let result = match configuration_error {
        Some(error) => Err(error),
        None => assemble(app, root),
    };
    state.set_failure(result.err());
    state
        .running
        .store(false, std::sync::atomic::Ordering::Release);
}

pub fn initialize_background<R: Runtime + 'static>(
    app: &AppHandle<R>,
    root: &Path,
    default_root: &Path,
    pointer: &Path,
    configuration_error: Option<BackendError>,
) {
    prepare(app, root, default_root, pointer);
    app.state::<StartupState>()
        .running
        .store(true, std::sync::atomic::Ordering::Release);
    let background_app = app.clone();
    let root = root.to_path_buf();
    let default_root = default_root.to_path_buf();
    let pointer = pointer.to_path_buf();
    if std::thread::Builder::new()
        .name("backend-startup".into())
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                initialize(
                    &background_app,
                    &root,
                    &default_root,
                    &pointer,
                    configuration_error,
                );
            }));
            if result.is_err() {
                let state = background_app.state::<StartupState>();
                state.set_failure(Some(BackendError::new(
                    "startup_failed",
                    "本地后端初始化中断，请重试或修改数据目录",
                )));
                state
                    .running
                    .store(false, std::sync::atomic::Ordering::Release);
            }
        })
        .is_err()
    {
        let state = app.state::<StartupState>();
        state.set_failure(Some(BackendError::new(
            "startup_unavailable",
            "无法启动本地后端初始化，请重试",
        )));
        state
            .running
            .store(false, std::sync::atomic::Ordering::Release);
    }
}

fn assemble<R: Runtime>(app: &AppHandle<R>, root: &Path) -> Result<()> {
    let path = root.join("deep-tutor.db");
    app.manage(security::SecurityState::new(&path));
    prepare_directory(root)?;
    // 22#1：装配先于会话初始化、无 principal——显式以 user1 打开引导库
    // （含旧版单库向 user1 工作区的迁移），不再依赖 Store::new 的隐式回退；
    // 首条受守卫命令经 prepare_store 切到实际登录用户
    let store = Store::at_user(&path, 1)?;
    let material_state = materials::MaterialState::new(&path)?;
    let provider_state = providers::ProviderState::new(&path)?;
    let learning_state = learning::LearningState::new(&path, &provider_state, &material_state);
    let knowledge_state = knowledge::KnowledgeState::new(&path, &provider_state, &material_state);
    let agent_state = agent::AgentState::new(&path, &provider_state, &learning_state);
    let practice_state = practice::PracticeState::new(&path, &provider_state, &learning_state);
    let review_state = review::ReviewState::new(&path, &learning_state);
    let sharing_state = sharing::SharingState::new(&learning_state);
    app.manage(BackendState {
        store: Mutex::new(store),
        materials: material_state,
        providers: provider_state,
        learning: learning_state,
        agent: agent_state,
        practice: practice_state,
        review: review_state,
        knowledge: knowledge_state,
        community: community::CommunityState::new(&path),
        plugins: plugins::PluginState::new(&path)?,
        sharing: sharing_state,
    });
    let principal = match security::initialize_session(app, &app.state::<security::SecurityState>())
    {
        Ok(principal) => Some(principal),
        Err(error) if security::reauthentication_required(&error) => None,
        Err(error) => return Err(error),
    };
    let state = app.state::<BackendState>();
    if let Some(principal) = principal {
        security::with_user(principal.id, || -> Result<()> {
            *state
                .store
                .lock()
                .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))? =
                Store::new(&path)?;
            materials::recover(app, &state.materials)?;
            providers::recover_chats(app, &state.providers)?;
            providers::routing::recover(app, &state.providers)?;
            learning::recover(app, &state.learning)?;
            agent::recover(app, &state.agent)?;
            practice::recover(app, &state.practice)?;
            projects::recover(app, &projects::ProjectState::new(&state.learning))?;
            knowledge::recover(app, &state.knowledge)?;
            sync::recover(app, &sync::SyncService::new(&state.learning))?;
            reports::recover(app, &reports::ReportState::new(&state.learning))?;
            Ok(())
        })?;
        security::with_user(principal.id, || {
            sync::scheduler::start_scheduler(app.clone())
        });
    }
    Ok(())
}

pub fn ensure_ready<R: Runtime>(app: &AppHandle<R>) -> Result<()> {
    let Some(state) = app.try_state::<StartupState>() else {
        return Ok(());
    };
    if let Some(error) = state.failure()? {
        return Err(error);
    }
    if state.running() || app.try_state::<BackendState>().is_none() {
        return Err(BackendError::new(
            "startup_not_ready",
            "本地后端尚未就绪，请等待初始化或重试",
        ));
    }
    Ok(())
}

fn overview<R: Runtime>(app: &AppHandle<R>) -> Result<StartupOverview> {
    let state = app
        .try_state::<StartupState>()
        .ok_or_else(|| BackendError::new("startup_unavailable", "本地启动状态不可用"))?;
    Ok(StartupOverview {
        data_root: state.root.clone(),
        default_data_root: state.default_root.clone(),
        pending_data_root: state.pending()?,
        ready: !state.running()
            && state.failure()?.is_none()
            && app.try_state::<BackendState>().is_some(),
        initializing: state.running(),
        error: state.failure()?,
    })
}

pub mod commands {
    use super::*;

    #[tauri::command]
    pub fn get_storage_overview<R: Runtime>(app: AppHandle<R>) -> Result<StartupOverview> {
        overview(&app)
    }

    #[tauri::command]
    pub fn retry_storage_initialization<R: Runtime>(app: AppHandle<R>) -> Result<StartupOverview> {
        let (root, default_root, pointer, configuration_error) = {
            let state = app.state::<StartupState>();
            if state.running()
                || (state.failure()?.is_none() && app.try_state::<BackendState>().is_some())
                || state.pending()?.is_some()
            {
                return overview(&app);
            }
            let configured = configured_directory(&state.pointer, &state.default_root);
            let root = configured.clone().unwrap_or_else(|_| state.root.clone());
            (
                root,
                state.default_root.clone(),
                state.pointer.clone(),
                configured.err(),
            )
        };
        initialize(&app, &root, &default_root, &pointer, configuration_error);
        overview(&app)
    }

    #[tauri::command]
    pub fn set_storage_location<R: Runtime>(
        app: AppHandle<R>,
        path: String,
    ) -> Result<StartupOverview> {
        let pointer = app.state::<StartupState>().pointer.clone();
        let selected = save_location(&pointer, Path::new(&path))?;
        app.state::<StartupState>().set_pending(Some(selected))?;
        overview(&app)
    }

    #[tauri::command]
    pub fn reset_storage_location<R: Runtime>(app: AppHandle<R>) -> Result<StartupOverview> {
        let state = app.state::<StartupState>();
        reset_location(&state.pointer)?;
        let pending = (state.root != state.default_root).then(|| state.default_root.clone());
        state.set_pending(pending)?;
        overview(&app)
    }

    #[tauri::command]
    pub fn restart_application<R: Runtime>(app: AppHandle<R>) {
        app.restart();
    }
}

#[cfg(test)]
mod tests {
    use super::{configured_directory, prepare_directory, reset_location, save_location};
    use std::fs;

    #[test]
    fn generated_install_names_are_ascii_without_changing_workspace_identity() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert!(config["productName"].as_str().unwrap().is_ascii());
        assert!(config["bundle"]["windows"]["nsis"]["startMenuFolder"]
            .as_str()
            .unwrap()
            .is_ascii());
        assert_eq!(config["identifier"], "com.deeptutor.workspace");
        assert!(config["app"]["windows"][0]["title"]
            .as_str()
            .unwrap()
            .contains("知舟"));
    }

    #[test]
    fn a_missing_default_directory_is_created_without_a_dialog() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("learning-data");
        prepare_directory(&root).unwrap();
        assert!(root.is_dir());
        assert!(!root.join("deep-tutor.db").exists());
    }

    #[test]
    fn a_custom_directory_survives_reload_and_replacement() {
        let temporary = tempfile::tempdir().unwrap();
        let pointer = temporary.path().join("configuration/storage.json");
        let default = temporary.path().join("default-data");
        let first = temporary.path().join("first-data");
        let second = temporary.path().join("second-data");
        save_location(&pointer, &first).unwrap();
        assert_eq!(configured_directory(&pointer, &default).unwrap(), first);
        save_location(&pointer, &second).unwrap();
        assert_eq!(configured_directory(&pointer, &default).unwrap(), second);
        assert!(!default.exists());
    }

    #[test]
    fn invalid_input_does_not_replace_the_saved_directory() {
        let temporary = tempfile::tempdir().unwrap();
        let pointer = temporary.path().join("configuration/storage.json");
        let default = temporary.path().join("default-data");
        let selected = temporary.path().join("selected-data");
        save_location(&pointer, &selected).unwrap();
        let file = temporary.path().join("not-a-directory");
        fs::write(&file, "keep").unwrap();
        assert_eq!(
            save_location(&pointer, &file).unwrap_err().code,
            "invalid_data_directory"
        );
        assert_eq!(configured_directory(&pointer, &default).unwrap(), selected);
        assert_eq!(fs::read_to_string(file).unwrap(), "keep");
    }

    #[test]
    fn reset_only_changes_the_pointer_and_preserves_learning_data() {
        let temporary = tempfile::tempdir().unwrap();
        let pointer = temporary.path().join("configuration/storage.json");
        let default = temporary.path().join("default-data");
        let selected = temporary.path().join("selected-data");
        save_location(&pointer, &selected).unwrap();
        fs::write(selected.join("keep.txt"), "learning data").unwrap();
        reset_location(&pointer).unwrap();
        assert_eq!(configured_directory(&pointer, &default).unwrap(), default);
        assert!(!default.exists());
        assert_eq!(
            fs::read_to_string(selected.join("keep.txt")).unwrap(),
            "learning data"
        );
    }

    #[test]
    fn corrupt_configuration_fails_instead_of_opening_another_workspace() {
        let temporary = tempfile::tempdir().unwrap();
        let pointer = temporary.path().join("configuration/storage.json");
        let default = temporary.path().join("default-data");
        fs::create_dir_all(pointer.parent().unwrap()).unwrap();
        fs::write(&pointer, b"not valid json").unwrap();
        assert_eq!(
            configured_directory(&pointer, &default).unwrap_err().code,
            "storage_config_invalid"
        );
        assert!(!default.exists());
    }

    #[test]
    fn corruption_and_locked_writes_return_typed_errors() {
        let temporary = tempfile::tempdir().unwrap();
        let corrupt = temporary.path().join("corrupt.db");
        fs::write(&corrupt, b"not a sqlite database").unwrap();
        let error = match crate::backend::store::Store::root(&corrupt) {
            Ok(_) => panic!("a corrupt database must not be accepted"),
            Err(error) => error,
        };
        // 86#3：库损坏独立错误码——原先与目录故障共用 storage_failed，
        // 用户无法区分「换目录+导入备份」与「检查目录权限」两条自救路径
        assert_eq!(error.code, "database_corrupt");

        let locked = temporary.path().join("locked.db");
        let store = crate::backend::store::Store::root(&locked).unwrap();
        let blocker = rusqlite::Connection::open(&locked).unwrap();
        blocker
            .execute_batch("PRAGMA busy_timeout = 0; BEGIN IMMEDIATE")
            .unwrap();
        blocker
            .execute(
                "INSERT INTO meta (key, value) VALUES ('lock-test', 'held')",
                [],
            )
            .unwrap();
        let error = match store.replace_collection("notes", 0, &serde_json::json!([])) {
            Ok(_) => panic!("a write-locked database must return its bounded-wait error"),
            Err(error) => error,
        };
        assert_eq!(error.code, "database_busy");
        blocker.execute_batch("ROLLBACK").unwrap();
        drop(store);
    }
}

#[cfg(test)]
mod command_tests {
    use super::*;
    use serde_json::{json, Value};
    use tauri::{
        ipc::{CallbackFn, InvokeBody},
        webview::InvokeRequest,
    };

    fn mock_app(
        root: &Path,
        default_root: &Path,
        pointer: &Path,
        configuration_error: Option<BackendError>,
    ) -> tauri::App<tauri::test::MockRuntime> {
        let app = tauri::test::mock_builder()
            .manage(security::SecurityState::for_tests_at(
                &root.join("deep-tutor.db"),
            ))
            .invoke_handler(security::guard_handler(tauri::generate_handler![
                commands::get_storage_overview,
                commands::retry_storage_initialization,
                commands::set_storage_location,
                commands::reset_storage_location,
                crate::backend::commands::bootstrap,
                crate::backend::commands::replace_collection,
            ]))
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        initialize_background(
            app.handle(),
            root,
            default_root,
            pointer,
            configuration_error,
        );
        for _ in 0..1000 {
            if !app.state::<StartupState>().running() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        assert!(!app.state::<StartupState>().running());
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

    fn note() -> Value {
        json!([{"id":1,"title":"原目录笔记","excerpt":"保留","content":"不自动迁移","tag":"学习","updated":"刚刚"}])
    }

    #[test]
    fn commands_change_the_next_startup_workspace_without_moving_data() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let selected = temporary.path().join("selected");
        let pointer = temporary.path().join("configuration/storage.json");
        let app = mock_app(&default_root, &default_root, &pointer, None);
        assert!(ipc(&app, "bootstrap", json!({})).is_ok());
        ipc(
            &app,
            "replace_collection",
            json!({"namespace":"notes","expectedRevision":0,"value":note()}),
        )
        .unwrap();

        let changed = ipc(
            &app,
            "set_storage_location",
            json!({"path":selected.to_string_lossy()}),
        )
        .unwrap();
        assert_eq!(changed["pendingDataRoot"], json!(selected));
        assert_eq!(
            ipc(&app, "bootstrap", json!({})).unwrap()["notes"][0]["title"],
            "原目录笔记"
        );
        assert!(!selected.join("users/1").exists());
        drop(app);

        let app = mock_app(&selected, &default_root, &pointer, None);
        assert!(ipc(&app, "bootstrap", json!({})).unwrap()["notes"]
            .as_array()
            .unwrap()
            .is_empty());
        let reset = ipc(&app, "reset_storage_location", json!({})).unwrap();
        assert_eq!(reset["pendingDataRoot"], json!(default_root));
        assert!(selected.join("deep-tutor.db").is_file());
        drop(app);

        let app = mock_app(&default_root, &default_root, &pointer, None);
        assert_eq!(
            ipc(&app, "bootstrap", json!({})).unwrap()["notes"][0]["title"],
            "原目录笔记"
        );
    }

    #[test]
    fn startup_failure_is_typed_and_can_be_recovered_without_restarting() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let pointer = temporary.path().join("configuration/storage.json");
        fs::create_dir_all(pointer.parent().unwrap()).unwrap();
        fs::write(&pointer, b"broken configuration").unwrap();
        let error = configured_directory(&pointer, &default_root).unwrap_err();
        let app = mock_app(&default_root, &default_root, &pointer, Some(error));
        assert_eq!(
            ipc(&app, "bootstrap", json!({})).unwrap_err()["code"],
            "storage_config_invalid"
        );
        assert_eq!(
            ipc(&app, "get_storage_overview", json!({})).unwrap()["ready"],
            false
        );
        assert!(!default_root.exists());

        ipc(&app, "reset_storage_location", json!({})).unwrap();
        let recovered = ipc(&app, "retry_storage_initialization", json!({})).unwrap();
        assert_eq!(recovered["ready"], true);
        assert!(ipc(&app, "bootstrap", json!({})).is_ok());
        assert!(default_root.join("users/1/deep-tutor.db").is_file());
    }
}
