mod anki;
pub mod commands;
mod formats;
mod notes;
mod pdf;
#[cfg(test)]
mod tests;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::learning::{self, LearningState, Plan, PlanResult};
use super::materials;
use super::review::ReviewState;
use super::store::Store;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;
use tauri::{AppHandle, Runtime};

const MAX_EXPORT_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ExportScope {
    Plan,
    Lecture,
    Knowledge,
    Review,
    Schedule,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ExportFormat {
    Md,
    Pdf,
    Ics,
    Apkg,
    Html,
    Csv,
    Txt,
    Opml,
}

impl ExportFormat {
    pub fn extension(self) -> &'static str {
        match self {
            Self::Md => "md",
            Self::Pdf => "pdf",
            Self::Ics => "ics",
            Self::Apkg => "apkg",
            Self::Html => "html",
            Self::Csv => "csv",
            Self::Txt => "txt",
            Self::Opml => "opml",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExportInput {
    pub scope: ExportScope,
    pub format: ExportFormat,
    pub plan_id: Option<String>,
    pub stage_id: Option<String>,
    pub unit_index: Option<usize>,
    pub version: Option<u32>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportResult {
    pub bytes: usize,
    pub sha256: String,
    pub format: ExportFormat,
    pub title: String,
    /// 导出降级警告（A-17）：如 PDF 缺字形字符以 □ 替换的占比提示；
    /// 无降级时为 None
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct ExportSection {
    pub heading: String,
    pub paragraphs: Vec<String>,
    pub bullets: Vec<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct ExportCard {
    pub front: String,
    pub back: String,
    pub tag: String,
    /// 复习卡稳定 id（11#4：Anki guid 锚定它而非导出时间戳）
    pub id: i64,
    /// SM-2 调度状态（11#3：导出迁移复习进度而非清零回新卡）
    pub due_at: String,
    pub interval_days: f64,
    pub ease: f64,
    pub repetitions: u32,
    pub lapses: u32,
    pub suspended: bool,
}

impl ExportCard {
    /// 无复习调度的新卡（图谱/讲义/知识库导出路径）
    pub(crate) fn new(front: String, back: String, tag: String) -> Self {
        Self {
            front,
            back,
            tag,
            id: 0,
            due_at: String::new(),
            interval_days: 0.0,
            ease: 2.5,
            repetitions: 0,
            lapses: 0,
            suspended: false,
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ExportRow {
    pub category: String,
    pub title: String,
    pub content: String,
    pub detail: String,
    pub date: String,
    pub minutes: Option<u32>,
}

#[derive(Debug, Clone)]
pub(crate) struct ExportEvent {
    pub date: String,
    pub title: String,
    pub description: String,
    pub uid: String,
}

#[derive(Debug, Clone)]
pub(crate) struct ExportDocument {
    pub title: String,
    pub sections: Vec<ExportSection>,
    pub cards: Vec<ExportCard>,
    pub rows: Vec<ExportRow>,
    pub events: Vec<ExportEvent>,
}

impl ExportDocument {
    fn new(title: String) -> Self {
        Self {
            title,
            sections: vec![],
            cards: vec![],
            rows: vec![],
            events: vec![],
        }
    }
}

/// 31#31：构建阶段按累计字节熔断——原先 64MB 上限在全部内容物化渲染之后
/// 才检查，大库导出先吃满数百 MB 瞬时内存/CPU 才被拒绝
fn guard_document_size(document: &ExportDocument) -> Result<()> {
    let mut bytes = document.title.len();
    for section in &document.sections {
        bytes += section.heading.len();
        bytes += section
            .paragraphs
            .iter()
            .map(|paragraph| paragraph.len() + 1)
            .sum::<usize>();
        bytes += section
            .bullets
            .iter()
            .map(|bullet| bullet.len() + 1)
            .sum::<usize>();
    }
    for row in &document.rows {
        bytes += row.title.len() + row.content.len() + row.detail.len() + row.date.len();
    }
    for card in &document.cards {
        bytes += card.front.len() + card.back.len() + card.tag.len();
    }
    if bytes > MAX_EXPORT_BYTES {
        return Err(BackendError::new(
            "export_size_invalid",
            "导出内容超过 64 MB 上限",
        ));
    }
    Ok(())
}

fn section(heading: String, paragraphs: Vec<String>, bullets: Vec<String>) -> ExportSection {
    ExportSection {
        heading,
        paragraphs,
        bullets,
    }
}

fn completed_plan(state: &LearningState, id: Option<&str>) -> Result<Plan> {
    let id =
        id.ok_or_else(|| BackendError::invalid("plan_required", "此导出范围需要选择学习方案"))?;
    let plan = learning::get(state, id)?;
    if plan.result.is_none() || !["done", "degraded"].contains(&plan.status.as_str()) {
        return Err(BackendError::new(
            "export_not_ready",
            "只有完整生成的方案可以导出",
        ));
    }
    Ok(plan)
}

fn result(plan: &Plan) -> Result<&PlanResult> {
    plan.result
        .as_ref()
        .ok_or_else(|| BackendError::new("export_not_ready", "方案内容不完整"))
}

fn plan_document(plan: &Plan) -> Result<ExportDocument> {
    let result = result(plan)?;
    let mut document = ExportDocument::new(result.goal.clone());
    let mut summary = vec![
        format!("目标截止日期：{}", result.deadline),
        format!("每日可投入：{} 分钟", plan.daily_minutes),
        format!(
            "学习日程：{} 至 {}",
            result.schedule.start_date, result.schedule.end_date
        ),
        format!(
            "方案版本：v{}；五维校验：{}",
            plan.version,
            if result.validation.passed {
                "通过"
            } else {
                "存在未通过项"
            }
        ),
    ];
    summary.extend(
        result
            .warnings
            .iter()
            .map(|warning| format!("复核提示：{warning}")),
    );
    document
        .sections
        .push(section("方案信息".into(), summary, vec![]));
    for stage in &result.stages {
        let bullets = stage
            .node_ids
            .iter()
            .filter_map(|id| result.graph.nodes.iter().find(|node| node.id == *id))
            .map(|node| format!("{}：{}", node.title, node.summary))
            .collect::<Vec<_>>();
        document.sections.push(section(
            stage.title.clone(),
            vec![format!(
                "{} 至 {} · {} 分钟 · 难度 {}/5",
                stage.start_date, stage.end_date, stage.minutes, stage.difficulty
            )],
            bullets.clone(),
        ));
        document.rows.push(ExportRow {
            category: "学习阶段".into(),
            title: stage.title.clone(),
            content: bullets.join("\n"),
            detail: format!("难度 {}/5；截止 {}", stage.end_date, stage.difficulty),
            date: stage.start_date.clone(),
            minutes: Some(stage.minutes),
        });
    }
    let mut checks = vec![];
    for (name, check) in [
        ("依赖闭包", &result.validation.dependency_closure),
        ("难度阶梯", &result.validation.difficulty_step),
        ("认知负荷", &result.validation.cognitive_load),
        ("里程碑可达", &result.validation.milestone_feasibility),
        ("缺口分析", &result.validation.gap_analysis),
    ] {
        checks.push(format!("{name}：{} {}", check.status, check.details));
    }
    checks.extend(
        result
            .validation
            .fixes
            .iter()
            .map(|fix| format!("修复建议：{fix}")),
    );
    document
        .sections
        .push(section("方案校验".into(), vec![], checks));
    let resources = result
        .resources
        .get("items")
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            Some(format!(
                "{} · {} · SHA-256 {}",
                item.get("title")?.as_str()?,
                item.get("kind")?.as_str()?,
                item.get("sha256")?.as_str()?
            ))
        })
        .collect::<Vec<_>>();
    if !resources.is_empty() {
        document
            .sections
            .push(section("关联资料".into(), vec![], resources));
    }
    for node in &result.graph.nodes {
        document.cards.push(ExportCard::new(
            format!("请解释：{}", node.title),
            node.summary.clone(),
            result.goal.clone(),
        ));
    }
    Ok(document)
}

fn lecture_document(state: &LearningState, input: &ExportInput) -> Result<ExportDocument> {
    let plan = completed_plan(state, input.plan_id.as_deref())?;
    let stage_id = input
        .stage_id
        .as_deref()
        .ok_or_else(|| BackendError::invalid("stage_required", "导出讲义需要选择学习阶段"))?;
    let lecture = learning::lecture::get(state, &plan.id, stage_id, input.version)?;
    let index = input.unit_index.unwrap_or(lecture.current_unit);
    let content = lecture
        .units
        .iter()
        .find(|unit| unit.index == index && unit.status == "done")
        .and_then(|unit| unit.content.as_ref())
        .ok_or_else(|| BackendError::new("lecture_not_ready", "所选讲义单元尚未保存完成结果"))?;
    let mut document = ExportDocument::new(content.title.clone());
    document.sections.push(section(
        "概述".into(),
        vec![content.overview.clone()],
        vec![],
    ));
    for item in &content.sections {
        document.sections.push(section(
            item.heading.clone(),
            vec![item.content.clone()],
            vec![],
        ));
        document.rows.push(ExportRow {
            category: "讲义章节".into(),
            title: item.heading.clone(),
            content: item.content.clone(),
            detail: content.title.clone(),
            date: String::new(),
            minutes: None,
        });
    }
    document.sections.push(section(
        "示例".into(),
        vec![],
        content
            .examples
            .iter()
            .map(|item| format!("{}：{}", item.title, item.content))
            .collect(),
    ));
    document.sections.push(section(
        "检查理解".into(),
        vec![],
        content
            .checkpoints
            .iter()
            .map(|item| format!("{} — {}", item.question, item.answer))
            .collect(),
    ));
    document.sections.push(section(
        "原文引用".into(),
        vec![],
        content
            .citations
            .iter()
            .map(|item| format!("资料 #{}：{}", item.material_id, item.quote))
            .collect(),
    ));
    for item in &content.checkpoints {
        document.cards.push(ExportCard::new(
            item.question.clone(),
            item.answer.clone(),
            content.title.clone(),
        ));
    }
    Ok(document)
}

fn knowledge_document(state: &LearningState) -> Result<ExportDocument> {
    let store = state.store()?;
    let mut document = ExportDocument::new("笔记与知识库".into());
    let notes = store.collection("notes")?;
    for note in notes.value.as_array().into_iter().flatten() {
        let title = note
            .get("title")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("未命名笔记");
        let content = note
            .get("content")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        let tag = note
            .get("tag")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        document.sections.push(section(
            title.into(),
            vec![format!("标签：{tag}"), content.into()],
            vec![],
        ));
        document.rows.push(ExportRow {
            category: "笔记".into(),
            title: title.into(),
            content: content.into(),
            detail: tag.into(),
            date: String::new(),
            minutes: None,
        });
        document
            .cards
            .push(ExportCard::new(title.into(), content.into(), tag.into()));
        guard_document_size(&document)?;
    }
    let mut statement = store.conn.prepare("SELECT materials.title, materials.kind, material_text.content FROM material_text JOIN materials ON materials.id = material_text.material_id ORDER BY materials.id")?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    for (title, kind, content) in rows {
        document.sections.push(section(
            title.clone(),
            vec![format!("资料类型：{kind}"), content.clone()],
            vec![],
        ));
        document.rows.push(ExportRow {
            category: "知识库资料".into(),
            title,
            content,
            detail: kind,
            date: String::new(),
            minutes: None,
        });
        guard_document_size(&document)?;
    }
    Ok(document)
}

fn review_document(state: &LearningState) -> Result<ExportDocument> {
    let review = ReviewState::new(state.database_path(), state);
    let cards = super::review::export_cards(&review)?;
    let mut document = ExportDocument::new("复习卡片".into());
    for card in cards {
        document.sections.push(section(
            card.front.clone(),
            vec![card.back.clone()],
            vec![format!(
                "标签：{}；到期：{}；间隔：{} 天",
                card.tag, card.due_at, card.interval_days
            )],
        ));
        document.rows.push(ExportRow {
            category: "复习卡".into(),
            title: card.front.clone(),
            content: card.back.clone(),
            detail: card.tag.clone(),
            // due_at 同时进 rows 与 cards：rows 侧改为 clone，
            // 保留原值给 cards 的调度迁移（11#3）
            date: card.due_at.clone(),
            minutes: None,
        });
        document.cards.push(ExportCard {
            front: card.front,
            back: card.back,
            tag: card.tag,
            id: card.id,
            due_at: card.due_at,
            interval_days: card.interval_days,
            ease: card.ease,
            repetitions: card.repetitions,
            lapses: card.lapses,
            suspended: card.suspended,
        });
        guard_document_size(&document)?;
    }
    Ok(document)
}

fn schedule_document(state: &LearningState, input: &ExportInput) -> Result<ExportDocument> {
    let plan = completed_plan(state, input.plan_id.as_deref())?;
    let result = result(&plan)?;
    let mut document = ExportDocument::new(format!("{} · 学习日程", result.goal));
    for entry in &result.schedule.entries {
        let stage = result
            .stages
            .iter()
            .find(|stage| stage.id == entry.stage_id);
        let title = stage
            .map(|stage| stage.title.clone())
            .unwrap_or_else(|| entry.stage_id.clone());
        let nodes = entry
            .node_ids
            .iter()
            .filter_map(|id| result.graph.nodes.iter().find(|node| node.id == *id))
            .map(|node| node.title.clone())
            .collect::<Vec<_>>()
            .join("、");
        let content = format!("{title}：{nodes}");
        document.sections.push(section(
            entry.date.clone(),
            vec![format!("{content} · {} 分钟", entry.minutes)],
            vec![],
        ));
        document.rows.push(ExportRow {
            category: "学习日程".into(),
            title: title.clone(),
            content: nodes.clone(),
            detail: stage
                .map(|stage| format!("{} 至 {}", stage.start_date, stage.end_date))
                .unwrap_or_default(),
            date: entry.date.clone(),
            minutes: Some(entry.minutes),
        });
        document.events.push(ExportEvent {
            date: entry.date.clone(),
            title: format!("{} · {}", result.goal, title),
            description: format!("{nodes}\n预计 {} 分钟", entry.minutes),
            uid: format!(
                "{:x}@deep-tutor.local",
                Sha256::digest(format!("{}:{}:{}", plan.id, entry.date, entry.stage_id).as_bytes())
            ),
        });
    }
    Ok(document)
}

fn build_document(state: &LearningState, input: &ExportInput) -> Result<ExportDocument> {
    match input.scope {
        ExportScope::Plan => plan_document(&completed_plan(state, input.plan_id.as_deref())?),
        ExportScope::Lecture => lecture_document(state, input),
        ExportScope::Knowledge => knowledge_document(state),
        ExportScope::Review => review_document(state),
        ExportScope::Schedule => schedule_document(state, input),
    }
}

pub fn export<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    input: ExportInput,
    destination: &Path,
) -> Result<ExportResult> {
    if matches!(input.format, ExportFormat::Ics) && !matches!(input.scope, ExportScope::Schedule) {
        return Err(BackendError::invalid(
            "invalid_export_combination",
            "ICS 只支持学习日程范围",
        ));
    }
    if matches!(input.format, ExportFormat::Apkg) && matches!(input.scope, ExportScope::Schedule) {
        return Err(BackendError::invalid(
            "invalid_export_combination",
            "日程请使用 ICS 导出，不生成复习卡",
        ));
    }
    let document = build_document(state, &input)?;
    if document.sections.is_empty() && document.rows.is_empty() && document.cards.is_empty() {
        return Err(BackendError::new(
            "empty_export",
            "当前范围没有可导出的内容",
        ));
    }
    let mut warning = None;
    let bytes = match input.format {
        ExportFormat::Md => formats::markdown(&document).into_bytes(),
        // A-17：缺字形替换占比超阈值时向导出回执带出警告，不再静默降质
        ExportFormat::Pdf => {
            let (bytes, notice) = pdf::render(&document)?;
            warning = notice;
            bytes
        }
        ExportFormat::Ics => formats::ics(&document)?.into_bytes(),
        ExportFormat::Apkg => anki::render(state, &document)?,
        ExportFormat::Html => formats::html(&document).into_bytes(),
        ExportFormat::Csv => formats::csv(&document).into_bytes(),
        ExportFormat::Txt => formats::text(&document).into_bytes(),
        ExportFormat::Opml => formats::opml(&document).into_bytes(),
    };
    if bytes.is_empty() || bytes.len() > MAX_EXPORT_BYTES {
        return Err(BackendError::new(
            "export_size_invalid",
            "导出内容为空或超过 64 MB 上限",
        ));
    }
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case(input.format.extension()))
    {
        return Err(BackendError::invalid(
            "invalid_export_destination",
            "导出文件扩展名必须与所选格式一致",
        ));
    }
    materials::atomic_export(destination, &bytes)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let event = Store::event(
        &tx,
        "content.exported",
        serde_json::json!({"format": input.format.extension(), "bytes": bytes.len(), "sha256": sha256}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(ExportResult {
        bytes: bytes.len(),
        sha256,
        format: input.format,
        title: document.title,
        warning,
    })
}
