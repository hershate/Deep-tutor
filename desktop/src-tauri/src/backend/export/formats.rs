use super::super::error::{BackendError, Result};
use super::{ExportDocument, ExportRow};
use time::OffsetDateTime;

pub fn markdown(document: &ExportDocument) -> String {
    let mut output = format!("# {}\n\n", document.title);
    for section in &document.sections {
        output.push_str(&format!("## {}\n\n", section.heading));
        for paragraph in &section.paragraphs {
            output.push_str(paragraph);
            output.push_str("\n\n");
        }
        for bullet in &section.bullets {
            output.push_str(&format!("- {bullet}\n"));
        }
        if !section.bullets.is_empty() {
            output.push('\n');
        }
    }
    output
}

pub fn text(document: &ExportDocument) -> String {
    let mut output = format!(
        "{}\n{}\n\n",
        document.title,
        "=".repeat(document.title.chars().count())
    );
    for section in &document.sections {
        output.push_str(&format!("{}\n", section.heading));
        for paragraph in &section.paragraphs {
            output.push_str(paragraph);
            output.push('\n');
        }
        for bullet in &section.bullets {
            output.push_str(&format!("- {bullet}\n"));
        }
        output.push('\n');
    }
    output
}

pub fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn xml(value: &str) -> String {
    escape_html(value)
}

pub fn html(document: &ExportDocument) -> String {
    let mut content = String::new();
    for section in &document.sections {
        content.push_str(&format!(
            "<section><h2>{}</h2>",
            escape_html(&section.heading)
        ));
        for paragraph in &section.paragraphs {
            content.push_str(&format!(
                "<p>{}</p>",
                escape_html(paragraph).replace('\n', "<br>")
            ));
        }
        if !section.bullets.is_empty() {
            content.push_str("<ul>");
            for bullet in &section.bullets {
                content.push_str(&format!("<li>{}</li>", escape_html(bullet)));
            }
            content.push_str("</ul>");
        }
        content.push_str("</section>");
    }
    format!("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"generator\" content=\"Deep Tutor\"><title>{}</title><style>body{{margin:0;background:#f4f7f2;color:#18352d;font:16px/1.7 system-ui,sans-serif}}main{{max-width:860px;margin:auto;padding:36px 20px}}h1{{font-size:30px}}section{{margin:20px 0;padding:22px;border:1px solid #dce4dc;border-radius:12px;background:white}}h2{{font-size:20px;color:#326c5b}}li{{margin:6px 0}}</style></head><body><main><h1>{}</h1>{}</main></body></html>", escape_html(&document.title), escape_html(&document.title), content)
}

fn csv_cell(value: impl ToString) -> String {
    let mut value = value.to_string();
    if value.starts_with(['=', '+', '-', '@']) {
        value.insert(0, '\'');
    }
    format!("\"{}\"", value.replace('"', "\"\""))
}

pub fn csv(document: &ExportDocument) -> String {
    let fallback;
    let rows = if document.rows.is_empty() {
        fallback = document
            .sections
            .iter()
            .map(|section| ExportRow {
                category: "内容".into(),
                title: section.heading.clone(),
                content: section.paragraphs.join("\n"),
                detail: section.bullets.join("\n"),
                date: String::new(),
                minutes: None,
            })
            .collect::<Vec<_>>();
        &fallback
    } else {
        &document.rows
    };
    let mut output = String::from("\u{feff}category,title,content,detail,date,minutes\r\n");
    for row in rows {
        output.push_str(
            &[
                csv_cell(&row.category),
                csv_cell(&row.title),
                csv_cell(&row.content),
                csv_cell(&row.detail),
                csv_cell(&row.date),
                csv_cell(
                    row.minutes
                        .map(|value| value.to_string())
                        .unwrap_or_default(),
                ),
            ]
            .join(","),
        );
        output.push_str("\r\n");
    }
    output
}

fn ics_text(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace(';', "\\;")
        .replace(',', "\\,")
        .replace('\r', "")
        .replace('\n', "\\n")
}

fn fold_ics(line: &str) -> String {
    let mut result = String::new();
    let mut length = 0;
    for character in line.chars() {
        let size = character.len_utf8();
        if length + size > 73 {
            result.push_str("\r\n ");
            length = 1;
        }
        result.push(character);
        length += size;
    }
    result
}

pub fn ics(document: &ExportDocument) -> Result<String> {
    if document.events.is_empty() {
        return Err(BackendError::new(
            "empty_export",
            "该方案没有可导出的学习日程",
        ));
    }
    let date_format = time::format_description::parse_borrowed::<2>(
        "[year padding:zero]-[month padding:zero]-[day padding:zero]",
    )
    .map_err(|_| BackendError::new("export_failed", "日历日期格式初始化失败"))?;
    let compact_date = time::format_description::parse_borrowed::<2>(
        "[year padding:zero][month padding:zero][day padding:zero]",
    )
    .map_err(|_| BackendError::new("export_failed", "日历日期格式初始化失败"))?;
    let stamp_format = time::format_description::parse_borrowed::<2>("[year padding:zero][month padding:zero][day padding:zero]T[hour padding:zero][minute padding:zero][second padding:zero]Z").map_err(|_| BackendError::new("export_failed", "日历时间格式初始化失败"))?;
    let stamp = OffsetDateTime::now_utc().format(&stamp_format)?;
    let mut output = String::from("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Deep Tutor//Learning Schedule//ZH-CN\r\nCALSCALE:GREGORIAN\r\n");
    for event in &document.events {
        let date = time::Date::parse(&event.date, &date_format)
            .map_err(|_| BackendError::new("invalid_schedule_date", "学习日程日期无效"))?;
        let end = date.next_day().ok_or_else(|| {
            BackendError::new("invalid_schedule_date", "学习日程超出支持的日期范围")
        })?;
        for line in [
            "BEGIN:VEVENT".into(),
            format!("UID:{}", event.uid),
            format!("DTSTAMP:{stamp}"),
            format!("DTSTART;VALUE=DATE:{}", date.format(&compact_date)?),
            format!("DTEND;VALUE=DATE:{}", end.format(&compact_date)?),
            format!("SUMMARY:{}", ics_text(&event.title)),
            format!("DESCRIPTION:{}", ics_text(&event.description)),
            "END:VEVENT".into(),
        ] {
            output.push_str(&fold_ics(&line));
            output.push_str("\r\n");
        }
    }
    output.push_str("END:VCALENDAR\r\n");
    Ok(output)
}

pub fn opml(document: &ExportDocument) -> String {
    let mut output = format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<opml version=\"2.0\"><head><title>{}</title></head><body><outline text=\"{}\">", xml(&document.title), xml(&document.title));
    for section in &document.sections {
        output.push_str(&format!("<outline text=\"{}\">", xml(&section.heading)));
        for paragraph in &section.paragraphs {
            output.push_str(&format!("<outline text=\"{}\"/>", xml(paragraph)));
        }
        for bullet in &section.bullets {
            output.push_str(&format!("<outline text=\"{}\"/>", xml(bullet)));
        }
        output.push_str("</outline>");
    }
    output.push_str("</outline></body></opml>\n");
    output
}
