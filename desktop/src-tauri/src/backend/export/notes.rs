use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::learning::LearningState;
use super::super::materials;
use super::super::store::Store;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::Path;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NotesArchiveResult {
    pub bytes: usize,
    pub sha256: String,
    pub count: usize,
}

fn file_name(note: &Value) -> Result<String> {
    let id = note
        .get("id")
        .and_then(Value::as_i64)
        .ok_or_else(|| BackendError::new("invalid_record", "笔记缺少有效标识"))?;
    let title = note
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("未命名笔记")
        .chars()
        .map(|character| {
            if "\\/:*?\"<>|\r\n".contains(character) {
                '_'
            } else {
                character
            }
        })
        .take(80)
        .collect::<String>();
    Ok(format!("知舟笔记/{id:03}-{title}.md"))
}

pub fn archive<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    destination: &Path,
) -> Result<NotesArchiveResult> {
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case("zip"))
    {
        return Err(BackendError::invalid(
            "invalid_export_destination",
            "笔记归档必须使用 .zip 扩展名",
        ));
    }
    let notes = state.store()?.collection("notes")?.value;
    let notes = notes
        .as_array()
        .ok_or_else(|| BackendError::new("invalid_record", "笔记集合格式无效"))?;
    if notes.is_empty() {
        return Err(BackendError::new("empty_export", "还没有可导出的笔记"));
    }
    let mut bytes = Vec::new();
    {
        let mut archive = zip::ZipWriter::new(std::io::Cursor::new(&mut bytes));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for note in notes {
            let title = note
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("未命名笔记");
            let tag = note.get("tag").and_then(Value::as_str).unwrap_or("");
            let updated = note.get("updated").and_then(Value::as_str).unwrap_or("");
            let content = note.get("content").and_then(Value::as_str).unwrap_or("");
            archive.start_file(file_name(note)?, options)?;
            archive.write_all(
                format!("# {title}\n\n标签：{tag}\n更新时间：{updated}\n\n{content}").as_bytes(),
            )?;
        }
        archive.start_file("manifest.json", options)?;
        archive.write_all(json!({"version": 1, "count": notes.len(), "exportedAt": OffsetDateTime::now_utc().format(&Rfc3339)?}).to_string().as_bytes())?;
        archive.finish()?;
    }
    if bytes.len() > super::MAX_EXPORT_BYTES {
        return Err(BackendError::new(
            "export_size_invalid",
            "笔记归档超过 64 MB 上限",
        ));
    }
    materials::atomic_export(destination, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let event = Store::event(
        &tx,
        "content.exported",
        json!({"format": "zip", "bytes": bytes.len(), "sha256": sha256, "count": notes.len()}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(NotesArchiveResult {
        bytes: bytes.len(),
        sha256,
        count: notes.len(),
    })
}
