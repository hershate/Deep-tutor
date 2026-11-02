use serde::Serialize;

#[derive(Debug, Serialize)]
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
    fn from(_: rusqlite::Error) -> Self {
        Self::storage()
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
