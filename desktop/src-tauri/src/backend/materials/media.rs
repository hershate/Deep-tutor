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

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MediaChunk {
    pub id: i64,
    pub offset: u64,
    pub total: u64,
    pub mime: String,
    pub data: Vec<u8>,
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
    let bytes = fs::read(&path)?;
    let actual = format!("{:x}", Sha256::digest(&bytes));
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
        total: bytes.len() as u64,
        mime,
        sha256: actual,
    })
}

pub fn chunk(state: &MaterialState, id: i64, offset: u64, length: u64) -> Result<MediaChunk> {
    let (material, path) = source(state, id)?;
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
    let mime = mime(&material.kind)
        .unwrap_or("application/octet-stream")
        .to_string();
    Ok(MediaChunk {
        id,
        offset,
        total,
        mime,
        data,
    })
}
