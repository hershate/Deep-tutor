use super::super::error::{BackendError, Result};
use super::types::*;
use std::collections::{HashMap, HashSet};
use time::format_description::well_known::Iso8601;
use time::{Date, OffsetDateTime};

pub fn today() -> Date {
    OffsetDateTime::now_local()
        .unwrap_or_else(|_| OffsetDateTime::now_utc())
        .date()
}

pub fn deadline(value: &str) -> Result<Date> {
    Date::parse(value, &Iso8601::DATE)
        .map_err(|_| BackendError::invalid("invalid_deadline", "截止日期必须是有效的 YYYY-MM-DD"))
}

pub fn validate_graph(graph: &Graph, sources: &[Source]) -> Result<()> {
    if graph.nodes.is_empty() || graph.nodes.len() > 100 {
        return Err(BackendError::new(
            "invalid_graph",
            "知识节点数量必须位于 1 到 100",
        ));
    }
    let source_map = sources
        .iter()
        .map(|source| (source.id, source))
        .collect::<HashMap<_, _>>();
    let mut ids = HashSet::new();
    for node in &graph.nodes {
        if node.id.is_empty()
            || node.id.len() > 64
            || !node.id.bytes().enumerate().all(|(index, byte)| {
                byte.is_ascii_lowercase() || byte == b'-' || (index > 0 && byte.is_ascii_digit())
            })
        {
            return Err(BackendError::new(
                "invalid_graph",
                "知识节点 ID 必须是以小写字母开头的短标识",
            ));
        }
        if !ids.insert(node.id.as_str()) {
            return Err(BackendError::new("invalid_graph", "知识节点 ID 不能重复"));
        }
        if node.title.trim().is_empty()
            || node.title.len() > 200
            || node.summary.trim().is_empty()
            || node.summary.len() > 4000
            || !(1..=5).contains(&node.difficulty)
            || !(5..=600).contains(&node.estimated_minutes)
        {
            return Err(BackendError::new(
                "invalid_graph",
                "知识节点标题、摘要、难度或预计时长无效",
            ));
        }
        if node.citations.is_empty() {
            return Err(BackendError::new(
                "citation_missing",
                "每个知识节点至少需要一个原文引用",
            ));
        }
        for citation in &node.citations {
            let source = source_map.get(&citation.material_id).ok_or_else(|| {
                BackendError::new("citation_out_of_scope", "引用资料不在本方案的白名单中")
            })?;
            let length = citation.quote.chars().count();
            if !(2..=1000).contains(&length) || !source.text.contains(&citation.quote) {
                return Err(BackendError::new(
                    "citation_mismatch",
                    "引用必须是所选资料中连续存在的原文",
                ));
            }
        }
    }
    let mut edges = HashSet::new();
    let mut indegree = graph
        .nodes
        .iter()
        .map(|node| (node.id.as_str(), 0usize))
        .collect::<HashMap<_, _>>();
    let mut outgoing: HashMap<&str, Vec<&str>> = HashMap::new();
    for edge in &graph.edges {
        if !ids.contains(edge.from.as_str())
            || !ids.contains(edge.to.as_str())
            || edge.from == edge.to
        {
            return Err(BackendError::new(
                "invalid_dependency_graph",
                "依赖边引用了未知节点或形成自环",
            ));
        }
        if !edges.insert((edge.from.as_str(), edge.to.as_str())) {
            return Err(BackendError::new(
                "invalid_dependency_graph",
                "依赖边不能重复",
            ));
        }
        *indegree.get_mut(edge.to.as_str()).unwrap() += 1;
        outgoing
            .entry(edge.from.as_str())
            .or_default()
            .push(edge.to.as_str());
    }
    let mut pending = indegree
        .iter()
        .filter(|(_, count)| **count == 0)
        .map(|(id, _)| *id)
        .collect::<Vec<_>>();
    let mut visited = 0;
    while let Some(id) = pending.pop() {
        visited += 1;
        if let Some(next) = outgoing.get(id) {
            for target in next {
                let count = indegree.get_mut(target).unwrap();
                *count -= 1;
                if *count == 0 {
                    pending.push(target);
                }
            }
        }
    }
    if visited != graph.nodes.len() {
        return Err(BackendError::new("dependency_cycle", "知识依赖图必须无环"));
    }
    Ok(())
}

fn stage_errors(graph: &Graph, stages: &[Stage]) -> Vec<String> {
    let mut errors = Vec::new();
    let known = graph
        .nodes
        .iter()
        .map(|node| node.id.as_str())
        .collect::<HashSet<_>>();
    let required = graph
        .nodes
        .iter()
        .filter(|node| node.required)
        .map(|node| node.id.as_str())
        .collect::<HashSet<_>>();
    let mut stage_ids = HashSet::new();
    let mut locations = HashMap::new();
    if stages.is_empty() || stages.len() > 20 {
        errors.push("学习阶段数量必须位于 1 到 20".into());
    }
    for (index, stage) in stages.iter().enumerate() {
        if stage.id.is_empty()
            || stage.id.len() > 64
            || stage.title.trim().is_empty()
            || stage.title.len() > 200
        {
            errors.push(format!("阶段 {} 的标识或标题无效", index + 1));
        }
        if !stage_ids.insert(stage.id.as_str()) {
            errors.push(format!("阶段 {} 的 ID 重复", index + 1));
        }
        if !(1..=5).contains(&stage.difficulty) {
            errors.push(format!("阶段 {} 的难度不在 1 到 5 之间", index + 1));
        }
        if stage.node_ids.is_empty() || stage.node_ids.len() > 100 {
            errors.push(format!("阶段 {} 没有有效知识节点", index + 1));
        }
        for id in &stage.node_ids {
            if !known.contains(id.as_str()) {
                errors.push(format!("阶段 {} 引用了未知节点 {id}", index + 1));
            }
            if locations.insert(id.as_str(), index).is_some() {
                errors.push(format!("节点 {id} 被重复分配到多个阶段"));
            }
        }
    }
    for id in required {
        if !locations.contains_key(id) {
            errors.push(format!("必学节点 {id} 未被分配到阶段"));
        }
    }
    for edge in &graph.edges {
        if let (Some(from), Some(to)) = (
            locations.get(edge.from.as_str()),
            locations.get(edge.to.as_str()),
        ) {
            if from >= to {
                errors.push(format!(
                    "依赖 {} → {} 没有按阶段先后排列",
                    edge.from, edge.to
                ));
            }
        } else if locations.contains_key(edge.to.as_str()) {
            errors.push(format!(
                "节点 {} 的前置 {} 未纳入学习阶段",
                edge.to, edge.from
            ));
        }
    }
    errors
}

pub fn validate_stages(graph: &Graph, stages: &[Stage]) -> Result<()> {
    if let Some(error) = stage_errors(graph, stages).into_iter().next() {
        return Err(BackendError::new("invalid_stages", &error));
    }
    Ok(())
}

pub fn schedule(
    graph: &Graph,
    stages: &[Stage],
    daily_minutes: u32,
    start: Date,
    deadline: Date,
    milestone: Option<Date>,
) -> Result<(Schedule, Vec<ScheduledStage>)> {
    if !(10..=600).contains(&daily_minutes) {
        return Err(BackendError::invalid(
            "invalid_daily_minutes",
            "每日时长必须位于 10 到 600 分钟",
        ));
    }
    let nodes = graph
        .nodes
        .iter()
        .map(|node| (node.id.as_str(), node))
        .collect::<HashMap<_, _>>();
    let mut cursor = start;
    let mut remaining_today = daily_minutes;
    let mut total_minutes = 0u32;
    let mut entries: Vec<ScheduleEntry> = Vec::new();
    let mut scheduled = Vec::new();
    for stage in stages {
        let stage_start = cursor;
        let mut stage_end = cursor;
        let mut minutes = 0u32;
        for id in &stage.node_ids {
            let node = nodes
                .get(id.as_str())
                .ok_or_else(|| BackendError::new("invalid_stages", "排期引用了未知知识节点"))?;
            let mut remaining = node.estimated_minutes;
            while remaining > 0 {
                let allocation = remaining.min(remaining_today);
                if let Some(entry) = entries
                    .iter_mut()
                    .rev()
                    .find(|entry| entry.date == cursor.to_string() && entry.stage_id == stage.id)
                {
                    entry.minutes += allocation;
                    if !entry.node_ids.contains(id) {
                        entry.node_ids.push(id.clone());
                    }
                } else {
                    entries.push(ScheduleEntry {
                        date: cursor.to_string(),
                        stage_id: stage.id.clone(),
                        node_ids: vec![id.clone()],
                        minutes: allocation,
                    });
                }
                remaining -= allocation;
                remaining_today -= allocation;
                minutes += allocation;
                total_minutes += allocation;
                stage_end = cursor;
                if remaining_today == 0 {
                    cursor = cursor.next_day().ok_or_else(|| {
                        BackendError::new("schedule_too_long", "排期超出支持的日期范围")
                    })?;
                    remaining_today = daily_minutes;
                }
            }
        }
        scheduled.push(ScheduledStage {
            id: stage.id.clone(),
            title: stage.title.clone(),
            node_ids: stage.node_ids.clone(),
            difficulty: stage.difficulty,
            minutes,
            start_date: stage_start.to_string(),
            end_date: stage_end.to_string(),
        });
    }
    let end = scheduled
        .last()
        .map(|stage| stage.end_date.clone())
        .unwrap_or_else(|| start.to_string());
    let end_date = self::deadline(&end)?;
    let days = (end_date - start).whole_days().max(0) as u32 + 1;
    Ok((
        Schedule {
            start_date: start.to_string(),
            end_date: end.clone(),
            total_minutes,
            days,
            within_deadline: end_date <= deadline,
            milestone_date: milestone.map(|date| date.to_string()),
            within_milestone: milestone.map(|date| end_date <= date),
            entries,
        },
        scheduled,
    ))
}

fn check(status: &str, details: impl Into<String>) -> CheckResult {
    CheckResult {
        status: status.into(),
        details: details.into(),
    }
}

pub fn validate_model(model: &ModelValidation) -> Result<()> {
    for result in [
        &model.report.dependency_closure,
        &model.report.difficulty_step,
        &model.report.cognitive_load,
        &model.report.milestone_feasibility,
        &model.report.gap_analysis,
    ] {
        if !["pass", "warn", "fail"].contains(&result.status.as_str())
            || result.details.len() > 4000
        {
            return Err(BackendError::new(
                "invalid_model_output",
                "模型校验报告的状态或说明无效",
            ));
        }
    }
    if model.fixes.len() > 20
        || model
            .fixes
            .iter()
            .any(|fix| fix.trim().is_empty() || fix.len() > 500)
    {
        return Err(BackendError::new(
            "invalid_model_output",
            "模型修复建议为空、过长或超过 20 项",
        ));
    }
    Ok(())
}

pub fn unavailable_model() -> ModelValidation {
    ModelValidation {
        report: ModelReport {
            dependency_closure: check("warn", "未执行模型校验"),
            difficulty_step: check("warn", "未执行模型校验"),
            cognitive_load: check("warn", "未执行模型校验"),
            milestone_feasibility: check("warn", "模型未返回有效结果，里程碑判断未执行"),
            gap_analysis: check("warn", "模型未返回有效结果，缺口判断未执行"),
        },
        passed: false,
        fixes: vec!["模型校验不可用，请人工复核里程碑与知识缺口".into()],
    }
}

pub fn validation_report(
    graph: &Graph,
    stages: &[Stage],
    schedule: &Schedule,
    model: &ModelValidation,
) -> ValidationReport {
    let dependencies = stage_errors(graph, stages);
    let dependency = if dependencies.is_empty() {
        check("pass", "")
    } else {
        check("fail", dependencies.join("；"))
    };
    let difficulty_issues = stages
        .windows(2)
        .filter(|pair| {
            pair[1].difficulty < pair[0].difficulty
                || pair[1].difficulty.abs_diff(pair[0].difficulty) > 2
        })
        .map(|pair| {
            format!(
                "{} → {} 的难度 {} → {} 不渐进或跳级超过 2",
                pair[0].title, pair[1].title, pair[0].difficulty, pair[1].difficulty
            )
        })
        .collect::<Vec<_>>();
    let difficulty = if difficulty_issues.is_empty() {
        check("pass", "")
    } else {
        check("fail", difficulty_issues.join("；"))
    };
    let mut load_issues = stages
        .iter()
        .filter(|stage| !(3..=6).contains(&stage.node_ids.len()))
        .map(|stage| {
            format!(
                "{} 包含 {} 个节点，建议 3–6 个",
                stage.title,
                stage.node_ids.len()
            )
        })
        .collect::<Vec<_>>();
    if !(3..=6).contains(&stages.len()) {
        load_issues.push(format!("共有 {} 个阶段，建议 3–6 个", stages.len()));
    }
    let cognitive = if load_issues.is_empty() {
        check("pass", "")
    } else {
        check("fail", load_issues.join("；"))
    };
    let mut milestone = model.report.milestone_feasibility.clone();
    if !schedule.within_deadline {
        if milestone.status == "pass" {
            milestone.status = "warn".into();
        }
        if !milestone.details.is_empty() {
            milestone.details.push('；');
        }
        milestone.details.push_str(&format!(
            "按每日容量排期至 {}，晚于截止日",
            schedule.end_date
        ));
    }
    if schedule.within_milestone == Some(false) {
        milestone.status = "fail".into();
        if !milestone.details.is_empty() {
            milestone.details.push('；');
        }
        milestone.details.push_str(&format!(
            "最后阶段完成日 {} 晚于硬里程碑日期 {}",
            schedule.end_date,
            schedule.milestone_date.as_deref().unwrap_or("未设置")
        ));
    }
    let gap = model.report.gap_analysis.clone();
    let passed = [&dependency, &difficulty, &cognitive, &milestone, &gap]
        .iter()
        .all(|result| result.status == "pass");
    let mut fixes = model.fixes.clone();
    if dependency.status != "pass" {
        fixes.push("补齐必学节点，并把前置节点安排到更早阶段".into());
    }
    if difficulty.status != "pass" {
        fixes.push("调整阶段顺序或插入过渡阶段，避免难度跳级".into());
    }
    if cognitive.status != "pass" {
        fixes.push("将每阶段控制在 3–6 个知识节点，并保留 3–6 个渐进阶段".into());
    }
    if !schedule.within_deadline {
        fixes.push("增加每日时长、延后截止日或减少选学内容".into());
    }
    fixes.sort();
    fixes.dedup();
    fixes.truncate(20);
    ValidationReport {
        dependency_closure: dependency,
        difficulty_step: difficulty,
        cognitive_load: cognitive,
        milestone_feasibility: milestone,
        gap_analysis: gap,
        passed,
        fixes,
    }
}
