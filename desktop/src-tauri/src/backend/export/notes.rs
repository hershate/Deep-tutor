use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::learning::LearningState;
use super::super::materials;
use super::super::store::Store;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{Seek, SeekFrom, Write};
use std::path::Path;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

/// 114#12：64MB 熔断前移到压缩写入途中——原先全量在内存拼完 zip 才查上限，
/// 超限库每次导出都付完整拼接成本后失败。包装 Writer 按写入结束位置判定
/// （zip 收尾对文件头是原位等长回写，不会越过已写高水位），越限立即报错。
/// 117 轮 A1：越限经 exceeded 标志可辨别——io::Error 经 From 映射会丢目标
/// 错误码（storage_failed 误导用户检查数据目录），archive() 据标志改写为
/// export_size_invalid；归档后复查降级为防御性兜底。
struct LimitedWriter<'a> {
    inner: std::io::Cursor<&'a mut Vec<u8>>,
    limit: usize,
    exceeded: &'a std::cell::Cell<bool>,
}

impl Write for LimitedWriter<'_> {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        let position = usize::try_from(self.inner.position()).unwrap_or(usize::MAX);
        if position.saturating_add(buffer.len()) > self.limit {
            self.exceeded.set(true);
            return Err(std::io::Error::other("笔记归档超过 64 MB 上限"));
        }
        self.inner.write(buffer)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl Seek for LimitedWriter<'_> {
    fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
        self.inner.seek(position)
    }
}

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
            // 31#27：Windows 控制字符（0x00-0x1F、0x7F）不在原替换表内，
            // 含控制符的标题会损坏 ZIP 条目名，外部解压工具不可用
            if "\\/:*?\"<>|\r\n".contains(character) || character.is_control() {
                '_'
            } else {
                character
            }
        })
        .take(80)
        .collect::<String>()
        // E-4 小项：尾随空格/句点会被部分 Windows 解压工具再修剪，导致
        // 「导出的文件名」与 manifest/导入侧认知不一致
        .trim_end_matches([' ', '.'])
        .to_string();
    let title = if title.is_empty() {
        "未命名笔记".to_string()
    } else {
        title
    };
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
        return Err(BackendError::new(
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
    // 117 轮 A1：exceeded 标志随构建块捕获——zip 的 io/ZipError 经 From
    // 映射会丢目标错误码，越限时据此改写为 export_size_invalid 而非
    // storage_failed/export_failed（自救指引指向「缩减笔记量」而非
    // 「检查数据目录」）
    let exceeded = std::cell::Cell::new(false);
    let build: std::result::Result<(), BackendError> = (|| {
        let mut archive = zip::ZipWriter::new(LimitedWriter {
            inner: std::io::Cursor::new(&mut bytes),
            limit: super::MAX_EXPORT_BYTES,
            exceeded: &exceeded,
        });
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for note in notes {
            // E-4：单文件格式的元数据以换行哨兵串分隔——标题/标签含换行时
            // 再导入会提前命中哨兵导致标题截断、tag 吸收残余；导出侧把
            // 换行折叠为空格，保证元数据恒单行、往返一致
            let title = note
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("未命名笔记")
                .replace(['\n', '\r'], " ");
            let tag = note
                .get("tag")
                .and_then(Value::as_str)
                .unwrap_or("")
                .replace(['\n', '\r'], " ");
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
        Ok(())
    })();
    if exceeded.get() {
        return Err(BackendError::new(
            "export_size_invalid",
            "笔记归档超过 64 MB 上限",
        ));
    }
    build?;
    // 防御性兜底（正常越限已由 exceeded 提前拦截）
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

#[cfg(test)]
mod limited_writer_tests {
    use super::*;
    use std::io::{Seek, Write};

    // 117 轮 A1：越限必须可辨别（exceeded 标志）且写入被拒；zip 收尾式的
    // 原位等长回写不误触发
    #[test]
    fn limited_writer_flags_exceeded_and_allows_in_place_rewrite() {
        let mut bytes = Vec::new();
        let exceeded = std::cell::Cell::new(false);
        let mut writer = LimitedWriter {
            inner: std::io::Cursor::new(&mut bytes),
            limit: 8,
            exceeded: &exceeded,
        };
        writer.write_all(b"12345").unwrap();
        assert!(!exceeded.get());
        assert!(writer.write_all(b"67890").is_err(), "越限写入应被拒");
        assert!(exceeded.get(), "越限必须置标志供 archive() 改写错误码");
        // seek 回退后的原位重写（≤limit）正常放行
        writer.seek(std::io::SeekFrom::Start(0)).unwrap();
        writer.write_all(b"abcde").unwrap();
        drop(writer);
        assert_eq!(&bytes[..5], b"abcde");
        assert_eq!(bytes.len(), 5, "越限数据不得落入缓冲");
    }
}
