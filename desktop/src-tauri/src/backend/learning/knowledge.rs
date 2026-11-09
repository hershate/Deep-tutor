use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::store::Store;
use super::*;
use rusqlite::params;
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, HashSet, VecDeque};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NodeProgress {
    pub node_id: String,
    pub mastery: Option<u8>,
    pub evidence_count: usize,
    pub reading_completed: bool,
    pub evidence: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeOverview {
    pub nodes: Vec<NodeProgress>,
    pub overall_mastery: Option<u8>,
    pub evaluated_nodes: usize,
    pub missing_evidence: usize,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReadingProgress {
    pub completed: bool,
    pub bookmarked: bool,
}

pub fn progress(state: &LearningState, id: &str) -> Result<KnowledgeOverview> {
    let id = plan_id(id)?;
    let plan = get(state, &format!("plan-{id}"))?;
    let result = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案尚未形成知识图谱"))?;
    let store = state.store()?;
    // 77#7：掌握度为全历史证据的等权平均（无时间衰减/最新优先）——设计
    // 语义：长期未复习的早期低分会拉低当前估计，报告推荐同口径排序
    // 77#1：行级过滤进 SQL（与报告页 evidence() 同口径）——原先 SUM/SUM 池化
    // 全部行，单条脏行（score>max）使池化值超界、整个节点从掌握度消失
    let mut statement = store.conn.prepare("SELECT node_id, COUNT(*), SUM(score), SUM(max_score) FROM learning_node_evidence WHERE plan_id = ?1 AND max_score > 0 AND score >= 0 AND score <= max_score GROUP BY node_id")?;
    let rows = statement.query_map(params![id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)? as usize,
            row.get::<_, f64>(2)?,
            row.get::<_, f64>(3)?,
        ))
    })?;
    let mut evidence = HashMap::new();
    for row in rows {
        let (node, count, score, maximum) = row?;
        if score.is_finite()
            && maximum.is_finite()
            && maximum > 0.0
            && score >= 0.0
            && score <= maximum
        {
            evidence.insert(node, (count, score, maximum));
        }
    }
    let mut statement = store.conn.prepare(
        "SELECT stage_id FROM learning_reading_progress WHERE plan_id = ?1 AND completed = 1",
    )?;
    let rows = statement.query_map(params![id], |row| row.get::<_, String>(0))?;
    let completed = rows.collect::<std::result::Result<HashSet<_>, _>>()?;
    let mut overall_score = 0.0;
    let mut overall_maximum = 0.0;
    let mut evaluated = 0;
    let nodes = result
        .graph
        .nodes
        .iter()
        .map(|node| {
            let reading = result
                .stages
                .iter()
                .any(|stage| stage.node_ids.contains(&node.id) && completed.contains(&stage.id));
            let (count, score, maximum) = evidence.get(&node.id).copied().unwrap_or((0, 0.0, 0.0));
            let mastery = (count > 0).then(|| ((score / maximum * 100.0).round() as u8).min(100));
            if mastery.is_some() {
                evaluated += 1;
                overall_score += score;
                overall_maximum += maximum;
            }
            let mut label = if count > 0 {
                format!("{count} 项测评记录 · {score}/{maximum}")
            } else {
                "尚无测评证据".into()
            };
            if reading {
                label.push_str(" · 阅读已完成");
            }
            NodeProgress {
                node_id: node.id.clone(),
                mastery,
                evidence_count: count,
                reading_completed: reading,
                evidence: label,
            }
        })
        .collect::<Vec<_>>();
    let overall_mastery =
        (evaluated > 0).then(|| ((overall_score / overall_maximum * 100.0).round() as u8).min(100));
    Ok(KnowledgeOverview {
        evaluated_nodes: evaluated,
        missing_evidence: nodes.len() - evaluated,
        nodes,
        overall_mastery,
    })
}

pub fn shortest_path(state: &LearningState, id: &str, from: &str, to: &str) -> Result<Vec<String>> {
    let id = plan_id(id)?;
    let plan = get(state, &format!("plan-{id}"))?;
    let graph = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案尚未形成知识图谱"))?
        .graph;
    let known = graph
        .nodes
        .iter()
        .map(|node| node.id.as_str())
        .collect::<HashSet<_>>();
    if !known.contains(from) || !known.contains(to) {
        return Err(BackendError::new("not_found", "补学路径的起点或目标不存在"));
    }
    let mut outgoing: HashMap<&str, Vec<&str>> = HashMap::new();
    for edge in &graph.edges {
        outgoing
            .entry(edge.from.as_str())
            .or_default()
            .push(edge.to.as_str());
    }
    let order = graph
        .nodes
        .iter()
        .enumerate()
        .map(|(index, node)| (node.id.as_str(), index))
        .collect::<HashMap<_, _>>();
    for neighbors in outgoing.values_mut() {
        neighbors.sort_by_key(|id| order.get(*id).copied().unwrap_or(usize::MAX));
    }
    let mut queue = VecDeque::from([from]);
    let mut visited = HashSet::from([from]);
    let mut previous = HashMap::new();
    while let Some(node) = queue.pop_front() {
        if node == to {
            let mut path = vec![to.to_string()];
            let mut current = to;
            while current != from {
                current = previous.get(current).copied().ok_or_else(|| {
                    BackendError::new("invalid_persisted_plan", "补学路径的前驱记录不完整")
                })?;
                path.push(current.to_string());
            }
            path.reverse();
            return Ok(path);
        }
        if let Some(neighbors) = outgoing.get(node) {
            for next in neighbors {
                if visited.insert(*next) {
                    previous.insert(*next, node);
                    queue.push_back(*next);
                }
            }
        }
    }
    Err(BackendError::new(
        "path_not_found",
        "所选节点之间没有可用的前置学习路径",
    ))
}

pub fn add_task<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    node_id: &str,
) -> Result<serde_json::Value> {
    let id = plan_id(id)?;
    let plan = get(state, &format!("plan-{id}"))?;
    let result = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案尚未形成可加入计划的图谱"))?;
    let node = result
        .graph
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .ok_or_else(|| BackendError::new("not_found", "知识节点不存在"))?;
    let store = state.store()?;
    if let Some(existing_task_id) = store
        .conn
        .query_row(
            "SELECT task_id FROM learning_node_tasks WHERE plan_id = ?1 AND node_id = ?2",
            params![id, node_id],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
    {
        // 去重键命中时先确认那条任务还活着；任务已删除则清掉残留行
        // 并照常重建，避免该节点永久无法再建补学任务（10#17）
        let tasks = store.collection("tasks")?.value;
        let alive = tasks
            .as_array()
            .into_iter()
            .flatten()
            .any(|task| task.get("id").and_then(Value::as_i64) == Some(existing_task_id));
        if alive {
            return store.app_state();
        }
        store.conn.execute(
            "DELETE FROM learning_node_tasks WHERE plan_id = ?1 AND node_id = ?2",
            params![id, node_id],
        )?;
    }
    let tx = store.conn.unchecked_transaction()?;
    let task_id = Store::next_free_id(&tx, "tasks")?
        .parse::<i64>()
        .map_err(|_| BackendError::new("invalid_plan_data", "无法分配补学任务标识"))?;
    Store::insert_json(
        &tx,
        "tasks",
        &task_id.to_string(),
        &json!({
            "id": task_id,
            "title": actions::text(&format!("补学 · {}", node.title), 500),
            "subject": actions::text(&result.goal, 100),
            "duration": node.estimated_minutes,
            "done": false,
            "kind": "学习",
        }),
    )?;
    tx.execute("INSERT INTO learning_node_tasks (plan_id, node_id, task_id, created_at) VALUES (?1, ?2, ?3, ?4)", params![id, node_id, task_id, now()?])?;
    Store::next_revision(&tx, "tasks")?;
    let event = Store::event(
        &tx,
        "knowledge.node_task_added",
        json!({"planId": format!("plan-{id}"), "nodeId": node_id, "taskId": task_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    store.app_state()
}

pub fn reading(state: &LearningState, id: &str, stage_id: &str) -> Result<ReadingProgress> {
    let id = plan_id(id)?;
    let store = state.store()?;
    let progress = store.conn.query_row(
        "SELECT completed, bookmarked FROM learning_reading_progress WHERE plan_id = ?1 AND stage_id = ?2",
        params![id, stage_id],
        |row| Ok(ReadingProgress { completed: row.get(0)?, bookmarked: row.get(1)? }),
    ).optional()?.unwrap_or(ReadingProgress { completed: false, bookmarked: false });
    Ok(progress)
}

pub fn set_reading<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    stage_id: &str,
    completed: bool,
    bookmarked: Option<bool>,
) -> Result<ReadingProgress> {
    let id = plan_id(id)?;
    let plan = get(state, &format!("plan-{id}"))?;
    if !plan
        .result
        .as_ref()
        .is_some_and(|result| result.stages.iter().any(|stage| stage.id == stage_id))
    {
        return Err(BackendError::new("not_found", "学习阶段不存在"));
    }
    let previous = reading(state, &id.to_string(), stage_id)?;
    let progress = ReadingProgress {
        completed,
        bookmarked: bookmarked.unwrap_or(previous.bookmarked),
    };
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO learning_reading_progress (plan_id, stage_id, completed, bookmarked, updated_at) VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(plan_id, stage_id) DO UPDATE SET completed = excluded.completed, bookmarked = excluded.bookmarked, updated_at = excluded.updated_at",
        params![id, stage_id, progress.completed, progress.bookmarked, now()?],
    )?;
    let event = Store::event(
        &tx,
        "study.progress_changed",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "completed": progress.completed, "bookmarked": progress.bookmarked}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(progress)
}

pub fn save_note<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    stage_id: &str,
    content: String,
) -> Result<serde_json::Value> {
    let id = plan_id(id)?;
    let content = content.trim();
    if content.is_empty() || content.len() > 100_000 {
        return Err(BackendError::invalid(
            "invalid_note",
            "批注不能为空且不能超过 100000 字节",
        ));
    }
    let plan = get(state, &format!("plan-{id}"))?;
    let stage = plan
        .result
        .as_ref()
        .and_then(|result| result.stages.iter().find(|stage| stage.id == stage_id))
        .ok_or_else(|| BackendError::new("not_found", "学习阶段不存在"))?;
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    // 批注同样占用 notes 配额，与笔记导入/集合替换路径保持一致；
    // 25#18：计数与插入同事务，关闭 check-then-insert 并发窗口
    store.ensure_quota_in_tx(&tx, "notes", 1)?;
    let note_id = Store::next_free_id(&tx, "notes")?
        .parse::<i64>()
        .map_err(|_| BackendError::new("invalid_plan_data", "无法分配批注标识"))?;
    Store::insert_json(
        &tx,
        "notes",
        &note_id.to_string(),
        &json!({
            "id": note_id,
            "title": actions::text(&format!("{} · 学习批注", stage.title), 500),
            "excerpt": actions::text(content, 90),
            "tag": actions::text(&plan.goal, 100),
            "updated": now()?,
            "content": content,
        }),
    )?;
    tx.execute("INSERT INTO learning_annotations (plan_id, stage_id, note_id, created_at) VALUES (?1, ?2, ?3, ?4)", params![id, stage_id, note_id, now()?])?;
    Store::next_revision(&tx, "notes")?;
    let event = Store::event(
        &tx,
        "study.note_saved",
        json!({"planId": format!("plan-{id}"), "stageId": stage_id, "noteId": note_id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    store.app_state()
}
