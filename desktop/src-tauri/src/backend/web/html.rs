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
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p", "pre",
    "section", "table", "ul",
];

pub fn parse(html: &str, search: bool) -> Result<ParsedHtml> {
    if html.len() > 1024 * 1024 {
        return Err(BackendError::new(
            "web_response_too_large",
            "网页内容超过 1 MB 上限",
        ));
    }
    let mut parsed = ParsedHtml::default();
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
        let Some(end) = end else { break };
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
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "nav",
            "footer",
            "aside",
        ]
        .contains(&name.as_str())
        {
            if !closing && !self_closing {
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
