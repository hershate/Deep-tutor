use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::store::Store;
use super::*;
use rusqlite::params;
use serde_json::{json, Value};

pub(crate) fn text(value: &str, maximum: usize) -> String {
    if value.len() <= maximum {
        return value.to_string();
    }
    let mut end = maximum;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_string()
}

pub fn adopt<R: Runtime>(app: &AppHandle<R>, state: &LearningState, id: &str) -> Result<Value> {
    let id = plan_id(id)?;
    let plan = get(state, &format!("plan-{id}"))?;
    if !["done", "degraded"].contains(&plan.status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "只有已完成或明确降级的方案可以采用",
        ));
    }
    let result = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案尚未形成可采用的完整结果"))?;
    let store = state.store()?;
    if store
        .conn
        .query_row(
            "SELECT project_id FROM learning_plan_adoptions WHERE plan_id = ?1",
            params![id],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
        .is_some()
    {
        return store.app_state();
    }
    let tx = store.conn.unchecked_transaction()?;
    let project_id = Store::next_free_id(&tx, "projects")?
        .parse::<i64>()
        .map_err(|_| BackendError::new("storage_failed", "无法分配项目标识"))?;
    let milestones = result
        .stages
        .iter()
        .enumerate()
        .map(|(index, stage)| {
            json!({
                "id": index + 1, "title": stage.title, "done": false,
            })
        })
        .collect::<Vec<_>>();
    Store::insert_json(
        &tx,
        "projects",
        &project_id.to_string(),
        &json!({
            "id": project_id,
            "title": text(&result.goal, 500),
            "description": text(&format!("目标：{}\n当前基础：{}\n来源方案：plan-{id}", result.goal, result.level), 10000),
            "due": result.deadline,
            "progress": 0,
            "tag": "学习方案",
            "milestones": milestones,
        }),
    )?;
    let mut task_ids = Vec::new();
    for entry in &result.schedule.entries {
        let stage = result
            .stages
            .iter()
            .find(|stage| stage.id == entry.stage_id)
            .ok_or_else(|| BackendError::new("invalid_persisted_plan", "排期引用了不存在的阶段"))?;
        let task_id = Store::next_free_id(&tx, "tasks")?
            .parse::<i64>()
            .map_err(|_| BackendError::new("storage_failed", "无法分配任务标识"))?;
        let titles = entry
            .node_ids
            .iter()
            .filter_map(|node_id| result.graph.nodes.iter().find(|node| &node.id == node_id))
            .map(|node| node.title.as_str())
            .collect::<Vec<_>>()
            .join("、");
        Store::insert_json(
            &tx,
            "tasks",
            &task_id.to_string(),
            &json!({
                "id": task_id,
                "title": text(&format!("{} · {}", stage.title, titles), 500),
                "subject": text(&result.goal, 100),
                "duration": entry.minutes,
                "done": false,
                "kind": "学习",
            }),
        )?;
        tx.execute("INSERT INTO learning_plan_schedule_tasks (plan_id, task_id, date, stage_id, node_ids_json, minutes) VALUES (?1, ?2, ?3, ?4, ?5, ?6)", params![id, task_id, entry.date, entry.stage_id, serde_json::to_string(&entry.node_ids)?, entry.minutes])?;
        task_ids.push(task_id);
    }
    tx.execute(
        "INSERT INTO learning_plan_adoptions (plan_id, project_id, created_at) VALUES (?1, ?2, ?3)",
        params![id, project_id, now()?],
    )?;
    Store::next_revision(&tx, "tasks")?;
    Store::next_revision(&tx, "projects")?;
    let event = Store::event(
        &tx,
        "plan.adopted",
        json!({"planId": format!("plan-{id}"), "projectId": project_id, "taskIds": task_ids}),
    )?;
    tx.commit()?;
    emit(app, &event);
    store.app_state()
}

pub fn regenerate<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: &str,
    scope: u8,
) -> Result<Plan> {
    let id = plan_id(id)?;
    if !(2..=6).contains(&scope) {
        return Err(BackendError::invalid(
            "invalid_regeneration_scope",
            "局部重生成仅支持知识图谱、阶段设计、资源检索、学习排期和五维校验",
        ));
    }
    let plan = get(state, &format!("plan-{id}"))?;
    if !["done", "degraded"].contains(&plan.status.as_str()) {
        return Err(BackendError::new(
            "plan_not_ready",
            "只有完成或降级的方案可以局部重生成",
        ));
    }
    if plan.adopted_project_id.is_some() {
        return Err(BackendError::new(
            "plan_adopted",
            "已采用的方案不能直接重生成，请保留历史并创建新方案",
        ));
    }
    let regeneration_limit = providers::usage::settings(&state.providers)?.regeneration_limit;
    if plan.version > u32::from(regeneration_limit) {
        return Err(BackendError::new(
            "regeneration_limit",
            &format!("每个方案最多局部重生成 {regeneration_limit} 次"),
        ));
    }
    let result = plan
        .result
        .ok_or_else(|| BackendError::new("plan_not_ready", "方案缺少可保留的历史结果"))?;
    register(state, id)?;
    let outcome = (|| -> Result<_> {
        let store = state.store()?;
        let tx = store.conn.unchecked_transaction()?;
        // 34#3：OR REPLACE——apply_intensity（31#16）也会在同版本落「覆盖前快照」，
        // 裸 INSERT 撞 (plan_id, version) 主键使切过强度的方案永久无法重生成
        tx.execute("INSERT OR REPLACE INTO learning_plan_versions (plan_id, version, result_json, created_at) VALUES (?1, ?2, ?3, ?4)", params![id, plan.version, serde_json::to_string(&result)?, now()?])?;
        for index in scope..STEP_NAMES.len() as u8 {
            tx.execute("UPDATE learning_plans SET artifacts_json = json_remove(artifacts_json, ?1) WHERE id = ?2", params![format!("$.step{index}"), id])?;
        }
        tx.execute("UPDATE learning_plan_steps SET status = 'pending', request_id = NULL, output_json = NULL, error_code = NULL, error_message = NULL, updated_at = ?1 WHERE plan_id = ?2 AND step_index >= ?3", params![now()?, id, scope])?;
        tx.execute("UPDATE learning_plans SET status = 'running', result_json = NULL, current_step = ?1, version = version + 1, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE id = ?3", params![scope, now()?, id])?;
        let event = Store::event(
            &tx,
            "plan.regeneration_started",
            json!({"planId": format!("plan-{id}"), "scope": scope, "version": plan.version + 1}),
        )?;
        tx.commit()?;
        Ok(event)
    })();
    match outcome {
        Ok(event) => {
            emit(app, &event);
            if let Err(error) = launch(app.clone(), state, id) {
                if let Ok(mut active) = state.active.lock() {
                    active.remove(&id);
                }
                let task_error = BackendError::new("task_start_failed", "局部重生成未能启动");
                let _ = fail_plan(app, state, id, task_error);
                return Err(error);
            }
            get(state, &format!("plan-{id}"))
        }
        Err(error) => {
            if let Ok(mut active) = state.active.lock() {
                active.remove(&id);
            }
            Err(error)
        }
    }
}
