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
    // A-18：Windows 记事本等外部工具保存的 md 常带 BOM（U+FEFF）与 CRLF——
    // BOM 使 frontmatter 前缀匹配失效并把 FEFF 带进正文，CRLF 使逐行键
    // 匹配失效；先统一规范化再解析
    let mut content = text.trim_start_matches('\u{feff}').replace("\r\n", "\n");
    let mut tag = "复盘".to_string();
    // 11#17：外部工具常见的 YAML frontmatter（--- 包围块，title/tag 字段）。
    // 此前只识别自家导出头，外部 Markdown 的元数据会混进正文。
    if let Some(rest) = content.strip_prefix("---\n") {
        if let Some(end) = rest.find("\n---") {
            let block = &rest[..end];
            let body = rest[end + 4..].trim_start_matches('\n');
            // B-7：只有块内确实命中元数据键才按 frontmatter 剥离——正文以
            // --- 分隔线开头的正常 Markdown（块内无 title:/tag:）不得丢段。
            // 31#28：进一步要求块内每行均为 key: value 形态——「分隔线之间的
            // 正文恰含 title:/tag: 行」时原先整块剥掉，首段无告警丢失
            let frontmatter_shape = !block.is_empty()
                && block.lines().all(|line| {
                    let line = line.trim();
                    line.is_empty()
                        || line.split_once(':').is_some_and(|(key, _)| {
                            !key.is_empty()
                                && key.chars().all(|character| {
                                    character.is_ascii_lowercase() || character == '-'
                                })
                        })
                });
            let mut matched = false;
            for line in block.lines() {
                if let Some(value) = line.strip_prefix("title:") {
                    let cleaned = value.trim().trim_matches('"').trim();
                    if !cleaned.is_empty() {
                        title = cleaned.to_string();
                        matched = true;
                    }
                }
                if let Some(value) = line
                    .strip_prefix("tags:")
                    .or_else(|| line.strip_prefix("tag:"))
                {
                    let cleaned = value
                        .trim()
                        .trim_start_matches('[')
                        .trim_end_matches(']')
                        .split([',', '，'])
                        .next()
                        .unwrap_or_default()
                        .trim()
                        .trim_matches('"')
                        .trim();
                    if !cleaned.is_empty() {
                        tag = cleaned.to_string();
                        matched = true;
                    }
                }
            }
            if matched && frontmatter_shape {
                content = body.to_string();
            }
        }
    }
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
    // 11#16：落库前的真实约束是“序列化后记录 ≤ 1 MB”（含转义膨胀）；
    // 在读取侧预检并把具体文件名带进错误，避免整批 ZIP 导入到最后一步才失败
    let serialized = serde_json::to_string(&content)
        .map_err(|_| invalid("invalid_note_content", "笔记正文无法序列化"))?
        .len()
        + serde_json::to_string(&title)
            .map(|value| value.len())
            .unwrap_or_default()
        + tag.len()
        + 512;
    if serialized > crate::backend::store::MAX_RECORD_BYTES {
        return Err(BackendError::new(
            "record_too_large",
            &format!("笔记「{file_title}」超过单条 1 MB 上限（含 JSON 转义后约 {serialized} 字节），请拆分后重试"),
        ));
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
            "size_limit_exceeded",
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
                "size_limit_exceeded",
                "单条笔记超过 1 MB 或压缩比超过安全上限",
            ));
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| BackendError::new("size_limit_exceeded", "笔记解压总量无效"))?;
        if total > MAX_FILE_BYTES {
            return Err(BackendError::new(
                "size_limit_exceeded",
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
                "size_limit_exceeded",
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
            "size_limit_exceeded",
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
    // 与学习批注写入口一致使用 UTC（+00:00）格式，避免同一字段混用本地偏移表示（07#8）
    let updated = OffsetDateTime::now_utc().format(&Rfc3339)?;
    let tx = guard.conn.unchecked_transaction()?;
    // 25#18：配额计数与插入同事务，关闭 check-then-insert 并发窗口
    guard.ensure_quota_in_tx(&tx, "notes", notes.len() as u64)?;
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
