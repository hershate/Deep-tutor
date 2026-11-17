use std::collections::HashMap;

use super::{BackendError, Result};

#[derive(Default)]
pub struct ParsedHtml {
    pub title: String,
    pub text: String,
    pub candidates: Vec<Candidate>,
}

pub struct Candidate {
    pub href: String,
    pub title: String,
    pub snippet: String,
}

fn decode_entities(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    let mut cursor = 0;
    while let Some(start) = value[cursor..].find('&') {
        let start = cursor + start;
        output.push_str(&value[cursor..start]);
        let Some(end) = value[start + 1..].find(';').map(|end| start + 1 + end) else {
            output.push_str(&value[start..]);
            return output;
        };
        if end - start > 18 {
            output.push('&');
            cursor = start + 1;
            continue;
        }
        let reference = &value[start + 1..end];
        let decoded = match reference {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            "nbsp" => Some(' '),
            _ => reference
                .strip_prefix("#x")
                .or_else(|| reference.strip_prefix("#X"))
                .and_then(|digits| u32::from_str_radix(digits, 16).ok())
                .or_else(|| {
                    reference
                        .strip_prefix('#')
                        .and_then(|digits| digits.parse::<u32>().ok())
                })
                .and_then(char::from_u32)
                .filter(|character| {
                    !character.is_control() || matches!(character, '\n' | '\t' | '\r')
                }),
        };
        if let Some(character) = decoded {
            output.push(character);
        } else {
            output.push_str(&value[start..=end]);
        }
        cursor = end + 1;
    }
    output.push_str(&value[cursor..]);
    output
}

fn tag_name(tag: &str) -> String {
    tag.trim_start()
        .trim_start_matches('/')
        .trim_start()
        .split(|character: char| character.is_whitespace() || character == '/')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
}

/// P-079：预扫描文档中的闭合标签——记录每个标签名最后一次出现的合法闭合
/// 标签字节偏移。
///
/// 原实现在每个自闭合候选标签上把文档剩余部分整体转小写（整段复制）再线性
/// 查找，N 个候选标签退化为 O(n²)：1 MB 文档实测停顿十余秒，抓取与检索
/// 导入路径（web/mod.rs 的 fetch / search）会被单个塞满自闭合标签的页面拖住。
/// 改为一次性预扫描后，「当前位置之后是否存在同名闭合标签」的判定降为 O(1)。
struct ClosingTags {
    last_offset: HashMap<String, usize>,
}

impl ClosingTags {
    fn scan(html: &str) -> Self {
        let mut last_offset: HashMap<String, usize> = HashMap::new();
        let mut cursor = 0;
        while let Some(offset) = html[cursor..].find("</") {
            let start = cursor + offset;
            cursor = start + 2;
            // 标签名按与 tag_name 相同的规则截取（空白、'/' 或 '>' 之前），
            // 其后必须是空白或 `>`——避免 `</scripture` 之类的同前缀标签被误判
            let name_start = start + 2;
            let mut name_end = name_start;
            let mut delimiter = None;
            for (offset, character) in html[name_start..].char_indices() {
                if character.is_whitespace() || character == '/' || character == '>' {
                    name_end = name_start + offset;
                    delimiter = Some(character);
                    break;
                }
                name_end = name_start + offset + character.len_utf8();
            }
            let valid = match delimiter {
                Some(character) => character.is_whitespace() || character == '>',
                // 文档以标签名结尾（截断的闭合标签）——与原判定的空尾一致
                None => true,
            };
            if valid && name_end > name_start {
                let name = html[name_start..name_end].to_ascii_lowercase();
                // 扫描顺序即偏移升序，最后一次写入即该名的最大偏移
                last_offset.insert(name, start);
            }
        }
        Self { last_offset }
    }

    /// `rest_start` 之后是否存在同名闭合标签（忽略大小写）。
    fn has_closing_after(&self, rest_start: usize, name: &str) -> bool {
        self.last_offset
            .get(name)
            .is_some_and(|offset| *offset >= rest_start)
    }
}

fn attribute(tag: &str, wanted: &str) -> Option<String> {
    let bytes = tag.as_bytes();
    let mut index = tag_name(tag).len() + usize::from(tag.starts_with('/'));
    while index < bytes.len() {
        while index < bytes.len() && (bytes[index].is_ascii_whitespace() || bytes[index] == b'/') {
            index += 1;
        }
        let start = index;
        while index < bytes.len()
            && (bytes[index].is_ascii_alphanumeric() || matches!(bytes[index], b'-' | b'_' | b':'))
        {
            index += 1;
        }
        if start == index {
            index += 1;
            continue;
        }
        let name = tag[start..index].to_ascii_lowercase();
        while index < bytes.len() && bytes[index].is_ascii_whitespace() {
            index += 1;
        }
        if index >= bytes.len() || bytes[index] != b'=' {
            continue;
        }
        index += 1;
        while index < bytes.len() && bytes[index].is_ascii_whitespace() {
            index += 1;
        }
        if index >= bytes.len() {
            return None;
        }
        let quote = if matches!(bytes[index], b'\'' | b'"') {
            let quote = bytes[index];
            index += 1;
            Some(quote)
        } else {
            None
        };
        let start = index;
        while index < bytes.len()
            && if let Some(quote) = quote {
                bytes[index] != quote
            } else {
                !bytes[index].is_ascii_whitespace()
            }
        {
            index += 1;
        }
        if name == wanted {
            return Some(decode_entities(&tag[start..index]));
        }
        if quote.is_some() {
            index += 1;
        }
    }
    None
}

fn clean(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// 正文清洗（10#21）：保留换行结构——行内空白折叠为单空格、行首尾空白去除、
/// 连续空行压成一个。知识问答的引用行号、模型对结构的理解都依赖换行。
fn clean_block(value: &str) -> String {
    let mut result = String::with_capacity(value.len());
    let mut last_space = false;
    let mut last_newline = false;
    for character in value.chars() {
        match character {
            '\n' => {
                if !last_newline {
                    result.push('\n');
                }
                last_newline = true;
                last_space = false;
            }
            item if item.is_whitespace() => {
                if !last_space && !last_newline {
                    result.push(' ');
                }
                last_space = true;
            }
            item => {
                result.push(item);
                last_space = false;
                last_newline = false;
            }
        }
    }
    result.trim().to_string()
}

/// 块级元素（10#21）：进入/退出时补换行，正文不再被压成单行
const BLOCK_TAGS: [&str; 26] = [
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
];

pub fn parse(html: &str, search: bool) -> Result<ParsedHtml> {
    if html.len() > 1024 * 1024 {
        return Err(BackendError::new(
            "web_response_too_large",
            "网页内容超过 1 MB 上限",
        ));
    }
    let mut parsed = ParsedHtml::default();
    let closing_tags = ClosingTags::scan(html);
    let mut cursor = 0;
    let mut skip_tag = String::new();
    let mut skip_depth = 0_u32;
    let mut in_title = false;
    let mut snippet_tag = String::new();
    let mut candidate: Option<Candidate> = None;
    let mut text = String::new();
    let mut title = String::new();
    while let Some(open) = html[cursor..].find('<') {
        let open = cursor + open;
        let raw_text = decode_entities(&html[cursor..open]);
        if skip_depth == 0 {
            if !snippet_tag.is_empty() {
                if let Some(current) = candidate.as_mut() {
                    current.snippet.push_str(&raw_text);
                } else if let Some(current) = parsed.candidates.last_mut() {
                    current.snippet.push_str(&raw_text);
                }
            } else if in_title {
                title.push_str(&raw_text);
            } else if let Some(current) = candidate.as_mut() {
                current.title.push_str(&raw_text);
            } else {
                text.push_str(&raw_text);
            }
        }
        let mut end = None;
        let mut quote = None;
        for (offset, character) in html[open + 1..].char_indices() {
            match (quote, character) {
                (Some(expected), actual) if expected == actual => quote = None,
                (None, '\'' | '"') => quote = Some(character),
                (None, '>') => {
                    end = Some(open + 1 + offset);
                    break;
                }
                _ => {}
            }
        }
        // 23#8：引号永不闭合（如注释内英文撇号、畸形属性）时不得终止整个
        // 解析——正文会被静默截断且无告警；按畸形标签跳过该 '<' 继续扫描，
        // 让其余文档照常解析
        let Some(end) = end else {
            cursor = open + 1;
            continue;
        };
        let tag = &html[open + 1..end];
        if tag.starts_with("!--") {
            // 10#21：注释可能包含 '>'，通用扫描会提前收束导致后半段泄漏为正文
            match html[open..].find("-->") {
                Some(close) => cursor = open + close + 3,
                None => cursor = html.len(),
            }
            continue;
        }
        let name = tag_name(tag);
        let closing = tag.trim_start().starts_with('/');
        let self_closing = tag.trim_end().ends_with('/');
        cursor = end + 1;
        if skip_depth > 0 {
            if name == skip_tag {
                if closing {
                    skip_depth -= 1;
                } else if !self_closing {
                    skip_depth += 1;
                }
            }
            continue;
        }
        if [
            "script", "style", "noscript", "template", "svg", "nav", "footer", "aside",
        ]
        .contains(&name.as_str())
        {
            // P-079：XHTML 风格自闭合写法（<script src="x"/>、<style/>）——
            // 浏览器把非 void HTML 元素的自闭合斜杠忽略，仍按开启标签处理，
            // 其后源码直到 </script> 都不是页面文本；原先当作已完结的空元素
            // 直接跳过，整段 JavaScript / CSS 源码泄漏进提取正文。
            // svg 属外来内容，自闭合标志被承认，仍按空元素处理。
            // 文档中没有同名闭合标签时仍按空元素处理——否则跳过状态永不
            // 结束，其后正文全部丢失
            let opens = !closing
                && (!self_closing
                    || (name != "svg" && closing_tags.has_closing_after(end + 1, &name)));
            if opens {
                skip_tag = name;
                skip_depth = 1;
            }
            continue;
        }
        if name == "title" {
            in_title = !closing;
            continue;
        }
        if BLOCK_TAGS.contains(&name.as_str())
            && snippet_tag.is_empty()
            && !in_title
            && candidate.is_none()
        {
            text.push('\n');
        }
        if search && !snippet_tag.is_empty() && closing && name == snippet_tag {
            snippet_tag.clear();
            continue;
        }
        // 10#22：DDG 检索选择器与 duckduckgo.com/html 页面结构强耦合
        //（result__snippet / result__a）。改版后 candidates 为空会走
        // search_unavailable 优雅失败，不会虚构链接；恢复需更新此处选择器。
        if search
            && !closing
            && attribute(tag, "class").is_some_and(|class| {
                class
                    .split_whitespace()
                    .any(|item| item == "result__snippet")
            })
        {
            if candidate.is_some() || !parsed.candidates.is_empty() {
                snippet_tag = name;
            }
            continue;
        }
        if search && name == "a" {
            if closing {
                if let Some(current) = candidate.take() {
                    parsed.candidates.push(current);
                }
            } else if attribute(tag, "class")
                .is_some_and(|class| class.split_whitespace().any(|item| item == "result__a"))
            {
                candidate = Some(Candidate {
                    href: attribute(tag, "href").unwrap_or_default(),
                    title: String::new(),
                    snippet: String::new(),
                });
            }
            continue;
        }
    }
    if let Some(candidate) = candidate {
        parsed.candidates.push(candidate);
    }
    parsed.title = clean(&title);
    parsed.text = clean_block(&text);
    for candidate in &mut parsed.candidates {
        candidate.title = clean(&candidate.title);
        candidate.snippet = clean(&candidate.snippet);
    }
    Ok(parsed)
}
