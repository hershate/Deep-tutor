use super::super::error::{BackendError, Result};
use lopdf::Document;
use quick_xml::events::Event;
use quick_xml::Reader;
use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use zip::ZipArchive;

pub const MAX_FILE_BYTES: u64 = 50 * 1024 * 1024;
pub const MAX_TEXT_BYTES: usize = 5 * 1024 * 1024;
const MAX_XML_BYTES: usize = 32 * 1024 * 1024;
const MAX_ZIP_TOTAL: usize = 64 * 1024 * 1024;
const MAX_PDF_PAGES: u32 = 2000;

#[derive(Debug)]
pub struct ParsedDocument {
    pub text: String,
    pub mode: String,
    pub warning: Option<String>,
}

fn failure(message: &str) -> BackendError {
    BackendError::new("parse_failed", message)
}

fn cancelled() -> BackendError {
    BackendError::new("cancelled", "解析已取消，未写入新的解析文本")
}

fn check_cancel(cancel: &Arc<AtomicBool>) -> Result<()> {
    if cancel.load(Ordering::Acquire) {
        Err(cancelled())
    } else {
        Ok(())
    }
}

/// 无进度回调的默认入口
pub fn parse(bytes: &[u8], kind: &str, cancel: Arc<AtomicBool>) -> Result<ParsedDocument> {
    parse_with_progress(bytes, kind, cancel, |_| {})
}

/// 11#14：带进度回调的解析入口——PDF 按页上报真实百分比（35–85 区间、
/// 5% 步进），替代与工作量无关的装饰性里程碑；其余格式无中间粒度。
pub fn parse_with_progress(
    bytes: &[u8],
    kind: &str,
    cancel: Arc<AtomicBool>,
    mut progress: impl FnMut(u8),
) -> Result<ParsedDocument> {
    check_cancel(&cancel)?;
    let mut parsed = match kind {
        "txt" | "md" => ParsedDocument {
            // 11#9：Windows 记事本旧默认"UTF-8"带 BOM；U+FEFF 非空白、
            // trim 不会移除，入库会污染 Wiki 上下文并连锁毁掉 PDF 导出
            text: {
                let text = std::str::from_utf8(bytes)
                    .map_err(|_| failure("文本必须是有效 UTF-8，未生成替代文本"))?;
                text.strip_prefix('\u{FEFF}').unwrap_or(text).to_string()
            },
            mode: "text-utf8".into(),
            warning: None,
        },
        "pdf" => parse_pdf(bytes, &cancel, &mut progress)?,
        "docx" => ParsedDocument {
            text: parse_docx(bytes, &cancel)?,
            mode: "docx-xml".into(),
            warning: None,
        },
        "pptx" => ParsedDocument {
            text: parse_pptx(bytes, &cancel)?,
            mode: "pptx-xml".into(),
            warning: None,
        },
        "epub" => ParsedDocument {
            text: parse_epub(bytes, &cancel)?,
            mode: "epub-xhtml".into(),
            warning: None,
        },
        _ => {
            return Err(BackendError::new(
                "unsupported_format",
                "当前支持 PDF、DOCX、PPTX、EPUB、TXT 和 Markdown；图片及音视频尚未接入原生识别",
            ))
        }
    };
    parsed.text = parsed.text.trim().to_string();
    check_cancel(&cancel)?;
    if parsed.text.trim().is_empty() {
        return Err(BackendError::new(
            "empty_content",
            "未提取到文本；扫描件需要 OCR，当前不生成模拟文本",
        ));
    }
    if parsed.text.len() > MAX_TEXT_BYTES {
        let mut end = MAX_TEXT_BYTES;
        while !parsed.text.is_char_boundary(end) {
            end -= 1;
        }
        parsed.text.truncate(end);
        parsed.warning = Some("解析文本超过 5 MB，已明确截断；完整原件保持不变".into());
    }
    Ok(parsed)
}

fn parse_pdf(
    bytes: &[u8],
    cancel: &Arc<AtomicBool>,
    progress: &mut dyn FnMut(u8),
) -> Result<ParsedDocument> {
    let document = Document::load_mem(bytes).map_err(|_| failure("PDF 结构无法读取"))?;
    let pages = document.get_pages();
    if pages.len() as u32 > MAX_PDF_PAGES {
        return Err(BackendError::new(
            "quota_exceeded",
            "PDF 页数超过 2000 页上限",
        ));
    }
    let mut text = String::new();
    let mut skipped_pages = 0u32;
    // 11#14：真实进度按页计算（35→85），5% 步进节流事件量
    let total = pages.len().max(1);
    let mut last_reported = 35u8;
    for (index, page) in pages.keys().enumerate() {
        check_cancel(cancel)?;
        let percent = 35 + ((index as u64 * 50) / total as u64) as u8;
        if percent >= last_reported.saturating_add(5) && percent < 85 {
            last_reported = percent;
            progress(percent);
        }
        // 11#10：单页内容流损坏只跳过该页并计数，已提取文本不再整份丢弃
        // （2000 页文档 1 页异常 ≠ 全量失败）
        match document.extract_text(&[*page]) {
            Ok(part) => {
                text.push_str(&part);
                text.push('\n');
            }
            Err(_) => skipped_pages += 1,
        }
        if text.len() > MAX_TEXT_BYTES {
            break;
        }
    }
    Ok(ParsedDocument {
        text,
        mode: "pdf-text-layer".into(),
        warning: (skipped_pages > 0).then(|| {
            format!("有 {skipped_pages} 页的文本层无法读取，已跳过这些页；其余页文本完整保留")
        }),
    })
}

struct ZipBudget {
    entries: usize,
    total: usize,
}

fn read_zip_entry(
    archive: &mut ZipArchive<Cursor<&[u8]>>,
    name: &str,
    budget: &mut ZipBudget,
    cancel: &Arc<AtomicBool>,
) -> Result<Vec<u8>> {
    check_cancel(cancel)?;
    if archive.len() > 2048 {
        return Err(BackendError::new(
            "quota_exceeded",
            "压缩包条目数超过 2048 上限",
        ));
    }
    let entry = archive
        .by_name(name)
        .map_err(|_| failure("压缩包缺少必需文档"))?;
    if entry.size() as usize > MAX_XML_BYTES
        || entry.size() > entry.compressed_size().max(1).saturating_mul(200)
    {
        return Err(BackendError::new(
            "quota_exceeded",
            "压缩包条目超过安全解压上限",
        ));
    }
    let mut bytes = Vec::new();
    entry
        .take((MAX_XML_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| failure("压缩包条目读取失败"))?;
    if bytes.len() > MAX_XML_BYTES || budget.total.saturating_add(bytes.len()) > MAX_ZIP_TOTAL {
        return Err(BackendError::new(
            "quota_exceeded",
            "压缩包解压总量超过 64 MB 上限",
        ));
    }
    budget.entries += 1;
    budget.total += bytes.len();
    Ok(bytes)
}

fn decode_reference(reference: &str) -> Result<String> {
    match reference {
        "amp" => Ok("&".into()),
        "lt" => Ok("<".into()),
        "gt" => Ok(">".into()),
        "quot" => Ok("\"".into()),
        "apos" => Ok("'".into()),
        _ => {
            let (digits, radix) = reference
                .strip_prefix("#x")
                .map(|digits| (digits, 16))
                .or_else(|| reference.strip_prefix('#').map(|digits| (digits, 10)))
                .ok_or_else(|| failure("不支持外部实体，未进行网络或文件展开"))?;
            let code = u32::from_str_radix(digits, radix).map_err(|_| failure("字符引用无效"))?;
            if code == 0 {
                return Err(failure("字符引用包含 XML 禁止的 NUL"));
            }
            char::from_u32(code)
                .map(|character| character.to_string())
                .ok_or_else(|| failure("字符引用不是有效 Unicode 标量"))
        }
    }
}

fn xml_text(bytes: &[u8], kind: &str, cancel: &Arc<AtomicBool>) -> Result<String> {
    let mut reader = Reader::from_reader(bytes);
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut output = String::new();
    let mut depth = 0usize;
    let mut ignored_depth = 0usize;
    let mut text_tag = false;
    loop {
        check_cancel(cancel)?;
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(event)) => {
                depth += 1;
                if depth > 256 {
                    return Err(failure("文档嵌套层级超过安全上限"));
                }
                let name = event.local_name();
                let name = name.as_ref();
                if kind == "epub" && matches!(name, "script" | "style") {
                    ignored_depth = depth;
                }
                if matches!(name, "t" | "tab") {
                    text_tag = true;
                    if name == "tab" {
                        output.push('\t');
                    }
                }
                if name == "br" {
                    output.push('\n');
                }
            }
            Ok(Event::Empty(event)) => {
                if matches!(event.local_name().as_ref(), "br") {
                    output.push('\n');
                } else if event.local_name().as_ref() == "tab" {
                    output.push('\t');
                }
            }
            Ok(Event::Text(event)) => {
                if ignored_depth == 0 && (kind == "epub" || text_tag) {
                    let decoded = event.xml10_content();
                    let text = quick_xml::escape::unescape(&decoded)
                        .map_err(|_| failure("文档实体转义无效"))?;
                    output.push_str(&text);
                }
            }
            Ok(Event::GeneralRef(event)) => {
                if ignored_depth == 0 && (kind == "epub" || text_tag) {
                    output.push_str(&decode_reference(event.as_ref())?);
                }
            }
            Ok(Event::CData(event)) => {
                if ignored_depth == 0 && (kind == "epub" || text_tag) {
                    output.push_str(&event.into_inner());
                }
            }
            Ok(Event::End(event)) => {
                let name = event.local_name();
                let name = name.as_ref();
                if matches!(
                    name,
                    "p" | "div" | "li" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6"
                ) {
                    output.push('\n');
                }
                if name == "t" {
                    text_tag = false;
                }
                if ignored_depth == depth {
                    ignored_depth = 0;
                }
                depth = depth.saturating_sub(1);
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(_) => return Err(failure("XML 文档结构无效")),
        }
        buffer.clear();
        if output.len() > MAX_TEXT_BYTES {
            break;
        }
    }
    Ok(output)
}

fn normalize(text: &str) -> String {
    text.lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn parse_docx(bytes: &[u8], cancel: &Arc<AtomicBool>) -> Result<String> {
    let mut archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|_| failure("DOCX 压缩结构无效"))?;
    let mut budget = ZipBudget {
        entries: 0,
        total: 0,
    };
    let xml = read_zip_entry(&mut archive, "word/document.xml", &mut budget, cancel)?;
    Ok(normalize(&xml_text(&xml, "docx", cancel)?))
}

fn parse_pptx(bytes: &[u8], cancel: &Arc<AtomicBool>) -> Result<String> {
    let mut archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|_| failure("PPTX 压缩结构无效"))?;
    let mut names = archive
        .file_names()
        .filter_map(|name| {
            name.strip_prefix("ppt/slides/slide")
                .and_then(|tail| tail.strip_suffix(".xml"))
                .and_then(|number| number.parse::<u32>().ok())
                .map(|number| (number, name.to_string()))
        })
        .collect::<Vec<_>>();
    names.sort_by_key(|(number, _)| *number);
    let mut budget = ZipBudget {
        entries: 0,
        total: 0,
    };
    let mut output = String::new();
    for (_, name) in names {
        let xml = read_zip_entry(&mut archive, &name, &mut budget, cancel)?;
        output.push_str(&normalize(&xml_text(&xml, "pptx", cancel)?));
        output.push('\n');
    }
    Ok(output)
}

fn attributes(bytes: &[u8], wanted: &[&str]) -> Result<Vec<HashMap<String, String>>> {
    let mut reader = Reader::from_reader(bytes);
    let mut buffer = Vec::new();
    let mut result = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(event) | Event::Empty(event)) => {
                if wanted.contains(&event.local_name().as_ref()) {
                    let mut attributes = HashMap::new();
                    for attribute in event.attributes() {
                        let attribute = attribute.map_err(|_| failure("文档属性无效"))?;
                        let key = attribute.key.as_ref().to_string();
                        let value = attribute
                            .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                            .map_err(|_| failure("文档属性转义无效"))?
                            .into_owned();
                        attributes.insert(key, value);
                    }
                    result.push(attributes);
                }
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(_) => return Err(failure("文档索引结构无效")),
        }
        buffer.clear();
    }
    Ok(result)
}

/// 百分号解码（11#12）：EPUB href 规范允许且常见中文文件名等编码
/// （chapter-%E4%B8%AD.xhtml），仅替换 %20 会让 by_name 找不到条目，
/// 报出误导性的"压缩包缺少必需文档"。无效转义序列原样保留。
fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let high = (bytes[index + 1] as char).to_digit(16);
            let low = (bytes[index + 2] as char).to_digit(16);
            if let (Some(high), Some(low)) = (high, low) {
                result.push((high * 16 + low) as u8);
                index += 3;
                continue;
            }
        }
        result.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&result).into_owned()
}

fn zip_path(base: &str, href: &str) -> Result<String> {
    let href = percent_decode(
        href.split(['?', '#'])
            .next()
            .unwrap_or_default(),
    );
    if href.contains(':') || href.starts_with('/') {
        return Err(failure("文档索引包含外部路径"));
    }
    let mut parts = base
        .split('/')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>();
    parts.pop();
    let normalized_href = href.replace('\\', "/");
    for part in normalized_href.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return Err(failure("文档索引越出压缩包目录"));
                }
            }
            part if !part.contains('\0') => parts.push(part),
            _ => return Err(failure("文档索引路径无效")),
        }
    }
    Ok(parts.join("/"))
}

fn parse_epub(bytes: &[u8], cancel: &Arc<AtomicBool>) -> Result<String> {
    let mut archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|_| failure("EPUB 压缩结构无效"))?;
    let mut budget = ZipBudget {
        entries: 0,
        total: 0,
    };
    let container = read_zip_entry(&mut archive, "META-INF/container.xml", &mut budget, cancel)?;
    let rootfile = attributes(&container, &["rootfile"])?
        .into_iter()
        .find_map(|attributes| attributes.get("full-path").cloned())
        .ok_or_else(|| failure("EPUB 缺少 OPF 索引"))?;
    let opf = read_zip_entry(&mut archive, &rootfile, &mut budget, cancel)?;
    let manifest = attributes(&opf, &["item", "itemref"])?;
    // 11#13：xhtml 之外接受 text/html（OEBPS 1.x 常见），两者都没有才为空
    let media_ok = |kind: Option<&String>| {
        kind.is_some_and(|kind| kind == "application/xhtml+xml" || kind == "text/html")
    };
    let items = manifest
        .iter()
        .filter(|attributes| attributes.contains_key("href"))
        .filter(|attributes| media_ok(attributes.get("media-type")))
        .map(|attributes| {
            (
                attributes.get("id").cloned().unwrap_or_default(),
                attributes.get("href").cloned().unwrap_or_default(),
            )
        })
        .collect::<HashMap<_, _>>();
    let mut hrefs: Vec<String> = manifest
        .iter()
        .filter_map(|attributes| attributes.get("idref"))
        .filter_map(|idref| items.get(idref))
        .cloned()
        .collect();
    if hrefs.is_empty() {
        // 11#13：无 spine 时按 href 排序兜底，HashMap 迭代顺序不再决定章节顺序
        hrefs = items.values().cloned().collect();
        hrefs.sort();
    }
    let mut output = String::new();
    for href in hrefs {
        let path = zip_path(&rootfile, &href)?;
        let xml = read_zip_entry(&mut archive, &path, &mut budget, cancel)?;
        output.push_str(&normalize(&xml_text(&xml, "epub", cancel)?));
        output.push('\n');
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use lopdf::{dictionary, Object, Stream};

    fn zip(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut bytes = Cursor::new(Vec::new());
        let mut writer = zip::ZipWriter::new(&mut bytes);
        let options = zip::write::SimpleFileOptions::default();
        for (name, content) in entries {
            writer.start_file(*name, options).unwrap();
            std::io::Write::write_all(&mut writer, content).unwrap();
        }
        writer.finish().unwrap();
        bytes.into_inner()
    }

    #[test]
    fn docx_and_pptx_extract_real_xml_text() {
        let docx = zip(&[("word/document.xml", r#"<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>先验 &amp; 后验</w:t></w:r></w:p></w:body></w:document>"#.as_bytes())]);
        let parsed = parse(&docx, "docx", Arc::new(AtomicBool::new(false))).unwrap();
        assert_eq!(parsed.text, "先验 & 后验");
        let pptx = zip(&[
            (
                "ppt/slides/slide2.xml",
                r#"<p:sld xmlns:a="a"><a:p><a:t>第二页</a:t></a:p></p:sld>"#.as_bytes(),
            ),
            (
                "ppt/slides/slide1.xml",
                r#"<p:sld xmlns:a="a"><a:p><a:t>第一页</a:t></a:p></p:sld>"#.as_bytes(),
            ),
        ]);
        let parsed = parse(&pptx, "pptx", Arc::new(AtomicBool::new(false))).unwrap();
        assert_eq!(parsed.text, "第一页\n第二页");
    }

    #[test]
    fn epub_uses_manifest_and_never_extracts_paths() {
        let epub = zip(&[
            ("META-INF/container.xml", r#"<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>"#.as_bytes()),
            ("OPS/book.opf", r#"<package><manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>"#.as_bytes()),
            ("OPS/chapter.xhtml", r#"<html><body><style>bad</style><h1>概率</h1><script>also bad</script><p>后验概率</p></body></html>"#.as_bytes()),
        ]);
        let parsed = parse(&epub, "epub", Arc::new(AtomicBool::new(false))).unwrap();
        assert_eq!(parsed.text, "概率\n后验概率");
    }

    #[test]
    fn pdf_extracts_text_from_a_real_document() {
        let mut document = Document::with_version("1.5");
        let pages_id = document.new_object_id();
        let font_id = document.add_object(dictionary! {
            "Type" => "Font", "Subtype" => "Type1", "BaseFont" => "Helvetica"
        });
        let content = Stream::new(
            dictionary! {},
            b"BT /F1 12 Tf 72 720 Td (Bayes) Tj ET".to_vec(),
        );
        let content_id = document.add_object(content);
        let page_id = document.add_object(dictionary! {
            "Type" => "Page", "Parent" => pages_id, "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
            "Contents" => content_id, "Resources" => dictionary! {"Font" => dictionary! {"F1" => font_id}}
        });
        let pages = dictionary! {"Type" => "Pages", "Kids" => vec![page_id.into()], "Count" => 1};
        document.objects.insert(pages_id, Object::Dictionary(pages));
        let catalog_id =
            document.add_object(dictionary! {"Type" => "Catalog", "Pages" => pages_id});
        document.trailer.set("Root", catalog_id);
        let mut bytes = Vec::new();
        document.save_to(&mut bytes).unwrap();
        let parsed = parse(&bytes, "pdf", Arc::new(AtomicBool::new(false))).unwrap();
        assert!(parsed.text.contains("Bayes"), "{}", parsed.text);
    }

    #[test]
    fn rejects_bad_text_and_cancel_is_not_success() {
        assert_eq!(
            parse(&[0, 159], "txt", Arc::new(AtomicBool::new(false)))
                .unwrap_err()
                .code,
            "parse_failed"
        );
        let cancel = Arc::new(AtomicBool::new(true));
        assert_eq!(
            parse(b"valid", "txt", cancel).unwrap_err().code,
            "cancelled"
        );
    }
}
