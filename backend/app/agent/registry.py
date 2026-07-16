"""工具注册表（plan/02 工具契约、plan/08 信封、plan/10 §十 统一接口）。

每个 Tool 声明：
- name / llm(是否调 LLM)
- LLM 工具：skill_name(作 system prompt)、output_model(data 的 pydantic 模型)、
  default_data(修复超限降级默认)、requires(字段覆盖所需的 upstream 字段，点号路径)
- Python 工具：func(async (inp, ctx) -> dict[data] | ToolOutput)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.agent.context import ExecContext
    from app.tools.base import ToolInput, ToolOutput

    # Python 工具函数签名：返回 data 字典或完整 ToolOutput
    ToolFunc = Callable[["ToolInput", "ExecContext"], Awaitable[dict[str, Any] | "ToolOutput"]]


@dataclass
class Tool:
    name: str
    llm: bool
    skill_name: str | None = None
    output_model: type[BaseModel] | None = None
    default_data: dict[str, Any] | None = None
    requires: list[str] = field(default_factory=list)  # 如 "understand_intent.topic"
    inline_prompt: str | None = None  # LLM 工具内联 prompt（step 5 Skill 未接入时的兜底）
    post_process: Callable[[dict[str, Any]], dict[str, Any]] | None = None  # LLM 输出校验后的清洗
    func: ToolFunc | None = None  # Python 工具

    def __post_init__(self) -> None:
        if self.llm and self.func is not None:
            raise ValueError(f"LLM 工具 {self.name} 不应同时提供 func")
        if not self.llm and self.func is None:
            raise ValueError(f"Python 工具 {self.name} 必须提供 func")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)
