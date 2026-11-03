pub mod commands;
pub mod library;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use super::BackendState;
use serde::Serialize;
use serde_json::{json, Value};
use std::io::{Cursor, Read};
use std::path::Path;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_NOTE_BYTES: u64 = 1024 * 1024;
const MAX_ENTRIES: usize = 1000;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NotesImportResult {
    pub state: Value,
    pub imported: usize,
}

struct NoteSource {
    title: String,
    content: String,
    tag: String,
}

fn invalid(code: &str, message: &str) -> BackendError {
    BackendError::invalid(code, message)
}

fn truncate(value: &str, maximum: usize) -> String {
    let mut end = value.len().min(maximum);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].trim().to_string()
}

fn note_source(file_title: &str, text: String) -> Result<NoteSource> {
    let mut title = file_title.to_string();
    let mut content = text;
    let mut tag = "复盘".to_string();
    if let Some(rest) = content.strip_prefix("# ") {
        if let Some((heading, rest)) = rest.split_once("\n\n标签：") {
            if let Some((metadata_tag, rest)) = rest.split_once("\n更新时间：") {
                if let Some((_updated, body)) = rest.split_once("\n\n") {
                    if !heading.trim().is_empty() && !metadata_tag.trim().is_empty() {
                        title = heading.to_string();
                        tag = metadata_tag.to_string();
                        content = body.to_string();
                    }
                }
            }
        }
    }
    if content.trim().is_empty() {
        return Err(invalid("empty_note_content", "笔记正文不能为空"));
    }
    if content.contains('\0') {
        return Err(invalid("invalid_note_content", "笔记正文不能包含 NUL 字符"));
    }
    let title = truncate(title.trim(), 500);
    if title.is_empty() {
        return Err(invalid("invalid_note_title", "笔记标题不能为空"));
    }
    Ok(NoteSource {
        title,
        content,
        tag: truncate(&tag, 100),
    })
}

fn read_archive(bytes: &[u8]) -> Result<Vec<NoteSource>> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| invalid("invalid_notes_archive", "笔记压缩包结构无效"))?;
    if archive.len() > MAX_ENTRIES {
        return Err(BackendError::new(
            "quota_exceeded",
            "笔记压缩包最多包含 1000 个条目",
        ));
    }
    let mut notes = Vec::new();
    let mut total = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|_| invalid("invalid_notes_archive", "无法读取笔记压缩包条目"))?;
        let path = entry
            .enclosed_name()
            .ok_or_else(|| invalid("unsafe_archive_path", "压缩包包含不安全的文件路径"))?;
        if entry.is_dir() {
            continue;
        }
        let extension = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !["md", "txt"].contains(&extension.as_str()) {
            continue;
        }
        if entry.size() > MAX_NOTE_BYTES
            || entry.size() > entry.compressed_size().max(1).saturating_mul(200)
        {
            return Err(BackendError::new(
                "quota_exceeded",
                "单条笔记超过 1 MB 或压缩比超过安全上限",
            ));
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| BackendError::new("quota_exceeded", "笔记解压总量无效"))?;
        if total > MAX_FILE_BYTES {
            return Err(BackendError::new(
                "quota_exceeded",
                "笔记解压总量超过 64 MB 上限",
            ));
        }
        let title = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| invalid("invalid_note_title", "笔记文件名不是有效 UTF-8"))?
            .to_string();
        let mut bytes = Vec::new();
        entry
            .by_ref()
            .take(MAX_NOTE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| {
                invalid(
                    "invalid_notes_archive",
                    "压缩包条目损坏或使用了不支持的加密",
                )
            })?;
        if bytes.len() as u64 > MAX_NOTE_BYTES {
            return Err(BackendError::new(
                "quota_exceeded",
                "单条笔记超过 1 MB 上限",
            ));
        }
        let text = String::from_utf8(bytes).map_err(|_| {
            invalid(
                "invalid_note_encoding",
                "Markdown 与 TXT 笔记必须使用 UTF-8 编码",
            )
        })?;
        notes.push(note_source(&title, text)?);
    }
    if notes.is_empty() {
        return Err(invalid(
            "empty_notes_import",
            "压缩包中没有可导入的 Markdown 或 TXT 笔记",
        ));
    }
    Ok(notes)
}

fn read_notes(path: &Path) -> Result<Vec<NoteSource>> {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !["zip", "md", "txt"].contains(&extension.as_str()) {
        return Err(invalid(
            "invalid_note_format",
            "仅支持 ZIP、Markdown 和 TXT 笔记",
        ));
    }
    let metadata = std::fs::metadata(path)
        .map_err(|_| invalid("import_read_failed", "无法读取所选笔记文件"))?;
    if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
        return Err(BackendError::new(
            "quota_exceeded",
            "笔记文件必须是普通文件且不超过 64 MB",
        ));
    }
    let bytes =
        std::fs::read(path).map_err(|_| invalid("import_read_failed", "无法读取所选笔记文件"))?;
    if extension == "zip" {
        return read_archive(&bytes);
    }
    let title = path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or_else(|| invalid("invalid_note_title", "笔记文件名不是有效 UTF-8"))?;
    let text = String::from_utf8(bytes).map_err(|_| {
        invalid(
            "invalid_note_encoding",
            "Markdown 与 TXT 笔记必须使用 UTF-8 编码",
        )
    })?;
    Ok(vec![note_source(title, text)?])
}

pub fn import<R: Runtime>(
    app: &AppHandle<R>,
    state: &BackendState,
    path: &Path,
) -> Result<NotesImportResult> {
    let notes = read_notes(path)?;
    let guard = state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))?;
    guard.ensure_quota("notes", notes.len() as u64)?;
    let updated = OffsetDateTime::now_local()
        .unwrap_or_else(|_| OffsetDateTime::now_utc())
        .format(&Rfc3339)?;
    let tx = guard.conn.unchecked_transaction()?;
    for source in &notes {
        let id = Store::next_free_id(&tx, "notes")?;
        let note = json!({
            "id": id.parse::<i64>().map_err(|_| BackendError::new("invalid_id", "笔记标识无效"))?,
            "title": source.title,
            "excerpt": source.content.chars().take(90).collect::<String>(),
            "tag": source.tag,
            "updated": updated,
            "content": source.content,
        });
        super::store::validate_collection("notes", &Value::Array(vec![note.clone()]))?;
        Store::insert_json(&tx, "notes", &id, &note)?;
    }
    let revision = Store::next_revision(&tx, "notes")?;
    let event = Store::event(
        &tx,
        "state.changed",
        json!({"namespace":"notes","revision":revision,"imported":notes.len()}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(NotesImportResult {
        state: guard.app_state()?,
        imported: notes.len(),
    })
}
