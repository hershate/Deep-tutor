use super::super::error::{BackendError, Result};
use super::{Material, MaterialState, MAX_FILE_BYTES};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;

const MAX_CHUNK_BYTES: u64 = 256 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MediaInfo {
    pub id: i64,
    pub total: u64,
    pub mime: String,
    pub sha256: String,
}

pub fn mime(kind: &str) -> Option<&'static str> {
    match kind.to_ascii_lowercase().as_str() {
        "mp4" | "m4v" => Some("video/mp4"),
        "mov" => Some("video/quicktime"),
        "webm" => Some("video/webm"),
        "mp3" => Some("audio/mpeg"),
        "wav" => Some("audio/wav"),
        "ogg" => Some("audio/ogg"),
        "m4a" => Some("audio/mp4"),
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        _ => None,
    }
}

pub fn category(kind: &str) -> Option<&'static str> {
    let mime = mime(kind)?;
    if mime.starts_with("video/") {
        Some("视频")
    } else if mime.starts_with("audio/") {
        Some("音频")
    } else {
        Some("文件")
    }
}

pub fn validate(kind: &str, bytes: &[u8]) -> Result<()> {
    let valid = match kind.to_ascii_lowercase().as_str() {
        "mp4" | "mov" | "m4v" | "m4a" => bytes.get(4..8) == Some(b"ftyp"),
        "webm" => bytes.starts_with(&[0x1a, 0x45, 0xdf, 0xa3]),
        "mp3" => {
            bytes.starts_with(b"ID3")
                || (bytes.starts_with(&[0xff])
                    && bytes.get(1).is_some_and(|byte| byte & 0xe0 == 0xe0))
        }
        "wav" => bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WAVE"),
        "ogg" => bytes.starts_with(b"OggS"),
        "png" => bytes.starts_with(&[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]),
        "jpg" | "jpeg" => bytes.starts_with(&[0xff, 0xd8, 0xff]),
        "gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "webp" => bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP"),
        _ => {
            return Err(BackendError::invalid(
                "unsupported_media_kind",
                "该资料不是可预览的音视频或图片",
            ))
        }
    };
    if valid {
        Ok(())
    } else {
        Err(BackendError::invalid(
            "invalid_media_format",
            "媒体文件内容与扩展名或文件签名不符",
        ))
    }
}

fn source(state: &MaterialState, id: i64) -> Result<(Material, PathBuf)> {
    let store = state.store()?;
    let material = super::get(&store.conn, &state.root(), id)?;
    let kind = material.kind.to_ascii_lowercase();
    if mime(&kind).is_none() {
        return Err(BackendError::new(
            "unsupported_media_kind",
            "该资料不是可预览的音视频或图片",
        ));
    }
    if !material.original_available {
        return Err(BackendError::new("media_not_found", "媒体原件不可用"));
    }
    let root = state.root().join("originals");
    let path = root.join(format!("{}.{}", material.sha256, kind));
    if fs::symlink_metadata(&path)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(BackendError::new(
            "media_path_blocked",
            "媒体原件不得使用符号链接",
        ));
    }
    let target = fs::canonicalize(&path)
        .map_err(|_| BackendError::new("media_not_found", "媒体原件不可用"))?;
    let canonical_root = fs::canonicalize(&root)?;
    if !target.starts_with(canonical_root) || !target.is_file() {
        return Err(BackendError::new(
            "media_path_blocked",
            "媒体原件超出应用资料目录",
        ));
    }
    if target.metadata()?.len() > MAX_FILE_BYTES {
        return Err(BackendError::new(
            "file_too_large",
            "媒体原件超过 50 MB 上限",
        ));
    }
    Ok((material, target))
}

pub fn info(state: &MaterialState, id: i64) -> Result<MediaInfo> {
    let (material, path) = source(state, id)?;
    let total = path.metadata()?.len();
    let actual = streaming_sha256(&path)?;
    if actual != material.sha256 {
        return Err(BackendError::new(
            "media_integrity_failed",
            "媒体原件哈希已改变，请重新导入",
        ));
    }
    let mime = mime(&material.kind)
        .unwrap_or("application/octet-stream")
        .to_string();
    Ok(MediaInfo {
        id,
        total,
        mime,
        sha256: actual,
    })
}

/// E-3：流式哈希——256KiB 分块读取（与 chunk() 同粒度），不整读 50MB 进内存
fn streaming_sha256(path: &std::path::Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 256 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// 46#2：传输完成后的整文件复核——info() 在传输前已流式校验，此处关闭
/// 「预览期间外部改动原件」的 TOCTOU 窗口；替代原先前端 blob.arrayBuffer()
/// 第三次复制 + JS 全量 SHA-256（50MB 视频在 WebView 侧为秒级）
pub fn verify(state: &MaterialState, id: i64) -> Result<()> {
    let (material, path) = source(state, id)?;
    let actual = streaming_sha256(&path)?;
    if actual != material.sha256 {
        return Err(BackendError::new(
            "media_integrity_failed",
            "媒体原件在校验期间发生变化，请重试",
        ));
    }
    Ok(())
}

/// 46#2：以原始字节经 tauri::ipc::Response 二进制通道返回——原先 Vec<u8>
/// 经 serde_json 逐字节序列化为数字数组（256KiB ≈ 0.9–1.6MB JSON 文本，
/// JS 再解析出 26 万个 Number），50MB 视频的预览内存峰值约 3–4×；
/// offset/total/mime 元数据由前端从 info() 与应答长度推导
pub fn chunk_bytes(state: &MaterialState, id: i64, offset: u64, length: u64) -> Result<Vec<u8>> {
    let (_material, path) = source(state, id)?;
    let total = path.metadata()?.len();
    if offset > total || length == 0 || length > MAX_CHUNK_BYTES {
        return Err(BackendError::invalid(
            "invalid_media_range",
            "媒体读取范围无效，单次最多读取 256 KiB",
        ));
    }
    let mut file = fs::File::open(&path)?;
    file.seek(SeekFrom::Start(offset))?;
    let mut data = Vec::new();
    file.take(length).read_to_end(&mut data)?;
    Ok(data)
}
