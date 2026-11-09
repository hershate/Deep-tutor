use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct BackendError {
    pub code: String,
    pub message: String,
}

impl BackendError {
    pub fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
        }
    }

    pub fn invalid(code: &str, message: &str) -> Self {
        Self::new(code, message)
    }

    fn storage() -> Self {
        Self::new(
            "storage_failed",
            "无法读写本地数据，请检查应用数据目录后重试",
        )
    }
}

impl From<rusqlite::Error> for BackendError {
    fn from(error: rusqlite::Error) -> Self {
        match error {
            rusqlite::Error::SqliteFailure(code, _)
                if matches!(
                    code.code,
                    rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked
                ) =>
            {
                Self::new("database_busy", "数据库正忙，请稍后重试")
            }
            // 86#3：库损坏/非库文件独立错误码——原先与目录只读、磁盘满共用
            // storage_failed 通用文案，用户无法区分「换目录+导入备份」与
            // 「检查目录权限」两条自救路径（SQLITE_CORRUPT=11、NOTADB=26）
            rusqlite::Error::SqliteFailure(code, _)
                if code.extended_code == 26 || code.extended_code & 0xFF == 11 =>
            {
                Self::new(
                    "database_corrupt",
                    "本地数据库文件已损坏；请换一个空目录重启应用，并通过全量备份导入恢复数据",
                )
            }
            _ => Self::storage(),
        }
    }
}

impl From<std::io::Error> for BackendError {
    fn from(_: std::io::Error) -> Self {
        Self::storage()
    }
}

impl From<serde_json::Error> for BackendError {
    fn from(_: serde_json::Error) -> Self {
        Self::new("invalid_record", "数据格式不符合应用约定")
    }
}

impl From<zip::result::ZipError> for BackendError {
    fn from(_: zip::result::ZipError) -> Self {
        Self::new("export_failed", "无法生成有效的压缩归档")
    }
}

impl From<time::error::Format> for BackendError {
    fn from(_: time::error::Format) -> Self {
        Self::storage()
    }
}

impl std::fmt::Display for BackendError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for BackendError {}

pub type Result<T> = std::result::Result<T, BackendError>;
