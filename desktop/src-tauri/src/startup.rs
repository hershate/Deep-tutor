use crate::backend::error::{BackendError, Result};
use std::fs;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataDirectory {
    Existing,
    Missing,
}

pub fn inspect_data_directory(path: &Path) -> Result<DataDirectory> {
    match fs::metadata(path) {
        Ok(metadata) if metadata.is_dir() => Ok(DataDirectory::Existing),
        Ok(_) => Err(BackendError::new(
            "invalid_data_directory",
            "本地学习数据位置已被文件占用，请移动该文件后重新打开应用",
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(DataDirectory::Missing),
        Err(_) => Err(BackendError::new(
            "data_directory_unavailable",
            "无法检查本地学习数据目录，请检查磁盘和目录权限",
        )),
    }
}

pub fn prepare_data_directory(path: &Path, confirm: impl FnOnce() -> bool) -> Result<bool> {
    match inspect_data_directory(path)? {
        DataDirectory::Existing => Ok(true),
        DataDirectory::Missing if !confirm() => Ok(false),
        DataDirectory::Missing => {
            fs::create_dir_all(path).map_err(|_| {
                BackendError::new(
                    "data_directory_unavailable",
                    "无法创建本地学习数据目录，请检查磁盘空间和目录权限",
                )
            })?;
            if !fs::metadata(path)
                .map(|metadata| metadata.is_dir())
                .unwrap_or(false)
            {
                return Err(BackendError::new(
                    "data_directory_unavailable",
                    "本地学习数据目录创建后仍不可用，请检查目录权限",
                ));
            }
            Ok(true)
        }
    }
}

pub fn launch<R: Runtime + 'static>(
    app: &AppHandle<R>,
    root: PathBuf,
    initialize: fn(&AppHandle<R>, &Path) -> Result<()>,
) -> Result<()> {
    match inspect_data_directory(&root)? {
        DataDirectory::Existing => {
            initialize(app, &root.join("deep-tutor.db"))?;
            show_main_window(app)
        }
        DataDirectory::Missing => {
            let app = app.clone();
            std::thread::Builder::new()
                .name("first-launch-consent".into())
                .spawn(move || {
                    let dialog_app = app.clone();
                    let dialog_root = root.clone();
                    let decision = move || {
                        dialog_app
                            .dialog()
                            .message(format!(
                                "知舟需要在本机保存学习记录、笔记和设置。\n\n是否创建本地学习数据目录？\n{}\n\n选择“退出”不会创建目录或文件。",
                                dialog_root.display()
                            ))
                            .title("创建本地学习数据目录")
                            .kind(MessageDialogKind::Info)
                            .buttons(MessageDialogButtons::OkCancelCustom(
                                "创建并继续".into(),
                                "退出".into(),
                            ))
                            .blocking_show()
                    };
                    match prepare_data_directory(&root, decision) {
                        Ok(false) => app.exit(0),
                        Ok(true) => {
                            if let Err(error) = initialize(&app, &root.join("deep-tutor.db"))
                                .and_then(|()| show_main_window(&app))
                            {
                                fail_startup(&app, error);
                            }
                        }
                        Err(error) => fail_startup(&app, error),
                    }
                })
                .map_err(|_| {
                    BackendError::new("startup_unavailable", "无法打开首次启动确认，请重新打开应用")
                })?;
            Ok(())
        }
    }
}

fn show_main_window<R: Runtime>(app: &AppHandle<R>) -> Result<()> {
    app.get_webview_window("main")
        .ok_or_else(|| BackendError::new("window_unavailable", "应用主窗口不可用，请重新打开应用"))?
        .show()
        .map_err(|_| BackendError::new("window_unavailable", "无法显示应用主窗口，请重新打开应用"))
}

pub fn fail_startup<R: Runtime + 'static>(app: &AppHandle<R>, error: BackendError) {
    let app = app.clone();
    let _ = std::thread::Builder::new()
        .name("startup-error".into())
        .spawn(move || {
            app.dialog()
                .message(format!(
                    "{}\n\n错误代码：{}\n请检查本地数据目录和系统密钥库后重新打开应用。",
                    error.message, error.code
                ))
                .title("知舟无法打开本地学习数据")
                .kind(MessageDialogKind::Error)
                .buttons(MessageDialogButtons::OkCustom("退出".into()))
                .blocking_show();
            app.exit(1);
        });
}

#[cfg(test)]
mod tests {
    use super::{inspect_data_directory, prepare_data_directory, DataDirectory};
    use std::fs;

    #[test]
    fn declining_a_missing_data_directory_creates_nothing() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("learning-data");
        assert_eq!(
            inspect_data_directory(&root).unwrap(),
            DataDirectory::Missing
        );
        assert!(!prepare_data_directory(&root, || false).unwrap());
        assert!(!root.exists());
        assert_eq!(fs::read_dir(temporary.path()).unwrap().count(), 0);
    }

    #[test]
    fn consent_creates_only_the_approved_directory() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("learning-data");
        assert!(prepare_data_directory(&root, || true).unwrap());
        assert!(root.is_dir());
        assert!(!root.join("deep-tutor.db").exists());
        assert_eq!(
            inspect_data_directory(&root).unwrap(),
            DataDirectory::Existing
        );
    }

    #[test]
    fn an_existing_directory_does_not_need_consent() {
        let temporary = tempfile::tempdir().unwrap();
        assert!(prepare_data_directory(temporary.path(), || {
            panic!("an existing directory must not trigger another prompt")
        })
        .unwrap());
    }

    #[test]
    fn a_file_or_unusable_parent_is_an_explicit_error() {
        let temporary = tempfile::tempdir().unwrap();
        let file = temporary.path().join("not-a-directory");
        fs::write(&file, "keep").unwrap();
        assert_eq!(
            inspect_data_directory(&file).unwrap_err().code,
            "invalid_data_directory"
        );
        assert_eq!(
            prepare_data_directory(&file.join("child"), || true)
                .unwrap_err()
                .code,
            "data_directory_unavailable"
        );
        assert_eq!(fs::read_to_string(file).unwrap(), "keep");
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
        assert_eq!(error.code, "storage_failed");

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
