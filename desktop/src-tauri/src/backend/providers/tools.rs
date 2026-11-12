use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallFunction {
    pub name: String,
    pub arguments: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallRequest {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub function: ToolCallFunction,
}

// 142#2：本结构是仓内 15 个结构体入参中唯一无 rename_all="camelCase" 的
// 例外——它同时是 OpenAI 线缆格式（tools 规范字段本就是 snake_case 家族
// +单词键），加 rename_all 会破坏线缆兼容；约束：新增字段必须保持单词
// （snake==camel），否则前端按惯例传 camelCase 键会被 serde 拒收
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    #[serde(rename = "type")]
    pub kind: String,
    pub function: ToolDefinitionFunction,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinitionFunction {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}
