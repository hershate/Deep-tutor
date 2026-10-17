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

import asyncio
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
        self.signature_parts: list[str] = []  # iter 367：thinking 签名（回传历史必需）
        # tool_use 增量按 id 聚合（input_json_delta 分片到达）
        self._tools: list[dict] = []  # [{id, name, json}]
        self.usage: LLMUsage | None = None
        self.stop_reason: str | None = None

    def feed(self, chunk) -> None:  # chunk: AgenticStreamChunk
        if chunk.text:
            self.text_parts.append(chunk.text)
        if chunk.thinking:
            self.thinking_parts.append(chunk.thinking)
        if getattr(chunk, "signature", ""):
            self.signature_parts.append(chunk.signature)
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
            block = {"type": "thinking", "thinking": "".join(self.thinking_parts)}
            # iter 367：带签名的 thinking 块才可回传（无签名回传即 400）
            if self.signature_parts:
                block["signature"] = "".join(self.signature_parts)
            blocks.append(block)
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


def _persist_message_sync(db, thread_id: int, role, content, run_id: int | None, usage: LLMUsage | None = None) -> None:
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


async def _persist_message(db, thread_id: int, role, content, run_id: int | None, usage: LLMUsage | None = None) -> None:
    # iter 366：DB 写经 to_thread 移出事件循环——SQLite 写串行下高并发提交不再阻塞 loop
    # （心跳/其他请求保持响应）。Session 顺序访问（此处 await 挂起，无并发触同一 Session），安全。
    await asyncio.to_thread(_persist_message_sync, db, thread_id, role, content, run_id, usage)


def _persist_run_step_sync(
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
    model_pricing: dict | None = None,
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
            # iter 369：按用户计费表估算（与 api_usage/用量页同口径；族默认对自配
            # 非 Anthropic 模型恒 0，显示与实记不一致）
            cost=usage_cost(usage, model_pricing) if usage else 0.0,
            model=usage.model if usage else "",
            duration_ms=duration_ms,
        )
    )
    db.commit()


async def _persist_run_step(
    db,
    run_id: int,
    step_index: int,
    type_: str,
    name: str,
    inp: dict,
    out: dict,
    **kw,
) -> None:
    await asyncio.to_thread(_persist_run_step_sync, db, run_id, step_index, type_, name, inp, out, **kw)


def _bump_step_count_sync(db, run_id: int, n: int) -> None:
    from app.db.models import Run

    db.query(Run).filter_by(id=run_id).update({"step_count": n})
    db.commit()


async def _bump_step_count(db, run_id: int, n: int) -> None:
    await asyncio.to_thread(_bump_step_count_sync, db, run_id, n)


def load_thread_messages(db, thread_id: int, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """加载最近 limit 条消息（chronological），修剪开头悬空 tool_result。

    悬空 tool_result（缺前导 assistant tool_use）会让 Anthropic API 报错——历史窗口
    恰好切在 tool_use/tool_result 之间时丢弃开头的 tool_result 轮。

    iter 367 两个自愈（仅修内存副本，不写库、幂等）：
    - 尾部悬空 tool_use：进程在工具执行期间崩溃（assistant 已落库、tool_result 未及写）
      后，该 thread 每次后续 run 的首轮 LLM 调用都 400（tool_use ids without
      tool_result）。为未应答的 tool_use 补占位 tool_result（is_error）并并入其后的
      user 消息（无则追加一条）。
    - 无 signature 的 thinking 块剔除：Anthropic 要求回传的 thinking 块带有效签名，
      旧数据/中断轮的裸 thinking 块直接丢弃（历史 thinking 允许省略）。
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

    # thinking 块清洗（无签名剔除；清空的消息补占位防空 content 被 API 拒）
    for m in msgs:
        if isinstance(m.get("content"), list):
            m["content"] = [
                b for b in m["content"]
                if not (isinstance(b, dict) and b.get("type") == "thinking" and not b.get("signature"))
            ]
            if m["content"] == []:
                m["content"] = [{"type": "text", "text": "…"}]

    # 悬空 tool_use 自愈（尾部及中途）：进程在工具执行期间崩溃（assistant 已落库、
    # tool_result 未及写）后，该 thread 每次后续 run 的 LLM 调用都 400（tool_use ids
    # without tool_result）。逆序遍历每条含 tool_use 的 assistant，为未应答 id 补占位
    # tool_result（is_error）并入其后 user 消息（无/非 user 则插入一条）；逆序保证
    # 插入不影响未处理下标。仅修内存副本，不写库、幂等（再次加载重复修复无副作用）。
    for i in range(len(msgs) - 1, -1, -1):
        c = msgs[i].get("content")
        if msgs[i]["role"] != "assistant" or not isinstance(c, list):
            continue
        tu_ids = [
            b.get("id")
            for b in c
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
        ]
        if not tu_ids:
            continue
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        answered: set = set()
        if nxt is not None and nxt["role"] == "user" and isinstance(nxt.get("content"), list):
            answered = {
                b.get("tool_use_id")
                for b in nxt["content"]
                if isinstance(b, dict) and b.get("type") == "tool_result"
            }
        unanswered = [tid for tid in tu_ids if tid not in answered]
        if not unanswered:
            continue
        placeholder = [
            {
                "type": "tool_result",
                "tool_use_id": tid,
                "content": "（服务在工具执行期间中断，该调用未完成）",
                "is_error": True,
            }
            for tid in unanswered
        ]
        if nxt is not None and nxt["role"] == "user":
            cur = nxt["content"]
            if isinstance(cur, list):
                nxt["content"] = placeholder + cur
            else:
                nxt["content"] = placeholder + [{"type": "text", "text": str(cur)}]
        else:
            msgs.insert(i + 1, {"role": "user", "content": placeholder})
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
    await _persist_message(db, thread_id, _role("user"), [{"type": "text", "text": user_message}], run_id)
    if emit:
        await emit("message_start", {"run_id": run_id, "role": "assistant"})

    for step in range(max_steps):
        if ctx.cancel_event.is_set():
            return AgentRunResult(status="interrupted", final_text=last_text, steps=step, usage=total_usage)
        # iter 363：promote_artifact 置 awaiting_approval 后退出循环（run 待用户 approve，
        # _finalize_chat 保持该终态；不退出则 run 继续跑与 approve 冲突）
        from app.db.models import Run as _Run, RunStatus as _RunStatus
        _cur_run = db.get(_Run, run_id)
        if _cur_run is not None and _cur_run.status == _RunStatus.awaiting_approval:
            return AgentRunResult(status="done", final_text=last_text, steps=step, usage=total_usage)

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
        except asyncio.CancelledError:
            # iter 369：硬取消（/runs/{id}/cancel 的 task.cancel / 关停取消）打断流读取时，
            # 本轮 message_start 已到的 input/cache tokens 已向供应商计费——补记后原样重抛
            # （取消语义不变；shield 防补记 await 被紧随的二次取消打断）
            if acc.usage is not None and (acc.usage.input_tokens or acc.usage.output_tokens):
                accumulate_usage(total_usage, acc.usage)
                meta = build_metadata(acc.usage, acc.usage.model, ctx.model_pricing)
                try:
                    await asyncio.shield(asyncio.to_thread(record_metadata_usage, db, meta, ctx.user_id))
                except Exception:  # noqa: BLE001  补记失败不阻断取消传播
                    log.warning("agent_loop 取消路径补记 usage 失败", exc_info=True)
            raise
        except Exception as exc:  # noqa: BLE001  provider 异常隔离——终止保已计量
            log.warning("agent_loop provider error at step %d: %s", step, exc)
            # iter 365 B1：异常前 provider 已 yield 的部分 usage（message_start 的 input_tokens）
            # 须入账——否则本轮 LLM 费用已发生却写不进 api_usage/预算（成本黑洞，iter 243 同型）
            if acc.usage is not None:
                accumulate_usage(total_usage, acc.usage)
                meta = build_metadata(acc.usage, acc.usage.model, ctx.model_pricing)
                await asyncio.to_thread(record_metadata_usage, db, meta, ctx.user_id)
                if ctx.budget is not None:
                    ctx.budget.add(meta.cost)
            return AgentRunResult(status="error", final_text=last_text, steps=step, error=str(exc), usage=total_usage)

        # 4) 每轮计量 + 预算守卫（iter 243 教训：即时计量防黑洞）
        turn_usage = acc.usage or LLMUsage()
        if turn_usage.input_tokens or turn_usage.output_tokens:
            accumulate_usage(total_usage, turn_usage)
            meta = build_metadata(turn_usage, turn_usage.model, ctx.model_pricing)
            await asyncio.to_thread(record_metadata_usage, db, meta, user_id)
            if ctx.budget is not None:
                ctx.budget.add(meta.cost)
                if ctx.budget.exceeded():
                    await _persist_run_step(
                        db, run_id, step, "message", "assistant", {}, {"reason": "budget_exceeded"},
                        usage=turn_usage, duration_ms=int((time.monotonic() - t0) * 1000),
                        model_pricing=ctx.model_pricing,
                    )
                    return AgentRunResult(status="interrupted", final_text=last_text, steps=step + 1, usage=total_usage, error="budget_exceeded")

        # 5) 持久化 assistant 消息 + run_step
        blocks = acc.to_content_blocks()
        last_text = acc.text
        if blocks:
            await _persist_message(db, thread_id, _role("assistant"), blocks, run_id, turn_usage)
        await _persist_run_step(
            db, run_id, step, "message", "assistant", {}, {"text": last_text[:500]},
            usage=turn_usage, stop_reason=acc.stop_reason, duration_ms=int((time.monotonic() - t0) * 1000),
            model_pricing=ctx.model_pricing,
        )
        if emit:
            await emit("message_end", {"step": step, "stop_reason": acc.stop_reason})
        await _bump_step_count(db, run_id, step + 1)

        # 6) 终止判定
        if acc.stop_reason != "tool_use" or not acc.tool_use_blocks():
            return AgentRunResult(status="done", final_text=last_text, steps=step + 1, usage=total_usage)

        # 7) 执行工具 + 持久化 tool_result（异常隔离：单工具失败记 error 不杀循环）
        results_msg: list[dict] = []
        hard_exc: BaseException | None = None
        try:
            for tu in acc.tool_use_blocks():
                if ctx.cancel_event.is_set():
                    break  # 软取消：退出循环，下方补占位（for step 下次迭代 return interrupted）
                tt0 = time.monotonic()
                try:
                    res = await tool_executor(tu, ctx)
                    await _persist_run_step(
                        db, run_id, step, "tool_call", tu.name, tu.input,
                        {"content": (res.content or "")[:1000], **res.meta},
                        duration_ms=int((time.monotonic() - tt0) * 1000), is_error=res.is_error,
                    )
                except Exception as exc:  # noqa: BLE001
                    res = AgentToolResult(content=f"工具执行异常: {exc}", is_error=True)
                    try:
                        await _persist_run_step(
                            db, run_id, step, "tool_call", tu.name, tu.input, {"error": str(exc)},
                            duration_ms=int((time.monotonic() - tt0) * 1000), is_error=True,
                        )
                    except Exception:  # noqa: BLE001  iter 365 B2：_persist_run_step 自身失败不穿透
                        db.rollback()
                if emit:
                    await emit("tool_result", {"id": tu.id, "name": tu.name, "is_error": res.is_error, "step": step})
                results_msg.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": res.content, "is_error": res.is_error}
                )
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001  iter 365 B2：硬取消/DB 抖动穿透
            hard_exc = exc  # 下方补占位 + persist 后 raise（防 thread 末尾孤儿 tool_use 卡死会话）
        # 占位补全：未执行的 tool_use 补 tool_result（软取消 break / 硬异常 都补）——
        # 否则 assistant tool_use 与 user tool_result 数不匹配，下次 run 加载历史被 Anthropic 拒（卡死）。
        done_ids = {r["tool_use_id"] for r in results_msg}
        for tu in acc.tool_use_blocks():
            if tu.id not in done_ids:
                results_msg.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": "已取消", "is_error": True}
                )
        if results_msg:
            try:
                await _persist_message(db, thread_id, _role("user"), results_msg, run_id)
            except Exception:  # noqa: BLE001  iter 365 B2：persist 失败回滚不抛（崩溃由 _finalize_chat 兜）
                db.rollback()
        if hard_exc is not None:
            raise hard_exc  # 传播硬取消（run 终止）/ 异常（_run_chat 记 error）

    return AgentRunResult(status="max_steps", final_text=last_text, steps=max_steps, usage=total_usage)


def _role(name: str):
    """延迟取 MessageRole 枚举（避 agent_loop 顶层 import models 的潜在循环）。"""
    from app.db.models import MessageRole

    return MessageRole(name)
