use super::super::error::{BackendError, Result};
use super::super::providers::{self, ChatMessage};
use super::validate;
use super::*;
use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use std::sync::atomic::Ordering;

pub(crate) const MODEL_SYSTEM: &str = "你是知舟 Deep Tutor 的学习方案生成器。你是文字模型，只能依据给定资料和明确输入工作。只输出一个 JSON 对象，不要 Markdown、代码围栏或额外解释。不要编造引用、链接、用量或已完成步骤。";

#[derive(Default)]
struct RunArtifacts {
    sources: Option<Vec<Source>>,
    graph: Option<Graph>,
    stages: Option<Vec<Stage>>,
    scheduled_stages: Option<Vec<ScheduledStage>>,
    resources: Option<Value>,
    schedule: Option<Schedule>,
    validation: Option<ValidationReport>,
}

impl RunArtifacts {
    fn absorb(&mut self, artifact: Artifact) {
        match artifact {
            Artifact::None => {}
            Artifact::Sources(sources) => self.sources = Some(sources),
            Artifact::Graph(graph) => self.graph = Some(graph),
            Artifact::Stages(stages) => self.stages = Some(stages),
            Artifact::Resources(resources) => self.resources = Some(resources),
            Artifact::Schedule(schedule, stages) => {
                self.schedule = Some(schedule);
                self.scheduled_stages = Some(stages);
            }
            Artifact::Validation(validation) => self.validation = Some(validation),
        }
    }
}

fn cancelled(state: &LearningState, id: i64) -> Result<()> {
    let cancelled = state
        .active
        .lock()
        .map_err(|_| BackendError::new("store_unavailable", "学习生成任务状态不可用"))?
        .get(&id)
        .map(|flag| flag.load(Ordering::Acquire))
        .unwrap_or(false);
    if cancelled {
        Err(BackendError::new("cancelled", "学习方案生成已取消"))
    } else {
        Ok(())
    }
}

fn sources(state: &LearningState, input: &PlanInput) -> Result<Vec<Source>> {
    let mut sources = Vec::new();
    let mut total_bytes = 0usize;
    for id in &input.material_ids {
        let detail = super::super::materials::detail(&state.materials, *id)?;
        if !["parsed", "degraded"].contains(&detail.material.status.as_str()) {
            return Err(BackendError::new(
                "material_not_parsed",
                &format!("资料 {} 尚未完成文本解析", detail.material.title),
            ));
        }
        let text = detail
            .text
            .filter(|text| !text.trim().is_empty())
            .ok_or_else(|| {
                BackendError::new(
                    "material_text_missing",
                    &format!("资料 {} 没有可用于生成的文本", detail.material.title),
                )
            })?;
        total_bytes = total_bytes.saturating_add(text.len());
        if total_bytes > 192 * 1024 {
            return Err(BackendError::new(
                "context_too_large",
                "所选资料文本超过 192 KB，请减少资料或选择较小章节后重试",
            ));
        }
        sources.push(Source {
            id: detail.material.id,
            title: detail.material.title,
            sha256: detail.material.sha256,
            text,
            warning: detail.material.warning,
        });
    }
    Ok(sources)
}

fn resources(input: &PlanInput, sources: &[Source]) -> Value {
    json!({
        "items": sources.iter().map(|source| json!({
            "materialId": source.id, "title": source.title, "sha256": source.sha256,
            "kind": "material", "verified": true,
        })).collect::<Vec<_>>(),
        "externalSearch": {
            "status": "unavailable",
            "reason": "尚未配置原生外部资源检索，未生成或验证任何外部链接",
            "queries": [input.goal],
        }
    })
}

fn graph_prompt(input: &PlanInput, sources: &[Source]) -> Result<String> {
    Ok(format!(
        "构建知识图谱。只返回 {{\"nodes\":[...],\"edges\":[...]}}。\nnodes 每项必须是 {{\"id\":\"小写英文短横线标识\",\"title\":\"...\",\"summary\":\"...\",\"difficulty\":1到5,\"required\":true或false,\"estimatedMinutes\":5到600,\"citations\":[{{\"materialId\":资料数字ID,\"quote\":\"该资料中连续存在的原文\"}}]}}。edges 每项是 {{\"from\":\"前置节点id\",\"to\":\"后续节点id\"}}。\n节点总数不得超过 100，每个节点至少一条真实引用，不得生成资料之外的引用或 URL。根据目标与当前基础选择必学内容，不虚构已掌握程度。\n学习目标：{}\n当前基础：{}\n资料快照（JSON）：{}",
        serde_json::to_string(&input.goal)?,
        serde_json::to_string(&input.level)?,
        serde_json::to_string(sources)?,
    ))
}

fn stages_prompt(input: &PlanInput, graph: &Graph) -> Result<String> {
    Ok(format!(
        "根据已校验的知识图谱设计渐进学习阶段。只返回 {{\"stages\":[...]}}。每阶段为 {{\"id\":\"阶段标识\",\"title\":\"...\",\"nodeIds\":[\"图谱节点id\"],\"difficulty\":1到5}}。\n设计 3–6 个阶段，每阶段 3–6 个节点，覆盖全部 required 节点且不重复分配；所有前置节点必须位于更早阶段，难度不得下降且相邻阶段跳级不得超过 2。不要新增图谱节点或引用。\n学习目标：{}\n当前基础：{}\n每日分钟数：{}\n知识图谱：{}",
        serde_json::to_string(&input.goal)?,
        serde_json::to_string(&input.level)?,
        input.daily_minutes,
        serde_json::to_string(graph)?,
    ))
}

fn validation_prompt(
    input: &PlanInput,
    graph: &Graph,
    stages: &[Stage],
    schedule: &Schedule,
) -> Result<String> {
    Ok(format!(
        "校验这份学习方案，只返回 {{\"report\":{{\"dependency_closure\":{{\"status\":\"pass|warn|fail\",\"details\":\"...\"}},\"difficulty_step\":{{...}},\"cognitive_load\":{{...}},\"milestone_feasibility\":{{...}},\"gap_analysis\":{{...}}}},\"passed\":true或false,\"fixes\":[\"...\"]}}。\n五项分别是：前置是否都在更早阶段、相邻难度跳级是否超过 2、每阶段是否为 3–6 节点、目标能否基于当前基础与前置知识达成、是否存在未解释术语或学习跳步。只根据给定数据判断，不要虚构学习证据或引用。里程碑日期容量由原生排期另行计算，不凭猜测宣布能按期完成。\n目标：{}\n当前基础：{}\n截止日：{}\n里程碑说明：{}\n里程碑日期（最后阶段完成日不得晚于该日）：{}\n图谱：{}\n阶段：{}\n排期：{}",
        serde_json::to_string(&input.goal)?,
        serde_json::to_string(&input.level)?,
        input.deadline,
        serde_json::to_string(&input.milestone)?,
        serde_json::to_string(&input.milestone_date)?,
        serde_json::to_string(graph)?,
        serde_json::to_string(stages)?,
        serde_json::to_string(schedule)?,
    ))
}

pub(crate) fn parse_model_json<T: DeserializeOwned>(output: &str) -> Result<T> {
    let output = output.trim();
    let content = if output.starts_with("```") {
        let mut lines = output.lines();
        let _ = lines.next();
        let body = lines.collect::<Vec<_>>().join("\n");
        body.trim()
            .strip_suffix("```")
            .map(str::trim)
            .map(str::to_string)
            .ok_or_else(|| BackendError::new("invalid_model_output", "模型代码围栏未闭合"))?
    } else {
        output.to_string()
    };
    serde_json::from_str(&content)
        .map_err(|_| BackendError::new("invalid_model_output", "模型输出不是符合约定结构的 JSON"))
}

fn model_json<T, F>(
    app: &AppHandle<impl Runtime>,
    state: &LearningState,
    id: i64,
    index: usize,
    input: &PlanInput,
    prompt: String,
    max_tokens: u32,
    validate_output: F,
) -> Result<T>
where
    T: DeserializeOwned,
    F: Fn(&T) -> Result<()> + Copy,
{
    let provider_id = input
        .provider_id
        .as_deref()
        .ok_or_else(|| BackendError::new("provider_not_configured", "方案缺少模型供应商"))?;
    let mut last_error = None;
    for retry in 0..2 {
        cancelled(state, id)?;
        let (_, request_id) = begin_step(app, state, id, index)?;
        let request_id = request_id
            .ok_or_else(|| BackendError::new("storage_failed", "模型步骤缺少请求标识"))?;
        let content = if retry == 0 {
            prompt.clone()
        } else {
            format!(
                "{prompt}\n上一次输出未通过原生结构校验，请修正后重新输出完整 JSON。错误：{}",
                last_error
                    .as_ref()
                    .map(|error: &BackendError| error.message.as_str())
                    .unwrap_or("结构不符合要求")
            )
        };
        let messages = vec![
            ChatMessage {
                role: "system".into(),
                content: MODEL_SYSTEM.into(),
                ..Default::default()
            },
            ChatMessage {
                role: "user".into(),
                content,
                ..Default::default()
            },
        ];
        let request = match providers::routing::stream_chat(
            app,
            &state.providers,
            &request_id,
            "plan",
            Some(provider_id),
            &messages,
            max_tokens,
            None,
            None,
            false,
        ) {
            Ok(request) => request,
            Err(error) => {
                fail_step(app, state, id, index, &error)?;
                return Err(error);
            }
        };
        if request.status != "done" {
            let error = request
                .error
                .unwrap_or_else(|| BackendError::new("provider_error", "模型请求未成功完成"));
            fail_step(app, state, id, index, &error)?;
            return Err(error);
        }
        let parsed = parse_model_json::<T>(&request.output).and_then(|output| {
            validate_output(&output)?;
            Ok(output)
        });
        match parsed {
            Ok(output) => return Ok(output),
            Err(error) => {
                fail_step(app, state, id, index, &error)?;
                last_error = Some(error);
            }
        }
    }
    Err(last_error
        .unwrap_or_else(|| BackendError::new("invalid_model_output", "模型输出未通过校验")))
}

fn artifact_data(index: usize, artifact: &Artifact, input: &PlanInput) -> Result<Value> {
    match (index, artifact) {
        (0, Artifact::Sources(sources)) => Ok(json!({"snapshot": sources})),
        (1, Artifact::None) => Ok(json!({"input": input})),
        (2, Artifact::Graph(graph)) => Ok(json!({"graph": graph})),
        (3, Artifact::Stages(stages)) => Ok(json!({"stages": stages})),
        (4, Artifact::Resources(resources)) => Ok(json!({"resources": resources})),
        (5, Artifact::Schedule(schedule, stages)) => {
            Ok(json!({"schedule": schedule, "stages": stages}))
        }
        (6, Artifact::Validation(validation)) => Ok(json!({"validation": validation})),
        _ => Err(BackendError::new(
            "storage_failed",
            "学习方案步骤产物类型不一致",
        )),
    }
}

fn persisted_artifact(index: usize, output: &Value) -> Result<Artifact> {
    match index {
        0 => Ok(Artifact::Sources(serde_json::from_value(
            output.get("snapshot").cloned().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "已保存的资料快照缺失")
            })?,
        )?)),
        1 => Ok(Artifact::None),
        2 => Ok(Artifact::Graph(serde_json::from_value(
            output.get("graph").cloned().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "已保存的知识图谱缺失")
            })?,
        )?)),
        3 => Ok(Artifact::Stages(serde_json::from_value(
            output.get("stages").cloned().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "已保存的学习阶段缺失")
            })?,
        )?)),
        4 => Ok(Artifact::Resources(
            output.get("resources").cloned().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "已保存的资源结果缺失")
            })?,
        )),
        5 => {
            Ok(Artifact::Schedule(
                serde_json::from_value(output.get("schedule").cloned().ok_or_else(|| {
                    BackendError::new("invalid_persisted_plan", "已保存的排期缺失")
                })?)?,
                serde_json::from_value(output.get("stages").cloned().ok_or_else(|| {
                    BackendError::new("invalid_persisted_plan", "已保存的阶段排期缺失")
                })?)?,
            ))
        }
        6 => Ok(Artifact::Validation(serde_json::from_value(
            output.get("validation").cloned().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "已保存的校验报告缺失")
            })?,
        )?)),
        _ => Err(BackendError::new(
            "invalid_persisted_plan",
            "未知的学习方案步骤",
        )),
    }
}

fn step_output(state: &LearningState, id: i64, index: usize) -> Result<Value> {
    let store = state.store()?;
    let output = store
        .conn
        .query_row(
            "SELECT output_json FROM learning_plan_steps WHERE plan_id = ?1 AND step_index = ?2",
            params![id, index as i64],
            |row| row.get::<_, Option<String>>(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "学习方案步骤不存在"))?;
    serde_json::from_str(
        &output.ok_or_else(|| BackendError::new("invalid_persisted_plan", "已完成步骤缺少产物"))?,
    )
    .map_err(Into::into)
}

fn finish_result(input: &PlanInput, artifacts: RunArtifacts) -> Result<PlanResult> {
    let graph = artifacts
        .graph
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少知识图谱"))?;
    let stages = artifacts
        .scheduled_stages
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少阶段排期"))?;
    let schedule = artifacts
        .schedule
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少确定性排期"))?;
    let validation = artifacts
        .validation
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少五维校验"))?;
    let resources = artifacts
        .resources
        .ok_or_else(|| BackendError::new("invalid_persisted_plan", "方案缺少资源结果"))?;
    let mut warnings = artifacts
        .sources
        .unwrap_or_default()
        .iter()
        .filter_map(|source| {
            source
                .warning
                .as_ref()
                .map(|warning| format!("资料 {}：{warning}", source.title))
        })
        .collect::<Vec<_>>();
    if let Some(reason) = resources
        .get("externalSearch")
        .and_then(|search| search.get("reason"))
        .and_then(Value::as_str)
    {
        warnings.push(reason.into());
    }
    if !schedule.within_deadline {
        warnings.push(format!(
            "排期结束日为 {}，晚于截止日 {}",
            schedule.end_date, input.deadline
        ));
    }
    if schedule.within_milestone == Some(false) {
        warnings.push(format!(
            "最后阶段完成日 {} 晚于硬里程碑日期 {}",
            schedule.end_date,
            schedule.milestone_date.as_deref().unwrap_or("未设置")
        ));
    }
    if !validation.passed {
        warnings.push("五维校验未全部通过，请查看各项状态与修复建议".into());
    }
    Ok(PlanResult {
        goal: input.goal.clone(),
        level: input.level.clone(),
        deadline: input.deadline.clone(),
        milestone: input.milestone.clone(),
        milestone_date: input.milestone_date.clone(),
        graph,
        stages,
        schedule,
        resources,
        validation,
        warnings,
    })
}

fn execute_index<R: Runtime>(
    app: &AppHandle<R>,
    state: &LearningState,
    id: i64,
    index: usize,
    input: &PlanInput,
    artifacts: &RunArtifacts,
) -> Result<Artifact> {
    match index {
        0 => Ok(Artifact::Sources(sources(state, input)?)),
        1 => Ok(Artifact::None),
        2 => {
            let sources = artifacts.sources.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "图谱步骤缺少资料快照")
            })?;
            let graph = model_json(
                app,
                state,
                id,
                index,
                input,
                graph_prompt(input, sources)?,
                8192,
                |graph| validate::validate_graph(graph, sources),
            )?;
            Ok(Artifact::Graph(graph))
        }
        3 => {
            let graph = artifacts.graph.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "阶段步骤缺少知识图谱")
            })?;
            let draft: StageDraft = model_json(
                app,
                state,
                id,
                index,
                input,
                stages_prompt(input, graph)?,
                4096,
                |draft: &StageDraft| validate::validate_stages(graph, &draft.stages),
            )?;
            Ok(Artifact::Stages(draft.stages))
        }
        4 => {
            let sources = artifacts.sources.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "资源步骤缺少资料快照")
            })?;
            Ok(Artifact::Resources(resources(input, sources)))
        }
        5 => {
            let graph = artifacts.graph.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "排期步骤缺少知识图谱")
            })?;
            let stages = artifacts.stages.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "排期步骤缺少学习阶段")
            })?;
            let (schedule, scheduled_stages) = validate::schedule(
                graph,
                stages,
                input.daily_minutes,
                validate::today(),
                validate::deadline(&input.deadline)?,
                input
                    .milestone_date
                    .as_deref()
                    .map(validate::deadline)
                    .transpose()?,
            )?;
            Ok(Artifact::Schedule(schedule, scheduled_stages))
        }
        6 => {
            let graph = artifacts.graph.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "校验步骤缺少知识图谱")
            })?;
            let stages = artifacts.stages.as_ref().ok_or_else(|| {
                BackendError::new("invalid_persisted_plan", "校验步骤缺少学习阶段")
            })?;
            let schedule = artifacts
                .schedule
                .as_ref()
                .ok_or_else(|| BackendError::new("invalid_persisted_plan", "校验步骤缺少排期"))?;
            let model = match model_json(
                app,
                state,
                id,
                index,
                input,
                validation_prompt(input, graph, stages, schedule)?,
                2048,
                validate::validate_model,
            ) {
                Ok(model) => model,
                Err(error) if error.code == "invalid_model_output" => validate::unavailable_model(),
                Err(error) => return Err(error),
            };
            Ok(Artifact::Validation(validate::validation_report(
                graph, stages, schedule, &model,
            )))
        }
        _ => Err(BackendError::new(
            "invalid_persisted_plan",
            "未知的学习方案步骤",
        )),
    }
}

pub fn run<R: Runtime>(app: &AppHandle<R>, state: &LearningState, id: i64) -> Result<()> {
    let input = input(state, id)?;
    if step_state(state, id, 7)?.0 == "done" {
        if let Some(result) = get(state, &format!("plan-{id}"))?.result {
            return complete_plan(app, state, id, &result);
        }
    }
    let mut artifacts = RunArtifacts::default();
    for index in 0..7 {
        cancelled(state, id)?;
        if step_state(state, id, index)?.0 == "done" {
            let artifact = persisted_artifact(index, &step_output(state, id, index)?)?;
            artifacts.absorb(artifact);
            continue;
        }
        if ![2, 3, 6].contains(&index) {
            begin_step(app, state, id, index)?;
        }
        let artifact = match execute_index(app, state, id, index, &input, &artifacts) {
            Ok(artifact) => artifact,
            Err(error) => {
                if step_state(state, id, index)?.0 == "running" {
                    fail_step(app, state, id, index, &error)?;
                }
                return Err(error);
            }
        };
        let data = artifact_data(index, &artifact, &input)?;
        complete_step(app, state, id, index, data)?;
        artifacts.absorb(artifact);
    }
    cancelled(state, id)?;
    let result = finish_result(&input, artifacts)?;
    begin_step(app, state, id, 7)?;
    complete_step(app, state, id, 7, json!({"result": result}))?;
    complete_plan(app, state, id, &result)
}
