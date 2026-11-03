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
                text.push(' ');
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
        if ["script", "style", "noscript", "template", "svg"].contains(&name.as_str()) {
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
        if search && !snippet_tag.is_empty() && closing && name == snippet_tag {
            snippet_tag.clear();
            continue;
        }
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
    parsed.text = clean(&text);
    for candidate in &mut parsed.candidates {
        candidate.title = clean(&candidate.title);
        candidate.snippet = clean(&candidate.snippet);
    }
    Ok(parsed)
}
