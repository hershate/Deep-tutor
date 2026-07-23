"""Agent 对话循环（iter 345 记忆系统）—— 原生 tool_use 迭代循环。

与确定性 ``Orchestrator``（plan-gen 固定 PIPELINE）**并存**：
- plan_generation run -> Orchestrator；
- chat/agent run -> 本模块 ``run_agent_loop``。

循环（仿 Anthropic 简单 agent 循环）：
1. 持久化用户消息（append-only 会话历史，崩溃留连贯半转录）；
2. 每步：加载历史（含本 run 已产出的 assistant/tool_result 轮）→ 修剪悬空 tool_result →
   前缀缓存标记 → ``provider.stream_agentic`` 流式（text/thinking/tool_use 增量）；
3. 每轮**即时**持久化 assistant 消息 + run_step，并经 ``record_metadata_usage`` 记
   ``api_usage``（吸收 iter 243 教训：勿延到结尾，cancel/timeout/崩溃会造预算黑洞）；
4. ``stop_reason == "tool_use"`` → 执行工具（注入的 ``tool_executor``），回灌 tool_result，
   持久化后续循环；否则结束（done）。

守卫：``MAX_AGENT_STEPS``（默认 25）+ ``BudgetTracker``（每轮 add/exceeded）+ ``cancel_event``
（每步与每工具前查）+ 工具异常隔离（单工具失败不杀循环，记 error tool_result）。

工具执行与工具集**注入**（解耦）：``tools``（广告给模型的 ToolDefinition 列表）+
``tool_executor``（执行 ToolUseBlock -> AgentToolResult）。实际工具注册/分派（核心工具走
``run_tool`` + 工作区/联网/记忆工具）由 API 层（phase G）装配——本模块只跑循环逻辑，
便于用 fake provider + fake executor 单测。

实时可见性：可选 ``emit``（async 事件回调）转发 text_delta/thinking_delta/tool_call/
tool_progress/tool_result/message_start/message_end，供 SSE（DeepSeek 式 UI）消费。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agent.context import ExecContext
from app.agent.cost import accumulate_usage, build_metadata, record_metadata_usage, usage_cost
from app.llm.base import LLMUsage, ToolDefinition, ToolUseBlock

log = logging.getLogger("deep_tutor.agent.loop")

MAX_AGENT_STEPS = 25
DEFAULT_HISTORY_LIMIT = 50  # 加载最近 N 条消息（按需加载历史——决策②）

# 工具执行器：ToolUseBlock + ctx -> AgentToolResult（由 API 层注入；核心工具走 run_tool）
ToolExecutor = Callable[[ToolUseBlock, ExecContext], "Awaitable[AgentToolResult]"]
# 事件发射器：async (event, data) -> None（供 SSE 转发；None 表示无 SSE）
EventEmitter = Callable[[str, dict], "Awaitable[None]"]


@dataclass
class AgentToolResult:
    """工具执行结果（回灌为 Anthropic tool_result.content）。"""

    content: str
    is_error: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class AgentRunResult:
    """循环结束状态。status: done | interrupted | error | max_steps。"""

    status: str
    final_text: str = ""
    steps: int = 0
    error: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)


# ---------------------------------------------------------------------------
# 流式累积器：把 AgenticStreamChunk 序列重组为 content 块 + usage + tool_uses
# ---------------------------------------------------------------------------


class _StreamAccumulator:
    """累积流式分片。块顺序重建为 [thinking?, text?, tool_use...]（Anthropic 容忍）。"""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        # tool_use 增量按 id 聚合（input_json_delta 分片到达）
        self._tools: list[dict] = []  # [{id, name, json}]
        self.usage: LLMUsage | None = None
        self.stop_reason: str | None = None

    def feed(self, chunk) -> None:  # chunk: AgenticStreamChunk
        if chunk.text:
            self.text_parts.append(chunk.text)
        if chunk.thinking:
            self.thinking_parts.append(chunk.thinking)
        if chunk.tool_use_start:
            tid, name = chunk.tool_use_start
            self._tools.append({"id": tid, "name": name, "json": ""})
        if chunk.tool_use_delta:
            tid, partial = chunk.tool_use_delta
            for tu in self._tools:
                if tu["id"] == tid:
                    tu["json"] += partial
                    break
        if chunk.usage:
            self.usage = chunk.usage
        if chunk.stop_reason:
            self.stop_reason = chunk.stop_reason

    @staticmethod
    def _parse_input(raw: str) -> dict:
        if not raw:
            return {}
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"value": v}
        except json.JSONDecodeError:
            return {"_raw": raw}  # 模型产出非法 JSON 时保留原串供排查，不崩

    def to_content_blocks(self) -> list[dict]:
        blocks: list[dict] = []
        if self.thinking_parts:
            blocks.append({"type": "thinking", "thinking": "".join(self.thinking_parts)})
        if self.text_parts:
            blocks.append({"type": "text", "text": "".join(self.text_parts)})
        for tu in self._tools:
            blocks.append(
                {"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": self._parse_input(tu["json"])}
            )
        return blocks

    def tool_use_blocks(self) -> list[ToolUseBlock]:
        return [ToolUseBlock(id=tu["id"], name=tu["name"], input=self._parse_input(tu["json"])) for tu in self._tools]

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


# ---------------------------------------------------------------------------
# 持久化辅助（每条 message / 每个 run_step 即时落库——崩溃安全）
# ---------------------------------------------------------------------------


def _persist_message(db, thread_id: int, role, content, run_id: int | None, usage: LLMUsage | None = None) -> None:
    from app.db.models import Message

    db.add(
        Message(
            thread_id=thread_id,
            role=role,
            content=content,
            run_id=run_id,
            tokens_in=usage.input_tokens if usage else 0,
            tokens_out=usage.output_tokens if usage else 0,
            model=usage.model if usage else "",
        )
    )
    db.commit()


def _persist_run_step(
    db,
    run_id: int,
    step_index: int,
    type_: str,
    name: str,
    inp: dict,
    out: dict,
    *,
    usage: LLMUsage | None = None,
    stop_reason: str | None = None,
    duration_ms: int = 0,
    is_error: bool = False,
) -> None:
    from app.db.models import RunStep

    db.add(
        RunStep(
            run_id=run_id,
            step_index=step_index,
            type=type_,
            name=name or "",
            input=inp or {},
            output=out or {},
            status="error" if is_error else "ok",
            stop_reason=stop_reason,
            tokens_in=usage.input_tokens if usage else 0,
            tokens_out=usage.output_tokens if usage else 0,
            cost=usage_cost(usage) if usage else 0.0,
            model=usage.model if usage else "",
            duration_ms=duration_ms,
        )
    )
    db.commit()


def _bump_step_count(db, run_id: int, n: int) -> None:
    from app.db.models import Run

    db.query(Run).filter_by(id=run_id).update({"step_count": n})
    db.commit()


def load_thread_messages(db, thread_id: int, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """加载最近 limit 条消息（chronological），修剪开头悬空 tool_result。

    悬空 tool_result（缺前导 assistant tool_use）会让 Anthropic API 报错——历史窗口
    恰好切在 tool_use/tool_result 之间时丢弃开头的 tool_result 轮。
    """
    from app.db.models import Message

    rows = (
        db.query(Message)
        .filter_by(thread_id=thread_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    msgs = [{"role": r.role.value, "content": r.content} for r in reversed(rows)]
    while msgs and msgs[0]["role"] == "user" and isinstance(msgs[0]["content"], list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in msgs[0]["content"]
    ):
        msgs.pop(0)
    return msgs


def _apply_prefix_cache(messages: list[dict]) -> None:
    """在「最新 user 轮之前」的末块打 cache_control（1 breakpoint，缓存增长前缀）。

    Anthropic 无状态——每轮重发全历史，不缓存则 O(n²) 输入成本。前缀缓存让历史部分
    命中 cache（hash 键自动前移）。content 块从 DB JSON 读出为全新对象，标记不污染 DB。
    """
    if len(messages) < 2:
        return
    content = messages[-2].get("content")
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


async def run_agent_loop(
    ctx: ExecContext,
    *,
    thread_id: int,
    run_id: int,
    user_message: str,
    provider,
    tools: list[ToolDefinition],
    tool_executor: ToolExecutor,
    system: str = "",
    max_steps: int = MAX_AGENT_STEPS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    emit: EventEmitter | None = None,
    thinking: bool = False,
) -> AgentRunResult:
    """迭代原生 tool_use Agent 循环。

    provider 须支持 stream_agentic（AnthropicProvider）；PluginProvider 不支持 ->
    调用方应先检测并拒绝（phase 1）。返回 AgentRunResult；SSE/Run 终态收口由调用方
    （API 层 done-callback）据 status 写 runs.status。
    """
    db = ctx.db
    user_id = ctx.user_id
    total_usage = LLMUsage()
    last_text = ""

    # 1) 持久化用户消息（会话历史 append-only）
    _persist_message(db, thread_id, _role("user"), [{"type": "text", "text": user_message}], run_id)
    if emit:
        await emit("message_start", {"run_id": run_id, "role": "assistant"})

    for step in range(max_steps):
        if ctx.cancel_event.is_set():
            return AgentRunResult(status="interrupted", final_text=last_text, steps=step, usage=total_usage)

        # 2) 加载历史 + 前缀缓存
        messages = load_thread_messages(db, thread_id, history_limit)
        _apply_prefix_cache(messages)

        # 3) 流式调用
        acc = _StreamAccumulator()
        t0 = time.monotonic()
        try:
            async for chunk in provider.stream_agentic(
                messages, system=system, tools=tools or None, thinking=thinking
            ):
                acc.feed(chunk)
                if emit:
                    if chunk.text:
                        await emit("text_delta", {"text": chunk.text})
                    if chunk.thinking:
                        await emit("thinking_delta", {"text": chunk.thinking})
                    if chunk.tool_use_start:
                        await emit("tool_call", {"id": chunk.tool_use_start[0], "name": chunk.tool_use_start[1], "step": step})
                    if chunk.tool_use_delta:
                        await emit("tool_progress", {"id": chunk.tool_use_delta[0], "step": step})
        except Exception as exc:  # noqa: BLE001  provider 异常隔离——终止保已计量
            log.warning("agent_loop provider error at step %d: %s", step, exc)
            return AgentRunResult(status="error", final_text=last_text, steps=step, error=str(exc), usage=total_usage)

        # 4) 每轮计量 + 预算守卫（iter 243 教训：即时计量防黑洞）
        turn_usage = acc.usage or LLMUsage()
        if turn_usage.input_tokens or turn_usage.output_tokens:
            accumulate_usage(total_usage, turn_usage)
            meta = build_metadata(turn_usage, turn_usage.model)
            record_metadata_usage(db, meta, user_id)
            if ctx.budget is not None:
                ctx.budget.add(meta.cost)
                if ctx.budget.exceeded():
                    _persist_run_step(
                        db, run_id, step, "message", "assistant", {}, {"reason": "budget_exceeded"},
                        usage=turn_usage, duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return AgentRunResult(status="interrupted", final_text=last_text, steps=step + 1, usage=total_usage, error="budget_exceeded")

        # 5) 持久化 assistant 消息 + run_step
        blocks = acc.to_content_blocks()
        last_text = acc.text
        if blocks:
            _persist_message(db, thread_id, _role("assistant"), blocks, run_id, turn_usage)
        _persist_run_step(
            db, run_id, step, "message", "assistant", {}, {"text": last_text[:500]},
            usage=turn_usage, stop_reason=acc.stop_reason, duration_ms=int((time.monotonic() - t0) * 1000),
        )
        if emit:
            await emit("message_end", {"step": step, "stop_reason": acc.stop_reason})
        _bump_step_count(db, run_id, step + 1)

        # 6) 终止判定
        if acc.stop_reason != "tool_use" or not acc.tool_use_blocks():
            return AgentRunResult(status="done", final_text=last_text, steps=step + 1, usage=total_usage)

        # 7) 执行工具 + 持久化 tool_result（异常隔离：单工具失败记 error 不杀循环）
        results_msg: list[dict] = []
        for tu in acc.tool_use_blocks():
            if ctx.cancel_event.is_set():
                break
            tt0 = time.monotonic()
            try:
                res = await tool_executor(tu, ctx)
                _persist_run_step(
                    db, run_id, step, "tool_call", tu.name, tu.input,
                    {"content": (res.content or "")[:1000], **res.meta},
                    duration_ms=int((time.monotonic() - tt0) * 1000), is_error=res.is_error,
                )
            except Exception as exc:  # noqa: BLE001
                res = AgentToolResult(content=f"工具执行异常: {exc}", is_error=True)
                _persist_run_step(
                    db, run_id, step, "tool_call", tu.name, tu.input, {"error": str(exc)},
                    duration_ms=int((time.monotonic() - tt0) * 1000), is_error=True,
                )
            if emit:
                await emit("tool_result", {"id": tu.id, "name": tu.name, "is_error": res.is_error, "step": step})
            results_msg.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": res.content, "is_error": res.is_error}
            )
        if results_msg:
            _persist_message(db, thread_id, _role("user"), results_msg, run_id)

    return AgentRunResult(status="max_steps", final_text=last_text, steps=max_steps, usage=total_usage)


def _role(name: str):
    """延迟取 MessageRole 枚举（避 agent_loop 顶层 import models 的潜在循环）。"""
    from app.db.models import MessageRole

    return MessageRole(name)
