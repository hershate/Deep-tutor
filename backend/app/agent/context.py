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
    # iter 356：当前用户的模型计费表（模型名 -> 4 单价字段，¥/1M tokens；设置页维护）。
    # 由 plans._run_orchestrator 按 get_active_model_pricing(user_id) 注入，驱动生成期
    # 实时累计成本（resolve_pricing：精确模型名 -> 族默认 -> None 记 0）。空 -> 纯族默认。
    model_pricing: dict = field(default_factory=dict)
    # iter 355：该次生成的 LLM 输出 token 上限（None -> _chat_with_retry 回退 settings.llm_max_tokens）。
    # 由各 ctx 构造点按用户偏好 get_active_max_tokens 注入（用户可在设置页调节，防 quiz 截断）。
    llm_max_tokens: int | None = None
    # iter 382：孤儿计量寄存器——LLM 调用被取消（CancelledError 传播，不吞）时，已累积
    # 的部分 usage 随协程局部变量丢弃 = 不可观测成本黑洞（日预算只信 api_usage 求和）。
    # loop._run_llm_tool 在取消传播前把 build_metadata(total,...) 寄存于此；由任务收口方
    # （plans._finalize / 材料流 finally）统一落账后清空。材料流 worker 的「run_tool 已完成
    # 但主循环未消费」窗口亦复用此寄存（正常消费路径移除条目防双落）。
    orphan_billed: list = field(default_factory=list)
