use super::super::error::{BackendError, Result};
use super::{ImportOutcome, MaterialState, MAX_FILE_BYTES};
use serde::Serialize;
use std::io::{Cursor, Read};
use std::path::Path;
use tauri::{AppHandle, Runtime};

const MAX_ENTRIES: usize = 64;
const MAX_TOTAL_BYTES: u64 = 200 * 1024 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveImportResult {
    pub outcomes: Vec<ImportOutcome>,
    pub skipped: Vec<String>,
    pub total_bytes: u64,
}

fn read_archive(path: &Path) -> Result<(Vec<(String, String, Vec<u8>)>, Vec<String>, u64)> {
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|value| !value.eq_ignore_ascii_case("zip"))
    {
        return Err(BackendError::invalid(
            "invalid_archive_format",
            "资料包必须使用 .zip 扩展名",
        ));
    }
    let source = std::fs::canonicalize(path)
        .map_err(|_| BackendError::new("not_found", "资料包不存在或不可读"))?;
    let metadata = source.metadata()?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_FILE_BYTES {
        return Err(BackendError::new(
            "invalid_archive_size",
            "资料包必须是普通文件，大小为 1 到 50 MB",
        ));
    }
    let bytes = std::fs::read(source)?;
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| BackendError::invalid("invalid_archive", "ZIP 资料包结构无效"))?;
    if archive.len() > MAX_ENTRIES {
        return Err(BackendError::new(
            "archive_too_many_entries",
            "资料包最多包含 64 个文件",
        ));
    }
    let mut entries = Vec::new();
    let mut skipped = Vec::new();
    let mut total = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|_| BackendError::invalid("invalid_archive_entry", "无法读取资料包条目"))?;
        let path = entry.enclosed_name().ok_or_else(|| {
            BackendError::invalid("unsafe_archive_path", "资料包包含不安全的文件路径")
        })?;
        if entry.is_dir() {
            continue;
        }
        let title = path
            .file_name()
            .and_then(|value| value.to_str())
            .filter(|name| !name.trim().is_empty() && name.len() <= 500)
            .ok_or_else(|| BackendError::invalid("invalid_archive_entry", "资料包文件名无效"))?
            .to_string();
        let kind = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if kind == "zip" {
            return Err(BackendError::new(
                "nested_archive_blocked",
                "不支持嵌套 ZIP 资料包",
            ));
        }
        if !super::SUPPORTED.contains(&kind.as_str()) {
            skipped.push(title);
            continue;
        }
        if entry.size() > MAX_FILE_BYTES
            || entry.size() > entry.compressed_size().max(1).saturating_mul(200)
        {
            return Err(BackendError::new(
                "archive_entry_too_large",
                "资料包条目超过 50 MB 或压缩比超过 200 倍",
            ));
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| BackendError::new("archive_too_large", "资料包解压总量无效"))?;
        if total > MAX_TOTAL_BYTES {
            return Err(BackendError::new(
                "archive_too_large",
                "资料包解压总量超过 200 MB 上限",
            ));
        }
        let mut bytes = Vec::new();
        entry
            .by_ref()
            .take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| {
                BackendError::invalid("invalid_archive_entry", "资料包条目损坏、加密或读取失败")
            })?;
        if bytes.len() as u64 != entry.size() || bytes.len() as u64 > MAX_FILE_BYTES {
            return Err(BackendError::invalid(
                "invalid_archive_entry",
                "资料包条目实际大小与目录不符",
            ));
        }
        super::validate_content(&kind, &bytes)?;
        entries.push((title, kind, bytes));
    }
    if entries.is_empty() {
        return Err(BackendError::new(
            "empty_archive",
            "资料包没有可导入的文档、音视频或图片",
        ));
    }
    Ok((entries, skipped, total))
}

pub fn import_archive<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    path: &Path,
) -> Result<ArchiveImportResult> {
    let (entries, skipped, total_bytes) = read_archive(path)?;
    let mut outcomes = Vec::new();
    for (title, kind, bytes) in entries {
        match super::stage_bytes(app, state, title.clone(), kind, bytes) {
            Ok(result) => outcomes.push(ImportOutcome {
                file_name: result.material.title.clone(),
                material: Some(result.material),
                deduplicated: result.deduplicated,
                error: None,
            }),
            Err(error) => outcomes.push(ImportOutcome {
                file_name: title,
                material: None,
                deduplicated: false,
                error: Some(error),
            }),
        }
    }
    Ok(ArchiveImportResult {
        outcomes,
        skipped,
        total_bytes,
    })
}
