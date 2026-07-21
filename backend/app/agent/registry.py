"""工具注册表（plan/02 工具契约、plan/08 信封、plan/10 §十 统一接口）。

每个 Tool 声明：
- name / llm(是否调 LLM)
- LLM 工具：skill_name(作 system prompt)、output_model(data 的 pydantic 模型)、
  default_data(修复超限降级默认)、requires(字段覆盖所需的 upstream 字段，点号路径)
- Python 工具：func(async (inp, ctx) -> dict[data] | ToolOutput)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

log = logging.getLogger("deep_tutor.registry")

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
    # 工具实际读取的上游工具名集合（声明式，err.md B13/iter 235 W4b）。
    # 仅作「并行组同组工具互不依赖」静态断言的事实源，不参与 check_field_coverage、
    # 不触发 missing_upstream_field——故填充它是**零运行时行为变更**。缺省空集
    # （不读上游）。应由工具作者据 func/Skill 的 inp.upstream 实际读取面如实填写。
    upstream_reads: frozenset[str] = field(default_factory=frozenset)
    inline_prompt: str | None = None  # LLM 工具内联 prompt（step 5 Skill 未接入时的兜底）
    post_process: Callable[[dict[str, Any], "ToolInput"], dict[str, Any]] | None = None  # 校验后清洗(data, inp)
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
        # err.md B20：重名注册此前静默覆盖（手滑注册两个同名工具时后者偷换前者）
        if tool.name in self._tools:
            log.warning("工具 %s 重复注册，将覆盖已有实现", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)
