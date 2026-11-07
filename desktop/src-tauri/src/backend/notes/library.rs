use super::super::error::{BackendError, Result};
use super::super::{learning, materials, BackendState};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeLink {
    pub plan_id: String,
    pub stage_id: String,
    pub node_id: String,
    pub title: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NoteKnowledge {
    pub note_id: i64,
    pub links: Vec<KnowledgeLink>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryIssue {
    pub id: String,
    pub severity: String,
    pub kind: String,
    pub title: String,
    pub detail: String,
    pub note_id: Option<i64>,
    pub material_id: Option<i64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryOverview {
    pub notes: Vec<NoteKnowledge>,
    pub issues: Vec<LibraryIssue>,
    pub linked_nodes: usize,
    pub duplicate_note_groups: usize,
    pub unlinked_notes: usize,
    pub missing_originals: usize,
    pub failed_materials: usize,
}

fn issue(
    id: String,
    severity: &str,
    kind: &str,
    title: String,
    detail: String,
    note_id: Option<i64>,
    material_id: Option<i64>,
) -> LibraryIssue {
    LibraryIssue {
        id,
        severity: severity.into(),
        kind: kind.into(),
        title,
        detail,
        note_id,
        material_id,
    }
}

pub fn overview(state: &BackendState) -> Result<LibraryOverview> {
    let guard = state
        .store
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "本地数据层暂不可用"))?;
    let note_values = guard.collection("notes")?.value;
    let note_values = note_values
        .as_array()
        .ok_or_else(|| BackendError::new("invalid_record", "笔记集合格式无效"))?;
    let mut notes = BTreeMap::new();
    let mut contents: BTreeMap<String, Vec<(i64, String)>> = BTreeMap::new();
    let mut issues = Vec::new();
    for note in note_values {
        let id = note
            .get("id")
            .and_then(Value::as_i64)
            .ok_or_else(|| BackendError::new("invalid_record", "笔记标识无效"))?;
        let title = note
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("未命名笔记")
            .to_string();
        let content = note
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        notes.insert(
            id,
            NoteKnowledge {
                note_id: id,
                links: Vec::new(),
            },
        );
        contents.entry(content).or_default().push((id, title));
    }
    let mut statement = guard.conn.prepare("SELECT note_id, plan_id, stage_id FROM learning_annotations ORDER BY note_id, plan_id, stage_id")?;
    let annotations = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut linked_nodes = BTreeSet::new();
    let mut annotated_notes = BTreeSet::new();
    for (note_id, plan_id, stage_id) in annotations {
        annotated_notes.insert(note_id);
        let Some(note) = notes.get_mut(&note_id) else {
            issues.push(issue(
                format!("orphan-annotation:{note_id}"),
                "warning",
                "orphan_annotation",
                "关联指向已删除笔记".into(),
                format!("批注记录 #{note_id} 没有对应笔记，不计入知识关联。"),
                Some(note_id),
                None,
            ));
            continue;
        };
        // 25#10：方案行损坏/缺失只降级为本条 issue，不再上抛——同函数对孤儿
        // 批注、无结果方案均降级，单条坏数据不应放大为整个知识库检查失败
        let plan = match learning::get(&state.learning, &format!("plan-{plan_id}")) {
            Ok(plan) => plan,
            Err(_) => {
                issues.push(issue(
                    format!("plan-unreadable:{note_id}"),
                    "warning",
                    "plan_unreadable",
                    "关联方案数据不可读".into(),
                    format!(
                        "笔记「{}」关联的方案 #{plan_id} 无法读取，已跳过该条关联。",
                        note_values
                            .iter()
                            .find(|note| note["id"].as_i64() == Some(note_id))
                            .and_then(|note| note["title"].as_str())
                            .unwrap_or("未命名笔记")
                    ),
                    Some(note_id),
                    None,
                ));
                continue;
            }
        };
        let Some(result) = plan.result else {
            issues.push(issue(
                format!("pending-plan:{note_id}"),
                "info",
                "pending_plan",
                "关联方案尚未形成完整结果".into(),
                format!(
                    "笔记「{}」的关联将在方案完成后重新计算。",
                    note_values
                        .iter()
                        .find(|note| note["id"].as_i64() == Some(note_id))
                        .and_then(|note| note["title"].as_str())
                        .unwrap_or("未命名笔记")
                ),
                Some(note_id),
                None,
            ));
            continue;
        };
        let Some(stage) = result.stages.iter().find(|stage| stage.id == stage_id) else {
            issues.push(issue(
                format!("missing-stage:{note_id}:{plan_id}:{stage_id}"),
                "warning",
                "orphan_annotation",
                "关联指向不存在的阶段".into(),
                format!("方案 plan-{plan_id} 中没有阶段 {stage_id}。"),
                Some(note_id),
                None,
            ));
            continue;
        };
        for node_id in &stage.node_ids {
            let Some(node) = result.graph.nodes.iter().find(|node| &node.id == node_id) else {
                issues.push(issue(
                    format!("missing-node:{note_id}:{plan_id}:{node_id}"),
                    "warning",
                    "orphan_annotation",
                    "关联指向不存在的知识节点".into(),
                    format!("阶段 {} 中没有知识节点 {}。", stage.title, node_id),
                    Some(note_id),
                    None,
                ));
                continue;
            };
            linked_nodes.insert((plan_id, node_id.clone()));
            note.links.push(KnowledgeLink {
                plan_id: format!("plan-{plan_id}"),
                stage_id: stage_id.clone(),
                node_id: node_id.clone(),
                title: node.title.clone(),
            });
        }
    }
    drop(guard);
    let mut duplicate_note_groups = 0;
    for group in contents.into_values().filter(|group| group.len() > 1) {
        duplicate_note_groups += 1;
        let titles = group
            .iter()
            .take(4)
            .map(|(_, title)| title.as_str())
            .collect::<Vec<_>>()
            .join("、");
        issues.push(issue(
            format!(
                "duplicate-notes:{}",
                group
                    .iter()
                    .map(|(id, _)| id.to_string())
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            "warning",
            "duplicate_notes",
            format!("{} 条笔记正文完全相同", group.len()),
            format!("{titles}。仅提示完全相同的正文，不判断是否应当删除。"),
            group.first().map(|(id, _)| *id),
            None,
        ));
    }
    let mut unlinked_notes = 0;
    for note in notes.values() {
        if note.links.is_empty() && !annotated_notes.contains(&note.note_id) {
            unlinked_notes += 1;
            let title = note_values
                .iter()
                .find(|value| value["id"].as_i64() == Some(note.note_id))
                .and_then(|value| value["title"].as_str())
                .unwrap_or("未命名笔记")
                .to_string();
            issues.push(issue(
                format!("unlinked-note:{}", note.note_id),
                "info",
                "unlinked_note",
                "未关联学习阶段".into(),
                format!("「{title}」是自由笔记；可在学习空间对应阶段保存批注以建立真实关联。"),
                Some(note.note_id),
                None,
            ));
        }
    }
    let mut missing_originals = 0;
    let mut failed_materials = 0;
    for material in materials::list(&state.materials)? {
        if !material.original_available && material.status != "metadata_only" {
            missing_originals += 1;
            issues.push(issue(
                format!("missing-original:{}", material.id),
                "warning",
                "missing_original",
                "资料原件不可用".into(),
                format!(
                    "「{}」的原件不存在；已保存的解析文本不因此删除。",
                    material.title
                ),
                None,
                Some(material.id),
            ));
        }
        if material.status == "failed" {
            failed_materials += 1;
            let detail = material
                .error
                .map(|error| format!("{}：{}", error.code, error.message))
                .unwrap_or_else(|| "原生解析返回失败状态。".into());
            issues.push(issue(
                format!("failed-material:{}", material.id),
                "error",
                "parse_failed",
                format!("「{}」解析失败", material.title),
                detail,
                None,
                Some(material.id),
            ));
        }
    }
    Ok(LibraryOverview {
        notes: notes.into_values().collect(),
        issues,
        linked_nodes: linked_nodes.len(),
        duplicate_note_groups,
        unlinked_notes,
        missing_originals,
        failed_materials,
    })
}
