use super::super::error::{BackendError, Result};
use super::{ImportOutcome, MaterialState, MAX_FILE_BYTES};
use serde::Serialize;
use std::io::Read;
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

/// 126#4/#5 + 130#4：两阶段流式导入。ZipArchive 直接持有文件句柄，峰值收敛
/// 到单条目（126#4/#5 语义）——阶段一逐条「读取→校验→释放」，全部条目通过
/// 校验之前不落盘（130#4 恢复 126#4 改造前的「先全量校验后暂存」原子性：
/// 中途任一条目非法时整包 Err、零落盘，重试语义与决策文档一致），只保留
/// 条目序号与元数据；阶段二按序号 seek 回读暂存。条目计数只数文件（目录
/// 条目不占 64 个名额；上限口径为 50MB 本体 / 200MB 解压总量）。

/// 154#4：zip 条目名解码——zip crate 对未置 UTF-8 标志的条目按 cp437
/// 解码，而中文 Windows 压缩工具（WinRAR/资源管理器）实为 GBK：按
/// 「原始字节 UTF-8 → GB18030（无错且含 CJK）→ zip 默认解码」取显示名。
/// GBK 中文件名经 cp437 恒可解出合法乱码（to_str 失败路径不可达），
/// 不处理则乱码标题静默入库。
pub(crate) fn entry_display_name<R: std::io::Read>(entry: &zip::read::ZipFile<'_, R>) -> String {
    let raw = entry.name_raw();
    if let Ok(text) = std::str::from_utf8(raw) {
        return text.to_string();
    }
    let (text, _, had_errors) = encoding_rs::GB18030.decode(raw);
    if !had_errors && text.chars().any(is_cjk) {
        return text.into_owned();
    }
    entry.name().to_string()
}

fn is_cjk(character: char) -> bool {
    matches!(character as u32, 0x3400..=0x9FFF | 0xF900..=0xFAFF)
}

pub fn import_archive<R: Runtime>(
    app: &AppHandle<R>,
    state: &MaterialState,
    path: &Path,
) -> Result<ArchiveImportResult> {
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|value| !value.eq_ignore_ascii_case("zip"))
    {
        return Err(BackendError::new(
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
    let file = std::fs::File::open(&source)?;
    let mut archive = zip::ZipArchive::new(file)
        .map_err(|_| BackendError::new("invalid_archive", "ZIP 资料包结构无效"))?;
    let mut skipped = Vec::new();
    let mut total = 0_u64;
    let mut files = 0_usize;
    let mut has_supported = false;
    // 阶段一产物：通过全部校验的条目（序号、标题、类型）——字节读完即释放
    let mut plan: Vec<(usize, String, String)> = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|_| BackendError::new("invalid_archive_entry", "无法读取资料包条目"))?;
        let path = entry.enclosed_name().ok_or_else(|| {
            BackendError::new("unsafe_archive_path", "资料包包含不安全的文件路径")
        })?;
        if entry.is_dir() {
            continue;
        }
        files += 1;
        if files > MAX_ENTRIES {
            return Err(BackendError::new(
                "archive_too_many_entries",
                "资料包最多包含 64 个文件",
            ));
        }
        // 154#4：标题与扩展名取解码后的显示名（enclosed_name 的 cp437
        // 解码对 GBK 名是乱码）；路径穿越校验仍以 enclosed_name 为准
        let display = entry_display_name(&entry);
        let file_name = display.rsplit(['/', '\\']).next().unwrap_or("");
        let title = if !file_name.trim().is_empty() && file_name.len() <= 500 {
            file_name.to_string()
        } else {
            return Err(BackendError::new("invalid_archive_entry", "资料包文件名无效"));
        };
        let kind = file_name
            .rsplit_once('.')
            .map(|(_, extension)| extension)
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
                BackendError::new("invalid_archive_entry", "资料包条目损坏、加密或读取失败")
            })?;
        if bytes.len() as u64 != entry.size() || bytes.len() as u64 > MAX_FILE_BYTES {
            return Err(BackendError::new(
                "invalid_archive_entry",
                "资料包条目实际大小与目录不符",
            ));
        }
        super::validate_content(&kind, &bytes)?;
        has_supported = true;
        plan.push((index, title, kind));
    }
    // 130#4：与「全部落库失败」分流——empty_archive 只在包内根本没有可导入
    // 条目时出现，不再掩盖落库侧的真实失败原因
    if !has_supported {
        return Err(BackendError::new(
            "empty_archive",
            "资料包没有可导入的文档、音视频或图片",
        ));
    }
    // 阶段二：全部条目已过校验，按序号回读暂存（峰值仍单条目）；落库失败
    // （配额/IO）是逐条 outcome，不牵连整包与其余条目
    // 139#4：两阶段的已知取舍——同一条目解压两次（200MB 解压总量上限下
    // CPU 耗时约翻倍），换取整包原子性（任一条目校验失败时零落盘、
    // 零部分导入）；在意耗时的改进方向是阶段一将已校验字节暂存临时目录
    // 供阶段二复用（IO 换 CPU），当前按原子性优先保留双解压
    let mut outcomes = Vec::new();
    let mut staged = 0_usize;
    for (index, title, kind) in plan {
        let entry_error = |message: &'static str| ImportOutcome {
            file_name: title.clone(),
            material: None,
            deduplicated: false,
            error: Some(BackendError::new("invalid_archive_entry", message)),
        };
        let mut entry = match archive.by_index(index) {
            Ok(entry) => entry,
            Err(_) => {
                outcomes.push(entry_error("无法读取资料包条目"));
                continue;
            }
        };
        let mut bytes = Vec::new();
        let read_failed = entry
            .by_ref()
            .take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .is_err();
        if read_failed || bytes.len() as u64 != entry.size() {
            outcomes.push(entry_error("资料包条目实际大小与目录不符"));
            continue;
        }
        match super::stage_bytes(app, state, title.clone(), kind, bytes) {
            Ok(result) => {
                staged += 1;
                outcomes.push(ImportOutcome {
                    file_name: result.material.title.clone(),
                    material: Some(result.material),
                    deduplicated: result.deduplicated,
                    error: None,
                })
            }
            Err(error) => outcomes.push(ImportOutcome {
                file_name: title,
                material: None,
                deduplicated: false,
                error: Some(error),
            }),
        }
    }
    if staged == 0 {
        // 全部条目过校验但落库全败——携带首个真实原因，与 empty_archive 分流
        let first = outcomes
            .iter()
            .find_map(|outcome| outcome.error.as_ref())
            .map(|error| error.message.clone())
            .unwrap_or_else(|| "未知原因".into());
        return Err(BackendError::new(
            "archive_import_failed",
            &format!("资料包条目均未能导入：{first}"),
        ));
    }
    Ok(ArchiveImportResult {
        outcomes,
        skipped,
        total_bytes: total,
    })
}
