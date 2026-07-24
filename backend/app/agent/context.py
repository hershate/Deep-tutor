"""执行上下文 ExecContext（plan/10 §十 工具包装器统一接口）。

贯穿一次会话生成：会话/trace 标识、工具注册表、LLM Provider、Skill 注册表、
数据目录、超时、取消信号、DB 会话、预算跟踪器。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent.budget import BudgetTracker
from app.llm.base import LLMProvider
from app.llm.pricing import Pricing

if TYPE_CHECKING:
    from app.agent.registry import ToolRegistry


@dataclass
class Timeouts:
    llm: float = 120.0
    # Python 工具外层兜底超时（err.md B6）：须高于工具内部最长超时
    # （parse_material 的 marker=600s），仅兜无内部守卫的挂起（如 whisper ffprobe 缺失）
    parse: float = 900.0
    url: float = 30.0


@dataclass
class ExecContext:
    session_id: str
    trace_id: str
    registry: ToolRegistry | None = None
    llm_provider: LLMProvider | None = None
    skill_registry: dict[str, str] | None = None  # skill_name -> system prompt body
    data_dir: str = ""
    timeouts: Timeouts = field(default_factory=Timeouts)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    db: object | None = None  # SQLAlchemy Session（step 6 接入；step 3 测试可不传）
    budget: BudgetTracker | None = None
    # iter 223：凭证档案解析后的有效 Bing Key（空 -> 工具内回退 settings.bing_api_key）；
    # 由 API 层按发起用户解析注入（app.core.credentials.resolve_bing_api_key）
    bing_api_key: str = ""
    # iter 345 记忆系统：Agent 对话循环专用（orchestrator 路径不填，保持默认零影响）
    thread_id: int | None = None
    run_id: int | None = None
    user_id: int | None = None  # 归属用户（record_metadata_usage 计量 + 配额）
    workspace_dir: str = ""  # 本 run 工作区沙箱根（agent/workspace.py）
    # iter 354 BUG2：启用供应商档案合成的每卡定价（None -> build_metadata 回退族默认）。
    # 由 plans._run_orchestrator 按 get_active_profile 价目注入，驱动生成期实时累计成本。
    pricing: Pricing | None = None
