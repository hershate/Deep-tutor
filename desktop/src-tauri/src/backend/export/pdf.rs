use super::super::error::{BackendError, Result};
use super::ExportDocument;
use lopdf::{dictionary, Document, Object, Stream};
use std::collections::BTreeMap;

fn invalid_font() -> BackendError {
    BackendError::new("pdf_font_invalid", "系统字体格式不受支持，无法生成 PDF")
}

fn number16(bytes: &[u8], offset: usize) -> Result<u16> {
    let data = bytes.get(offset..offset + 2).ok_or_else(invalid_font)?;
    Ok(u16::from_be_bytes([data[0], data[1]]))
}

fn number32(bytes: &[u8], offset: usize) -> Result<u32> {
    let data = bytes.get(offset..offset + 4).ok_or_else(invalid_font)?;
    Ok(u32::from_be_bytes([data[0], data[1], data[2], data[3]]))
}

struct Font {
    bytes: Vec<u8>,
    program: Vec<u8>,
    cmap: usize,
    units: f64,
    metrics: usize,
    metric_count: usize,
    bbox: [i64; 4],
    ascent: i64,
    descent: i64,
}

impl Font {
    fn load(bytes: Vec<u8>) -> Result<Self> {
        let base = if bytes.starts_with(b"ttcf") {
            number32(&bytes, 12)? as usize
        } else {
            0
        };
        let count = number16(&bytes, base + 4)? as usize;
        let mut source_tables = Vec::new();
        let mut tables = BTreeMap::new();
        let mut program = bytes
            .get(base..base + 12 + count * 16)
            .ok_or_else(invalid_font)?
            .to_vec();
        for index in 0..count {
            let record = base + 12 + index * 16;
            let tag: [u8; 4] = bytes
                .get(record..record + 4)
                .ok_or_else(invalid_font)?
                .try_into()
                .map_err(|_| invalid_font())?;
            let offset = number32(&bytes, record + 8)? as usize;
            let length = number32(&bytes, record + 12)? as usize;
            source_tables.push((tag, offset, length));
            tables.insert(tag, (offset, length));
        }
        for (_, offset, length) in &source_tables {
            let _ = bytes
                .get(*offset..offset + length)
                .ok_or_else(invalid_font)?;
        }
        let order = source_tables
            .iter()
            .map(|table| table.0)
            .collect::<Vec<_>>();
        let mut offsets = BTreeMap::new();
        let mut destination = 12 + count * 16;
        for (tag, offset, length) in source_tables {
            let data = bytes
                .get(offset..offset + length)
                .ok_or_else(invalid_font)?;
            program.extend_from_slice(data);
            while program.len() % 4 != 0 {
                program.push(0);
            }
            offsets.insert(tag, destination);
            destination = program.len();
        }
        for (index, tag) in order.into_iter().enumerate() {
            let offset = *offsets.get(&tag).ok_or_else(invalid_font)?;
            program[12 + index * 16 + 8..12 + index * 16 + 12]
                .copy_from_slice(&(offset as u32).to_be_bytes());
        }
        let cmap = cmap(&bytes, &tables).map_err(|_| {
            BackendError::new("pdf_font_invalid_cmap", "系统字体缺少 Unicode 字符映射")
        })?;
        let head = tables
            .get(b"head")
            .ok_or_else(|| BackendError::new("pdf_font_invalid_head", "系统字体缺少尺寸表"))?
            .0;
        let hhea = tables
            .get(b"hhea")
            .ok_or_else(|| BackendError::new("pdf_font_invalid_metrics", "系统字体缺少字宽表"))?
            .0;
        let metrics = tables
            .get(b"hmtx")
            .ok_or_else(|| BackendError::new("pdf_font_invalid_metrics", "系统字体缺少字宽表"))?
            .0;
        let units = number16(&bytes, head + 18)? as f64;
        if units <= 0.0 {
            return Err(invalid_font());
        }
        let metric_count = number16(&bytes, hhea + 34)? as usize;
        if metric_count == 0 {
            return Err(invalid_font());
        }
        Ok(Self {
            bbox: [
                number16(&bytes, head + 36)? as i16 as i64,
                number16(&bytes, head + 38)? as i16 as i64,
                number16(&bytes, head + 40)? as i16 as i64,
                number16(&bytes, head + 42)? as i16 as i64,
            ],
            ascent: number16(&bytes, hhea + 4)? as i16 as i64,
            descent: number16(&bytes, hhea + 6)? as i16 as i64,
            bytes,
            program,
            cmap,
            units,
            metrics,
            metric_count,
        })
    }

    fn glyph(&self, character: char) -> Result<Option<u16>> {
        let code = character as u32;
        let format = number16(&self.bytes, self.cmap)?;
        let glyph = if format == 12 {
            let groups = number32(&self.bytes, self.cmap + 12)? as usize;
            let mut found = None;
            for index in 0..groups {
                let offset = self.cmap + 16 + index * 12;
                let start = number32(&self.bytes, offset)?;
                let end = number32(&self.bytes, offset + 4)?;
                if code < start {
                    break;
                }
                if code <= end {
                    found = Some(number32(&self.bytes, offset + 8)? + code - start);
                    break;
                }
            }
            found
        } else if format == 4 && code <= 0xffff {
            let count = number16(&self.bytes, self.cmap + 6)? as usize / 2;
            let ends = self.cmap + 14;
            let starts = ends + count * 2 + 2;
            let deltas = starts + count * 2;
            let offsets = deltas + count * 2;
            let mut found = None;
            for index in 0..count {
                let end = u32::from(number16(&self.bytes, ends + index * 2)?);
                let start = u32::from(number16(&self.bytes, starts + index * 2)?);
                if code < start {
                    break;
                }
                if code <= end {
                    let delta = number16(&self.bytes, deltas + index * 2)?;
                    let range = number16(&self.bytes, offsets + index * 2)? as usize;
                    if range == 0 {
                        found = Some((code as u16).wrapping_add(delta) as u32);
                    } else {
                        let address = offsets + index * 2 + range + (code - start) as usize * 2;
                        let glyph = number16(&self.bytes, address)?;
                        if glyph != 0 {
                            found = Some(glyph.wrapping_add(delta) as u32);
                        }
                    }
                    break;
                }
            }
            found
        } else {
            None
        };
        match glyph {
            Some(0) | None => Ok(None),
            Some(glyph) if glyph <= u16::MAX as u32 => Ok(Some(glyph as u16)),
            _ => Ok(None),
        }
    }

    fn width(&self, glyph: u16) -> Result<f64> {
        let index = usize::from(glyph).min(self.metric_count - 1);
        Ok(f64::from(number16(&self.bytes, self.metrics + index * 4)?) * 1000.0 / self.units)
    }
}

fn cmap(bytes: &[u8], tables: &BTreeMap<[u8; 4], (usize, usize)>) -> Result<usize> {
    let cmap = tables.get(b"cmap").ok_or_else(invalid_font)?.0;
    let count = number16(bytes, cmap + 2)? as usize;
    let mut fallback = None;
    for index in 0..count {
        let offset = cmap + 4 + index * 8;
        let platform = number16(bytes, offset)?;
        let encoding = number16(bytes, offset + 2)?;
        let table = cmap + number32(bytes, offset + 4)? as usize;
        let format = number16(bytes, table)?;
        if format == 12 && (platform == 3 || platform == 0) {
            return Ok(table);
        }
        if fallback.is_none() && format == 4 && ((platform == 3 && encoding == 1) || platform == 0)
        {
            fallback = Some(table);
        }
    }
    fallback.ok_or_else(invalid_font)
}

#[cfg(windows)]
// 11#2 已知限制：Font::load 复制源字体全部表（无字形子集化），msyh 全量
// 嵌入使一页 PDF 也有约 10 MB 级体积。仓库规约默认不新增依赖，引入完整
// TrueType 子集化（glyf/loca/cmap/hmtx 重建）前维持全量嵌入：正确性优先，
// 体积问题以本声明登记，导出仍为单个自包含文件、无外部字体依赖。
fn system_font() -> Result<Font> {
    let mut last_error = None;
    for path in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"] {
        match std::fs::read(path) {
            Ok(bytes) if bytes.len() <= 64 * 1024 * 1024 => match Font::load(bytes) {
                Ok(font) => return Ok(font),
                Err(error) => last_error = Some(error),
            },
            Ok(_) => {
                last_error = Some(BackendError::new(
                    "pdf_font_too_large",
                    "系统字体超过 64 MB 上限",
                ))
            }
            Err(error) => last_error = Some(error.into()),
        }
    }
    Err(last_error.unwrap_or_else(|| {
        BackendError::new("pdf_font_unavailable", "未找到可嵌入的 Windows 中文字体")
    }))
}

#[cfg(not(windows))]
fn system_font() -> Result<Font> {
    Err(BackendError::new(
        "pdf_font_unavailable",
        "当前平台尚未配置可嵌入的 Unicode 字体",
    ))
}

fn source_lines(document: &ExportDocument) -> Vec<String> {
    let split_lines = |text: &str| -> Vec<String> {
        // 段落正文天然多行：统一换行符后按行拆开，
        // 否则布局阶段会对 '\n' 查字形表失败，多行内容无法导出 PDF
        text.replace("\r\n", "\n")
            .replace('\r', "\n")
            .split('\n')
            .map(str::to_string)
            .collect()
    };
    let mut lines = vec![document.title.clone(), String::new()];
    for section in &document.sections {
        lines.extend(split_lines(&section.heading));
        for paragraph in &section.paragraphs {
            lines.extend(split_lines(paragraph));
        }
        lines.extend(
            section
                .bullets
                .iter()
                .flat_map(|item| split_lines(&format!("• {item}"))),
        );
        lines.push(String::new());
    }
    lines
}

fn unicode_cmap(glyphs: &BTreeMap<char, u16>) -> String {
    let mut result = String::from("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /DeepTutor-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n");
    let entries = glyphs.iter().collect::<Vec<_>>();
    for block in entries.chunks(100) {
        result.push_str(&format!("{} beginbfchar\n", block.len()));
        for (character, glyph) in block {
            let mut buffer = [0; 2];
            let unicode = character.encode_utf16(&mut buffer);
            result.push_str(&format!("<{glyph:04X}> <"));
            for unit in unicode {
                result.push_str(&format!("{unit:04X}"));
            }
            result.push_str(">\n");
        }
        result.push_str("endbfchar\n");
    }
    result.push_str("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
    result
}

pub fn render(document: &ExportDocument) -> Result<(Vec<u8>, Option<String>)> {
    let font = system_font()?;
    let source = source_lines(document);
    // 11#1：任一字符缺字形不再让整份 PDF 失败——收集时跳过缺字形字符，
    // 布局阶段以 □（U+25A1）替代呈现；emoji/生僻字/BOM 残留只影响对应字符
    let mut glyphs = BTreeMap::new();
    for character in source
        .iter()
        .flat_map(|line| line.chars())
        .filter(|character| !character.is_control() && *character != '\u{FEFF}')
    {
        if let Some(glyph) = font.glyph(character)? {
            glyphs.insert(character, glyph);
        }
    }
    let fallback_glyph = font.glyph('\u{25A1}')?.unwrap_or(0);
    glyphs.entry('\u{25A1}').or_insert(fallback_glyph);
    // 缺字形替换的 advance：字体无 □ 字形（glyph 0）时 width≈0 会让大量
    // 替换符挤进一行（A-17），回退为约半字宽
    let fallback_advance = if fallback_glyph == 0 {
        5.5
    } else {
        font.width(fallback_glyph)? * 0.011
    };
    let mut lines = Vec::new();
    let mut total_characters = 0_usize;
    let mut replaced_characters = 0_usize;
    for line in source {
        let line = line.replace('\t', "    ");
        let mut current = String::new();
        let mut width = 0.0;
        for character in line.chars() {
            let glyph = match glyphs.get(&character) {
                Some(glyph) => *glyph,
                // 11#1：缺字形字符以 □ 替代，导出继续；A-17：计数用于回执警告
                None => {
                    let advance = fallback_advance;
                    if !current.is_empty() && width + advance > 495.0 {
                        lines.push(std::mem::take(&mut current));
                        width = 0.0;
                    }
                    current.push('\u{25A1}');
                    width += advance;
                    total_characters += 1;
                    replaced_characters += 1;
                    continue;
                }
            };
            let advance = font.width(glyph)? * 0.011;
            if !current.is_empty() && width + advance > 495.0 {
                lines.push(std::mem::take(&mut current));
                width = 0.0;
            }
            current.push(character);
            width += advance;
            total_characters += 1;
        }
        lines.push(current);
    }
    let pages = lines.chunks(48).collect::<Vec<_>>();
    let mut pdf = Document::with_version("1.7");
    let pages_id = pdf.new_object_id();
    let font_file = pdf.add_object(Stream::new(
        dictionary! {"Length1" => font.program.len() as i64},
        font.program.clone(),
    ));
    let scale = |value: i64| value * 1000 / font.units as i64;
    let descriptor = pdf.add_object(dictionary! {
        "Type" => "FontDescriptor", "FontName" => "DeepTutorUnicode", "Flags" => 32,
        "FontBBox" => font.bbox.iter().map(|value| Object::Integer(scale(*value))).collect::<Vec<_>>(),
        "ItalicAngle" => 0, "Ascent" => scale(font.ascent), "Descent" => scale(font.descent),
        "CapHeight" => scale(font.ascent), "StemV" => 80, "FontFile2" => font_file,
    });
    let mut widths = BTreeMap::new();
    for glyph in glyphs.values() {
        widths.insert(*glyph, font.width(*glyph)? as i64);
    }
    let descendant = pdf.add_object(dictionary! {
        "Type" => "Font", "Subtype" => "CIDFontType2", "BaseFont" => "DeepTutorUnicode",
        "CIDSystemInfo" => dictionary! {"Registry" => "Adobe", "Ordering" => "Identity", "Supplement" => 0},
        "FontDescriptor" => descriptor, "DW" => 600, "CIDToGIDMap" => "Identity",
        "W" => widths.into_iter().map(|(glyph, width)| Object::Array(vec![glyph.into(), Object::Array(vec![width.into()])])).collect::<Vec<_>>(),
    });
    let cmap = pdf.add_object(Stream::new(
        dictionary! {},
        unicode_cmap(&glyphs).into_bytes(),
    ));
    let font_id = pdf.add_object(dictionary! {
        "Type" => "Font", "Subtype" => "Type0", "BaseFont" => "DeepTutorUnicode",
        "Encoding" => "Identity-H", "DescendantFonts" => vec![descendant.into()], "ToUnicode" => cmap,
    });
    let resources = pdf.add_object(dictionary! {"Font" => dictionary! {"F1" => font_id}});
    let mut page_ids = Vec::new();
    for page in pages {
        let mut content = String::from("BT\n/F1 11 Tf\n14 TL\n54 780 Td\n");
        for line in page {
            content.push('<');
            for character in line.chars() {
                let glyph = glyphs.get(&character).ok_or_else(|| {
                    BackendError::new("pdf_font_missing_glyph", "内容包含不支持的字符")
                })?;
                content.push_str(&format!("{glyph:04X}"));
            }
            content.push_str("> Tj\nT*\n");
        }
        content.push_str("ET\n");
        let stream = pdf.add_object(Stream::new(dictionary! {}, content.into_bytes()));
        page_ids.push(pdf.add_object(
            dictionary! {"Type" => "Page", "Parent" => pages_id, "Contents" => stream},
        ));
    }
    pdf.objects.insert(pages_id, Object::Dictionary(dictionary! {
        "Type" => "Pages", "Kids" => page_ids.into_iter().map(Object::Reference).collect::<Vec<_>>(), "Count" => lines.len().div_ceil(48) as i64,
        "Resources" => resources, "MediaBox" => vec![0.into(), 0.into(), 595.into(), 842.into()],
    }));
    let catalog = pdf.add_object(dictionary! {"Type" => "Catalog", "Pages" => pages_id});
    pdf.trailer.set("Root", catalog);
    pdf.compress();
    let mut bytes = Vec::new();
    pdf.save_to(&mut bytes)?;
    // A-17：缺字形替换占比 >1% 时随回执告警（与材料解析域 03#16 同阈值），
    // 不再让成片 □ 静默进入成品
    let notice = if total_characters > 0
        && replaced_characters * 100 > total_characters
    {
        Some(format!(
            "导出字体缺少 {} 个字符的字形（约 {}%），已用 □ 替代，建议改用 HTML/Markdown 格式查看完整内容",
            replaced_characters,
            replaced_characters * 100 / total_characters.max(1)
        ))
    } else {
        None
    };
    Ok((bytes, notice))
}
