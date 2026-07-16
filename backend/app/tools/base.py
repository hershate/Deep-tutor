"""统一工具 I/O 信封（plan/08 §三 Envelope）。

所有 AI 工作流工具的输入/输出统一包络；Agent 循环与前端只处理信封，
工具内部按 args/data 契约。pydantic v2 校验，不符走工具调用修复（Pillar 2）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SchemaVersion = Literal["1.0"]
ToolStatus = Literal["pending", "running", "ok", "error", "repaired"]
Stability = Literal["stable", "candidate", "experimental"]


class ToolError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolMetadata(BaseModel):
    """成本与审计元数据，写入 tool_calls + api_usage。"""

    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    duration_ms: int = 0
    model: str = ""


class ToolInput(BaseModel):
    schema_version: SchemaVersion = "1.0"
    tool: str
    session_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    upstream: dict[str, Any] = Field(default_factory=dict)  # {tool_name: {data: ...}}


class ToolOutput(BaseModel):
    schema_version: SchemaVersion = "1.0"
    tool: str
    status: ToolStatus = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[ToolError] = Field(default_factory=list)
    metadata: ToolMetadata = Field(default_factory=ToolMetadata)
    stability: Stability = "stable"
