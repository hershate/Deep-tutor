use super::super::error::BackendError;
use super::super::providers::openai::TokenUsage;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const STEP_NAMES: [&str; 8] = [
    "解析资料",
    "澄清意图",
    "构建知识图谱",
    "设计渐进阶段",
    "检索学习资源",
    "确定性排期",
    "五维校验",
    "方案落盘",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanInput {
    pub goal: String,
    pub level: String,
    pub daily_minutes: u32,
    pub deadline: String,
    pub material_ids: Vec<i64>,
    pub provider_id: Option<String>,
    pub milestone: Option<String>,
    pub milestone_date: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Source {
    pub id: i64,
    pub title: String,
    pub sha256: String,
    pub text: String,
    pub warning: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Citation {
    pub material_id: i64,
    pub quote: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct KnowledgeNode {
    pub id: String,
    pub title: String,
    pub summary: String,
    pub difficulty: u8,
    pub required: bool,
    pub estimated_minutes: u32,
    pub citations: Vec<Citation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct KnowledgeEdge {
    pub from: String,
    pub to: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Graph {
    pub nodes: Vec<KnowledgeNode>,
    pub edges: Vec<KnowledgeEdge>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Stage {
    pub id: String,
    pub title: String,
    pub node_ids: Vec<String>,
    pub difficulty: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StageDraft {
    pub stages: Vec<Stage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScheduleEntry {
    pub date: String,
    pub stage_id: String,
    pub node_ids: Vec<String>,
    pub minutes: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScheduledStage {
    pub id: String,
    pub title: String,
    pub node_ids: Vec<String>,
    pub difficulty: u8,
    pub minutes: u32,
    pub start_date: String,
    pub end_date: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Schedule {
    pub start_date: String,
    pub end_date: String,
    pub total_minutes: u32,
    pub days: u32,
    pub within_deadline: bool,
    pub milestone_date: Option<String>,
    pub within_milestone: Option<bool>,
    pub entries: Vec<ScheduleEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CheckResult {
    pub status: String,
    pub details: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ValidationReport {
    pub dependency_closure: CheckResult,
    pub difficulty_step: CheckResult,
    pub cognitive_load: CheckResult,
    pub milestone_feasibility: CheckResult,
    pub gap_analysis: CheckResult,
    pub passed: bool,
    pub fixes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub struct ModelReport {
    pub dependency_closure: CheckResult,
    pub difficulty_step: CheckResult,
    pub cognitive_load: CheckResult,
    pub milestone_feasibility: CheckResult,
    pub gap_analysis: CheckResult,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ModelValidation {
    pub report: ModelReport,
    pub passed: bool,
    pub fixes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanResult {
    pub goal: String,
    pub level: String,
    pub deadline: String,
    pub milestone: Option<String>,
    pub milestone_date: Option<String>,
    pub graph: Graph,
    pub stages: Vec<ScheduledStage>,
    pub schedule: Schedule,
    pub resources: Value,
    pub validation: ValidationReport,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanStep {
    pub index: u8,
    pub name: String,
    pub status: String,
    pub attempt: u32,
    pub request_id: Option<String>,
    pub output: Option<Value>,
    pub error: Option<BackendError>,
    pub usage: Option<TokenUsage>,
    pub upstream_attempts: Option<u8>,
    pub latency_ms: Option<i64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Plan {
    pub id: String,
    pub status: String,
    pub provider_id: String,
    pub goal: String,
    pub level: String,
    pub daily_minutes: u32,
    pub deadline: String,
    pub material_ids: Vec<i64>,
    pub current_step: u8,
    pub progress: u8,
    pub version: u32,
    pub adopted_project_id: Option<i64>,
    pub steps: Vec<PlanStep>,
    pub artifacts: Value,
    pub result: Option<PlanResult>,
    pub error: Option<BackendError>,
    pub created_at: String,
    pub updated_at: String,
}

pub enum Artifact {
    None,
    Sources(Vec<Source>),
    Graph(Graph),
    Stages(Vec<Stage>),
    Resources(Value),
    Schedule(Schedule, Vec<ScheduledStage>),
    Validation(ValidationReport),
}
