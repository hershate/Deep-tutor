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

/// P-001：桥接提示的载体——记录提示描述的是哪个数据根。
/// 提示断言的是「该路径下是我们新建的空工作区」；装配到别的根、或该
/// 工作区已有用户数据（用户把原数据恢复到该目录），前提都不再成立，
/// 提示须重新求值后清除。
#[derive(Debug, Clone)]
struct StartupNotice {
    root: PathBuf,
    message: String,
}

pub struct StartupState {
    default_root: PathBuf,
    pointer: PathBuf,
    // 152#4：root 需在重试成功后回写——改 Mutex 以支持运行中更新。
    // P-014：进程内换根只在「尚未注册绑定状态」时被放行（此时 assemble
    // 以新 root 建库，data_root()/overview 不得滞留旧目录）；已注册状态
    // 绑定旧根时换根被拒（storage_root_changed，要求重启应用）
    root: Mutex<PathBuf>,
    pending_root: Mutex<Option<PathBuf>>,
    failure: Mutex<Option<BackendError>>,
    running: std::sync::atomic::AtomicBool,
    // 106#9：configured 指向的目录在启动时缺失（移走/误删叶子）——按
    // 「目录即真相」语义原样新建空工作区，但须给用户桥接提示而非
    // 让「数据似乎全没了」无解释
    notice: Mutex<Option<StartupNotice>>,
    // P-014：已注册状态（安全库 / BackendState 各域）绑定的数据根——
    // 进程内换根会让域状态与安全库分属两个目录（双目录混装），据此
    // 拒绝就地换库并要求重启
    bound_root: Mutex<Option<PathBuf>>,
    // P-131：106#8 的系统目录解析失败态——此态下重试会以进程当前工作
    // 目录装配完整工作区（与 106#8 注释「该场景重试也解析不出」矛盾），
    // 置位后重试直接返回类型化错误
    paths_unresolved: std::sync::atomic::AtomicBool,
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
    notice: Option<String>,
    /// P-044/P-014：待生效目录是否只能在重启应用后生效——已注册状态绑定
    /// 旧根时进程内换根被拒（storage_root_changed，见
    /// retry_storage_initialization），界面须据此改写文案，不得承诺
    /// 「点重试即按新目录重装配」
    pending_requires_restart: bool,
}

impl StartupState {
    /// 当前应用数据根目录：导出等写盘命令用它做第二道路径遏制（08#2）
    /// 152#4：返回克隆（root 为可回写的 Mutex 字段）
    pub fn data_root(&self) -> std::path::PathBuf {
        self.root
            .lock()
            .map(|root| root.clone())
            .unwrap_or_else(|poisoned| poisoned.into_inner().clone())
    }

    /// 重试成功后回写实际装配的根目录（152#4）
    fn set_root(&self, next: PathBuf) {
        match self.root.lock() {
            Ok(mut root) => *root = next,
            Err(poisoned) => *poisoned.into_inner() = next,
        }
    }

    fn current_root(&self) -> PathBuf {
        self.root
            .lock()
            .map(|root| root.clone())
            .unwrap_or_else(|poisoned| poisoned.into_inner().clone())
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
            root: Mutex::new(root.to_path_buf()),
            default_root: default_root.to_path_buf(),
            pointer: root.join("storage-location.json"),
            pending_root: Mutex::new(None),
            failure: Mutex::new(None),
            running: std::sync::atomic::AtomicBool::new(false),
            notice: Mutex::new(None),
            bound_root: Mutex::new(None),
            paths_unresolved: std::sync::atomic::AtomicBool::new(false),
        }
    }

    /// P-014：已注册状态绑定的数据根（首次注册时确定，此后不变）
    fn bound_root(&self) -> Option<PathBuf> {
        self.bound_root.lock().ok().and_then(|root| root.clone())
    }

    /// P-014：首次绑定数据根——已绑定后不再改写（换根须重启）
    fn bind_root(&self, root: PathBuf) {
        match self.bound_root.lock() {
            Ok(mut bound) => {
                if bound.is_none() {
                    *bound = Some(root);
                }
            }
            Err(poisoned) => {
                let mut bound = poisoned.into_inner();
                if bound.is_none() {
                    *bound = Some(root);
                }
            }
        }
    }

    /// P-001：清除「已按缺失路径新建空工作区」提示
    fn clear_notice(&self) {
        if let Ok(mut notice) = self.notice.lock() {
            *notice = None;
        }
    }

    /// P-001：读取当前桥接提示（供概览输出）
    fn notice_message(&self) -> Option<String> {
        self.notice
            .lock()
            .ok()
            .and_then(|notice| notice.as_ref().map(|notice| notice.message.clone()))
    }

    /// P-001：重估桥接提示的前提——提示断言的是「该路径下是我们新建的空
    /// 工作区」。两种情况下前提不再成立，提示须失效：
    /// 1) 装配到了别的根——提示描述的目录已不是当前工作区；
    /// 2) 当前根下的工作区已有用户数据——用户把原数据恢复回了该目录，
    ///    「空工作区」的说法已不成立。
    /// 判据取自工作区本身（是否存在用户数据），不依赖用户是否点过按钮。
    fn reevaluate_notice(&self, root: &Path, workspace_has_data: bool) {
        let mut notice = match self.notice.lock() {
            Ok(notice) => notice,
            Err(poisoned) => poisoned.into_inner(),
        };
        let Some(current) = notice.as_ref() else {
            return;
        };
        if current.root != root || workspace_has_data {
            *notice = None;
        }
    }

    /// P-001：写入桥接提示（该根下的工作区是新建的空工作区）
    fn set_notice(&self, root: &Path, message: String) {
        if let Ok(mut notice) = self.notice.lock() {
            *notice = Some(StartupNotice {
                root: root.to_path_buf(),
                message,
            });
        }
    }

    /// P-131：系统目录解析失败态（106#8）——重试无法解析出真实目录
    fn paths_unresolved(&self) -> bool {
        self.paths_unresolved
            .load(std::sync::atomic::Ordering::Acquire)
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

/// 127#12：进程崩溃在指针写中途残留的 storage-location.{旧pid}.tmp——
/// 新进程 pid 不同永不触碰（save 只覆盖自己 pid 的 tmp），逐次崩溃累积；
/// 清扫同目录全部 *.{pid 数字}.tmp（调用点自身 tmp 尚未创建/已 rename）
fn sweep_pointer_tmps(pointer: &Path) {
    let Some(directory) = pointer.parent() else {
        return;
    };
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if let Some(stem) = name.strip_suffix(".tmp") {
            if let Some(pid_part) = stem.rsplit('.').next() {
                if !pid_part.is_empty() && pid_part.bytes().all(|byte| byte.is_ascii_digit()) {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
    }
}

pub fn save_location(pointer: &Path, selected: &Path) -> Result<PathBuf> {
    let _lock = LOCATION_LOCK
        .lock()
        .map_err(|_| BackendError::new("storage_config_unavailable", "数据目录配置锁不可用"))?;
    sweep_pointer_tmps(pointer);
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
            root: Mutex::new(root.to_path_buf()),
            default_root: default_root.to_path_buf(),
            pointer: pointer.to_path_buf(),
            pending_root: Mutex::new(None),
            failure: Mutex::new(None),
            running: std::sync::atomic::AtomicBool::new(false),
            notice: Mutex::new(None),
            bound_root: Mutex::new(None),
            paths_unresolved: std::sync::atomic::AtomicBool::new(false),
        });
    }
    // 152#4：State 已存在（重试路径）时同步刷新 root——data_root()/overview
    // 不得滞留旧目录。
    // P-014：外部改指针后的进程内换根已被 retry_storage_initialization
    // 拒绝（storage_root_changed，要求重启）；此处仅在「尚未注册绑定状态」
    // 的重试路径上以新 root 装配。
    else {
        app.state::<StartupState>().set_root(root.to_path_buf());
    }
}

/// P-001：装配后的工作区是否已有用户数据——桥接提示断言的是「该路径下是
/// 我们新建的空工作区」；用户把原数据放回该目录后该前提不再成立，
/// 提示须随之失效（否则用户被告知「数据全没了」而数据其实就在库里）。
/// 数据在用户工作区库（users/<id>/），故读该库而非根库。
fn workspace_has_user_data<R: Runtime>(app: &AppHandle<R>) -> bool {
    let Some(backend) = app.try_state::<BackendState>() else {
        return false;
    };
    let Ok(store) = backend.store.lock() else {
        return false;
    };
    store.has_user_data().unwrap_or(false)
}

/// 106#9：配置指向的目录在启动时缺失（移动/误删叶子）——inspect 对
/// NotFound 放行并由 prepare_directory 原路径新建空工作区；此处记录
/// 提示供 overview/设置页展示，避免「数据全没了」的无解释空库
fn note_missing_directory<R: Runtime>(app: &AppHandle<R>, root: &Path, pointer: &Path) {
    if !pointer.exists() || root.exists() || !root.is_absolute() {
        // 无配置文件 = 首次运行走默认目录，新建是正常路径，不提示
        return;
    }
    let message = format!(
        "配置的数据目录不存在，已按该路径新建了空工作区：{}。若你刚移动过数据目录，请在「设置 → 隐私、备份与数据」中重新选择正确位置，避免在新空库中重复建数据。",
        root.display()
    );
    if let Some(state) = app.try_state::<StartupState>() {
        state.set_notice(root, message);
    }
}

/// 106#8：GUI 装配前的路径解析失败转失败恢复 UI——manage 一个携带 failure
/// 的 StartupState，窗口打开错误页而非 builder.run().expect 在无控制台的
/// release 构建下无声闪退；根路径用当前目录占位（该场景重试也解析不出，
/// 错误页的价值是给出可见错误，真实出路是修复系统已知文件夹配置）
/// P-131：置位 paths_unresolved——占位根是进程当前工作目录，重试若照常
/// 装配会在该目录建出完整工作区（与上述注释直接矛盾），须显式拒绝
pub fn present_startup_failure<R: Runtime>(app: &AppHandle<R>, message: &str) {
    let fallback = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    prepare(
        app,
        &fallback,
        &fallback,
        &fallback.join("storage-location.json"),
    );
    let state = app.state::<StartupState>();
    state
        .paths_unresolved
        .store(true, std::sync::atomic::Ordering::Release);
    state.set_failure(Some(BackendError::new(
        "storage_config_unavailable",
        message,
    )));
}

pub fn initialize<R: Runtime>(
    app: &AppHandle<R>,
    root: &Path,
    default_root: &Path,
    pointer: &Path,
    configuration_error: Option<BackendError>,
) {
    prepare(app, root, default_root, pointer);
    // 127#12：启动清扫崩溃残留的指针 tmp（save 只清理自己 pid 的）
    sweep_pointer_tmps(pointer);
    // 111#6：缺失探测收敛进 initialize 本体——原先只在冷启动的
    // initialize_background 探测，重试初始化重建目录后第二次起提示缺失；
    // 与 GUI 路径同点（prepare 之后、assemble 建目录之前）
    note_missing_directory(app, root, pointer);
    let state = app.state::<StartupState>();
    state.set_failure(None);
    state
        .running
        .store(true, std::sync::atomic::Ordering::Release);
    let result = match configuration_error {
        Some(error) => Err(error),
        None => assemble(app, root),
    };
    let succeeded = result.is_ok();
    state.set_failure(result.err());
    // 127#12：清除「已按缺失路径新建」提示的条件是「存在待生效目录」——
    // 原先无条件清除，使「目录缺失→新建空工作区」这条毫秒级正常路径上的
    // 桥接提示被抹掉，用户面对全空工作区没有任何解释（P-001）
    if succeeded {
        let pending = state.pending().ok().flatten();
        if pending.is_some() {
            state.clear_notice();
        } else {
            // P-001：成功重试后重新求值桥接提示——提示断言的是「该路径下是
            // 我们新建的空工作区」；用户把原数据恢复回该目录后工作区已有数据，
            // 前提不再成立，旧提示须清除（否则用户被告知「数据全没了」而数据
            // 其实就在库里）
            state.reevaluate_notice(root, workspace_has_user_data(app));
        }
        // P-044：待生效目录已在本轮装配中生效——不再声称「下次启动使用」
        if pending.as_deref() == Some(root) {
            let _ = state.set_pending(None);
        }
    }
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
    // 106#9 的缺失探测已随 111#6 收敛进 initialize 本体（重试路径同享）
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
    // 127#10：时区偏移在启动单线程期预热一次——探测此前全部发生在
    // 命令/线程池上下文，Unix 多线程健全性检查失败时 UTC 被静默缓存
    let _ = crate::backend::store::now_local();
    let path = root.join("deep-tutor.db");
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
    // P-014：安全库与工作区状态绑定同一数据根，且只在库校验通过后才注册
    // ——原先在开库之前就注册安全库，库损坏/目录不可用时状态已绑死在打不开
    // 的目录上，进程内换目录重试会留下「安全库在旧目录、工作区在新目录」的
    // 混装；注册即记录绑定根，供重试换根时拒绝就地换库
    if app.try_state::<security::SecurityState>().is_none() {
        app.manage(security::SecurityState::new(&path));
        app.state::<StartupState>().bind_root(root.to_path_buf());
    }
    // 127#16：重试路径沿用已注册实例（不再依赖 Tauri2 二次 manage 的
    // no-op 语义）——目录不变由 P-014 的绑定根校验保证、路径一致，下方
    // initialize_session 与 recover 族照常对旧实例重跑（首次失败发生在
    // recover 中段时，retry 需要补跑未完成的域）
    if app.try_state::<BackendState>().is_none() {
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
        app.state::<StartupState>().bind_root(root.to_path_buf());
    }
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
        data_root: state.current_root(),
        default_data_root: state.default_root.clone(),
        pending_data_root: state.pending()?,
        ready: !state.running()
            && state.failure()?.is_none()
            && app.try_state::<BackendState>().is_some(),
        initializing: state.running(),
        error: state.failure()?,
        notice: state.notice_message(),
        // 待生效目录与已绑定数据根不一致时，进程内换根会被
        // retry_storage_initialization 拒绝（storage_root_changed），
        // 只能重启应用后在新目录启动——口径与该守卫一致
        pending_requires_restart: match (state.pending()?, state.bound_root()) {
            (Some(pending), Some(bound)) => pending != bound,
            _ => false,
        },
    })
}

pub mod commands {
    use super::*;

    #[tauri::command]
    pub fn get_storage_overview<R: Runtime>(app: AppHandle<R>) -> Result<StartupOverview> {
        overview(&app)
    }

    // 106#4：重试初始化原先为同步命令——主线程直跑全量 assemble（含
    // user1 整库 fs::copy 迁移 + 15 域 migrate + 12 域 recover），慢盘大库
    // 下窗口冻结并被 Windows 标「未响应」，且无 catch_unwind（assemble
    // panic 直接杀进程）。与 initialize_background 同口径：后台线程 +
    // catch_unwind 兜底转失败恢复 UI
    #[tauri::command(async)]
    pub async fn retry_storage_initialization<R: Runtime + 'static>(
        app: AppHandle<R>,
    ) -> Result<StartupOverview> {
        let (root, default_root, pointer, configuration_error) = {
            let state = app.state::<StartupState>();
            // P-131：系统目录解析失败态（106#8）下根路径只是进程当前工作
            // 目录的占位——照常装配会在该目录建出完整工作区，与 106#8 注释
            // 「该场景重试也解析不出」直接矛盾
            if state.paths_unresolved() {
                return Err(BackendError::new(
                    "storage_path_unresolved",
                    "无法定位应用的系统目录，重试无法解决；请修复系统的「已知文件夹」配置后重新启动应用",
                ));
            }
            // P-044：pending 不再永久短路重试——保存目录后重试必须按新目录
            // 真正重装配（决策 20「保存后重试或下次启动使用新目录」）；原先
            // 该守卫使重试在进程生命周期内恒为 no-op，保存成功后的「稍后
            // 重试」做不到，还把修复前的旧错误原样抛回
            if state.running()
                || (state.failure()?.is_none() && app.try_state::<BackendState>().is_some())
            {
                return overview(&app);
            }
            let configured = configured_directory(&state.pointer, &state.default_root);
            let root = configured.clone().unwrap_or_else(|_| state.current_root());
            // P-014：已注册状态绑定旧数据根时不能就地换库——各域状态自持
            // 旧 database_path（materials/learning 等），强行换根会形成双
            // 目录混装且全程无提示。改为类型化错误要求重启
            if let Some(bound) = state.bound_root() {
                if bound != root {
                    return Err(BackendError::new(
                        "storage_root_changed",
                        "数据目录已变更，需要重启应用后在新目录启动；当前进程仍使用原目录",
                    ));
                }
            }
            // 152#3：守卫通过即在命令线程同步置位 running——原先在
            // spawn_blocking 闭包内才置位，两次并发 retry 都能通过守卫
            // 形成双路 assemble（对照 initialize_background 的同步置位）
            state
                .running
                .store(true, std::sync::atomic::Ordering::Release);
            (
                root,
                state.default_root.clone(),
                state.pointer.clone(),
                configured.err(),
            )
        };
        let background_app = app.clone();
        tauri::async_runtime::spawn_blocking(move || {
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
        .await
        .map_err(|_| BackendError::new("startup_unavailable", "无法启动本地后端初始化，请重试"))?;
        overview(&app)
    }

    // 106#4：目录保存含 Store::root 打开校验与整目录写——原先同步命令在
    // 主线程执行慢盘 IO；移后台（LOCATION_LOCK 与 StartupState 均可跨线程）
    #[tauri::command(async)]
    pub async fn set_storage_location<R: Runtime + 'static>(
        app: AppHandle<R>,
        path: String,
    ) -> Result<StartupOverview> {
        let pointer = app.state::<StartupState>().pointer.clone();
        let selected =
            tauri::async_runtime::spawn_blocking(move || save_location(&pointer, Path::new(&path)))
                .await
                .map_err(|_| {
                    BackendError::new("startup_unavailable", "数据目录保存任务异常终止")
                })??;
        let state = app.state::<StartupState>();
        state.set_pending(Some(selected))?;
        // P-001/127#12：用户已重新选择目录——「已按缺失路径新建空工作区」
        // 的旧提示随之失效（pending 文案取代之，二者并存互相矛盾）
        state.clear_notice();
        overview(&app)
    }

    // 127#4：移后台——reset_location 抢 LOCATION_LOCK，与 106#4 移后台的
    // save_location 是同一把锁（持锁方做 prepare_directory + Store::root
    // 完整建库迁移，慢盘秒级）；同步命令在主线程 lock() 会冻结窗口直到
    // 后台释放，恰好复现 106#4 要消除的病症
    #[tauri::command(async)]
    pub async fn reset_storage_location<R: Runtime + 'static>(
        app: AppHandle<R>,
    ) -> Result<StartupOverview> {
        tauri::async_runtime::spawn_blocking(move || {
            use tauri::Manager;
            let state = app.state::<StartupState>();
            reset_location(&state.pointer)?;
            let pending =
                (state.current_root() != state.default_root).then(|| state.default_root.clone());
            state.set_pending(pending.clone())?;
            // P-001/127#12：恢复默认目录后旧目录的「已按缺失路径新建」提示
            // 失效（pending 文案取代之）
            if pending.is_some() {
                state.clear_notice();
            }
            overview(&app)
        })
        .await
        .map_err(|_| {
            crate::backend::error::BackendError::new("internal_error", "目录重置任务执行异常终止")
        })?
    }

    #[tauri::command]
    pub fn restart_application<R: Runtime>(app: AppHandle<R>) {
        // 106#3：标记经继承环境传给子进程——新进程在 acquire 里对
        // ALREADY_EXISTS 做 2s 重试退避，而非立即聚焦垂死旧实例后自杀
        std::env::set_var("DEEPTUTOR_RESTARTING", "1");
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
        mock_app_with(
            root,
            default_root,
            pointer,
            configuration_error,
            security::SecurityState::for_tests_at(&root.join("deep-tutor.db")),
        )
    }

    // 103#8：可携带既有 SecurityState（凭据存储跨「重启」复用，模拟生产
    // keyring 的进程间持久性）
    fn mock_app_with(
        root: &Path,
        default_root: &Path,
        pointer: &Path,
        configuration_error: Option<BackendError>,
        state: security::SecurityState,
    ) -> tauri::App<tauri::test::MockRuntime> {
        let app = tauri::test::mock_builder()
            .manage(state)
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
        let default_credentials = app.state::<security::SecurityState>().credentials_handle();
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

        // 103#8：生产中 keyring 随进程持久——「重启回原目录」携带原凭据
        // 存储；不携带会被判为会话槽丢失（须显式重登）
        let app = mock_app_with(
            &default_root,
            &default_root,
            &pointer,
            None,
            security::SecurityState::for_tests_at_with(
                &default_root.join("deep-tutor.db"),
                default_credentials,
            ),
        );
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

    // P-001：目录缺失→按该路径重建空工作区属毫秒级正常路径，桥接提示必须
    // 活到界面（原先装配成功后无条件清除，用户面对全空工作区无任何解释）
    #[test]
    fn a_missing_configured_directory_keeps_its_bridge_notice_after_assembly() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let selected = temporary.path().join("selected");
        let pointer = temporary.path().join("configuration/storage.json");
        // 指针指向随后被移走的目录——启动时按该路径重建空工作区
        save_location(&pointer, &selected).unwrap();
        fs::remove_dir_all(&selected).unwrap();
        let app = mock_app(&selected, &default_root, &pointer, None);
        let overview = ipc(&app, "get_storage_overview", json!({})).unwrap();
        assert_eq!(overview["ready"], true);
        let notice = overview["notice"].as_str().unwrap_or_default();
        assert!(
            notice.contains("已按该路径新建了空工作区"),
            "目录缺失的桥接提示不得在装配成功后被清除：{notice}"
        );
    }

    // P-044：启动失败后保存新目录，「重试初始化」必须按新目录真正重装配
    // （原先 pending 守卫使重试在进程生命周期内恒为 no-op）
    #[test]
    fn a_saved_directory_is_applied_by_the_retry_after_a_failed_startup() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let selected = temporary.path().join("selected");
        let pointer = temporary.path().join("configuration/storage.json");
        fs::create_dir_all(pointer.parent().unwrap()).unwrap();
        fs::write(&pointer, b"broken configuration").unwrap();
        let error = configured_directory(&pointer, &default_root).unwrap_err();
        // 生产拓扑下安全库随装配目录注册（assemble 内），此处预置到重试目标
        // 目录以复现同一绑定关系
        let app = mock_app_with(
            &default_root,
            &default_root,
            &pointer,
            Some(error),
            security::SecurityState::for_tests_at(&selected.join("deep-tutor.db")),
        );
        assert_eq!(
            ipc(&app, "bootstrap", json!({})).unwrap_err()["code"],
            "storage_config_invalid"
        );
        let saved = ipc(
            &app,
            "set_storage_location",
            json!({"path": selected.to_string_lossy()}),
        )
        .unwrap();
        assert_eq!(saved["pendingDataRoot"], json!(selected));
        // 本进程尚未注册绑定状态（失败发生在状态注册之前）——重试可按新目录
        // 立即重装配，概览不得标记为「只能重启后生效」
        assert_eq!(saved["pendingRequiresRestart"], false);
        let recovered = ipc(&app, "retry_storage_initialization", json!({})).unwrap();
        assert_eq!(recovered["ready"], true);
        assert_eq!(recovered["dataRoot"], json!(selected));
        // 新目录已在本轮装配生效——不再声称「下次启动使用」
        assert_eq!(recovered["pendingDataRoot"], json!(null));
        assert!(selected.join("users/1/deep-tutor.db").is_file());
    }

    // P-014：外部改写目录指针后的进程内重试不得就地换库——已注册域状态
    // 自持旧 database_path，换根会形成双目录混装（用户在新目录看不到旧
    // 目录的域数据且全程无提示）
    #[test]
    fn a_changed_pointer_does_not_switch_the_assembled_workspace_in_process() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let other = temporary.path().join("other");
        let pointer = temporary.path().join("configuration/storage.json");
        let app = mock_app(&default_root, &default_root, &pointer, None);
        assert_eq!(
            ipc(&app, "get_storage_overview", json!({})).unwrap()["ready"],
            true
        );
        // 模拟装配之后的失败（此前装配失败发生在 BackendState 注册之后）
        app.state::<StartupState>()
            .set_failure(Some(BackendError::new(
                "database_corrupt",
                "本地数据库损坏",
            )));
        save_location(&pointer, &other).unwrap();
        let error = ipc(&app, "retry_storage_initialization", json!({})).unwrap_err();
        assert_eq!(error["code"], "storage_root_changed");
        // 新目录不得被就地装配——工作区仍绑定原目录
        assert!(!other.join("users/1/deep-tutor.db").exists());
        assert_eq!(
            ipc(&app, "get_storage_overview", json!({})).unwrap()["dataRoot"],
            json!(default_root)
        );
    }

    // P-131：系统目录解析失败态下重试不得在进程当前工作目录装配完整工作区
    // （106#8 注释已声明该场景重试也解析不出）
    #[test]
    fn a_path_resolution_failure_refuses_to_assemble_in_the_current_directory() {
        let temporary = tempfile::tempdir().unwrap();
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![
                commands::get_storage_overview,
                commands::retry_storage_initialization,
            ])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();
        tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .unwrap();
        // 占位根取进程当前工作目录——重试若照常装配会在该目录建出工作区；
        // 切到临时目录避免回归时污染仓库（工程测试串行执行）
        let previous = std::env::current_dir().unwrap();
        std::env::set_current_dir(temporary.path()).unwrap();
        present_startup_failure(app.handle(), "无法定位应用数据目录");
        let error = ipc(&app, "retry_storage_initialization", json!({})).unwrap_err();
        std::env::set_current_dir(previous).unwrap();
        assert_eq!(error["code"], "storage_path_unresolved");
        assert!(app.try_state::<BackendState>().is_none());
        assert!(!temporary.path().join("deep-tutor.db").exists());
    }

    // P-001：提示断言的是「该路径下是我们新建的空工作区」——用户把原数据
    // 恢复回该目录、重试装配成功后该前提不再成立，旧提示须被清除（否则
    // 用户被告知「数据全没了」而数据其实就在库里）
    #[test]
    fn a_restored_workspace_clears_the_bridge_notice_after_a_successful_retry() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let selected = temporary.path().join("selected");
        let pointer = temporary.path().join("configuration/storage.json");
        // 指针指向随后被移走的目录——启动时按该路径重建空工作区
        save_location(&pointer, &selected).unwrap();
        fs::remove_dir_all(&selected).unwrap();
        let app = mock_app(&selected, &default_root, &pointer, None);
        let overview = ipc(&app, "get_storage_overview", json!({})).unwrap();
        assert_eq!(overview["ready"], true);
        assert!(overview["notice"]
            .as_str()
            .unwrap_or_default()
            .contains("已按该路径新建了空工作区"));
        // 用户把原数据恢复回该目录（此处以写入真实学习数据等效）
        assert!(ipc(&app, "bootstrap", json!({})).is_ok());
        ipc(
            &app,
            "replace_collection",
            json!({"namespace":"notes","expectedRevision":0,"value":note()}),
        )
        .unwrap();
        // 装配之后的失败——重试走完整装配，并在成功后重新求值提示
        app.state::<StartupState>()
            .set_failure(Some(BackendError::new(
                "database_corrupt",
                "本地数据库损坏",
            )));
        let recovered = ipc(&app, "retry_storage_initialization", json!({})).unwrap();
        assert_eq!(recovered["ready"], true);
        assert_eq!(
            recovered["notice"],
            json!(null),
            "工作区已有用户数据时，「已按该路径新建了空工作区」的提示须失效"
        );
    }

    // P-044/P-014：待生效目录与本进程已绑定的数据根不一致时，进程内换根
    // 会被 storage_root_changed 拒绝——概览须如实标记「只能重启后生效」，
    // 界面据此改写文案，不得一律承诺「点重试即按新目录重装配」
    #[test]
    fn the_overview_marks_a_pending_directory_that_only_a_restart_can_apply() {
        let temporary = tempfile::tempdir().unwrap();
        let default_root = temporary.path().join("default");
        let other = temporary.path().join("other");
        let pointer = temporary.path().join("configuration/storage.json");
        let app = mock_app(&default_root, &default_root, &pointer, None);
        // 应用已运行（工作区已绑定 default_root）——待生效目录只能下次启动生效
        let saved = ipc(
            &app,
            "set_storage_location",
            json!({"path": other.to_string_lossy()}),
        )
        .unwrap();
        assert_eq!(saved["pendingDataRoot"], json!(other));
        assert_eq!(saved["pendingRequiresRestart"], true);
        assert_eq!(
            ipc(&app, "get_storage_overview", json!({})).unwrap()["pendingRequiresRestart"],
            true
        );
    }
}
