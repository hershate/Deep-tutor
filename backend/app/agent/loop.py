"""内层工具调用循环（plan/01 §三 两层执行模型 / plan/10 §一/§十）。

run_tool(name, inp, ctx) -> ToolOutput：
- LLM 工具：system=Skill SKILL.md（稳定前缀 + cache_control）+ user=args/upstream
  -> Claude -> 解析 JSON -> pydantic 校验 -> 不符走工具调用修复（反馈重试≤3 -> 降级默认 repaired）
- Python 工具：确定性 func，无 LLM
- 超时 -> error(upstream_timeout)；取消 -> error(cancelled)；异常 -> error(tool_exception)
- 跨重试累加 usage 填 metadata（成本计量 Pillar 3）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.context import ExecContext
from app.agent.cost import accumulate_usage, build_metadata
from app.agent.registry import Tool
from app.llm.base import LLMUsage, TransientLLMError
from app.tools.base import ToolError, ToolInput, ToolMetadata, ToolOutput

log = logging.getLogger("deep_tutor.agent")

# 工具调用修复：首次失败后最多重试 3 次（对齐 plan：修复重试≤3）
REPAIR_MAX_RETRIES = 3
# LLM API 瞬态错误（限流/连接/5xx）退避重试次数：不占修复重试额度（err.md B1）
LLM_TRANSIENT_MAX_RETRIES = 3

# prompt 大小守卫（err.md B2）：单字符串字段截断 + text/chunks 去重 + 总量硬上限。
# upstream 全量广播会把 parse_material 的 text+chunks（同一内容两份）塞进每个
# 下游 LLM 工具，20 条资料极端下 prompt 超 1MB -> 超 context 400 / 成本爆炸。
_MAX_PROMPT_FIELD_CHARS = 20_000
_MAX_PROMPT_CHARS = 200_000

DEFAULT_SKILL_PROMPT = (
    "你是 Deep-tutor 的工具执行器。请根据输入完成任务，并仅返回符合约定 schema 的 "
    "JSON 对象（不要包含多余解释或 markdown 代码块）。"
)


async def run_tool(name: str, inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    """统一工具包装器（plan/10 §十 签名 run_tool(name, inp, ctx)）。"""
    if ctx.registry is None:
        raise RuntimeError("ExecContext.registry 未设置")
    tool = ctx.registry.get(name)
    t0 = time.monotonic()

    if ctx.cancel_event.is_set():
        return _error_output(tool, "cancelled", "取消信号已触发")

    try:
        if tool.llm:
            out = await _run_llm_tool(tool, inp, ctx)
        else:
            out = await _run_python_tool(tool, inp, ctx)
    except asyncio.TimeoutError:
        limit = ctx.timeouts.llm if tool.llm else ctx.timeouts.parse
        return _error_output(tool, "upstream_timeout", f"{name} 超时（{limit}s）")
    except Exception as exc:  # noqa: BLE001  统一兜底，避免单工具异常炸掉编排器
        log.exception("工具 %s 执行异常", name)
        return _error_output(tool, "tool_exception", str(exc))

    out.metadata.duration_ms = int((time.monotonic() - t0) * 1000)
    return out


# ---------------------------------------------------------------------------
# Python 工具
# ---------------------------------------------------------------------------


async def _run_python_tool(tool: Tool, inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    assert tool.func is not None
    # err.md B6：Python 工具同样受超时约束（Timeouts.parse 此前是死配置，
    # whisper 转写等在 ffprobe 缺失时可无限挂起）
    result = await asyncio.wait_for(tool.func(inp, ctx), timeout=ctx.timeouts.parse)
    if isinstance(result, ToolOutput):
        return result
    return ToolOutput(tool=tool.name, status="ok", data=result or {})


# ---------------------------------------------------------------------------
# LLM 工具（含工具调用修复）
# ---------------------------------------------------------------------------


async def _run_llm_tool(tool: Tool, inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    if ctx.llm_provider is None:
        return _degraded(tool, "no_llm_provider", "LLM provider 未配置")

    system = _get_system_prompt(tool, ctx)
    user = _build_user(inp)
    total = LLMUsage()
    last_error: str | None = None
    last_model = ""

    for attempt in range(1, REPAIR_MAX_RETRIES + 2):  # 1 + REPAIR_MAX_RETRIES 次尝试
        if ctx.cancel_event.is_set():
            return _error_output(tool, "cancelled", "取消信号已触发")
        message = user if attempt == 1 else user + _repair_feedback(last_error)
        resp = await _chat_with_retry(ctx, system, message)
        accumulate_usage(total, resp.usage)
        last_model = resp.usage.model or last_model
        if resp.stop_reason == "max_tokens":
            # err.md B5：截断输出必然 JSON 解析失败，白烧修复重试；
            # 提高 max_tokens 重试一次，仍截断则直接降级
            log.warning("工具 %s 输出被 max_tokens 截断，提高上限重试一次", tool.name)
            resp = await _chat_with_retry(ctx, system, message, max_tokens=8192)
            accumulate_usage(total, resp.usage)
            last_model = resp.usage.model or last_model
            if resp.stop_reason == "max_tokens":
                out = _degraded(tool, "max_tokens", "输出超 max_tokens 上限被截断")
                out.metadata = build_metadata(total, last_model)
                return out
        data, err = _parse_and_validate(resp.content, tool.output_model)
        if err is None:
            if tool.post_process:
                data = tool.post_process(data, inp)
            return ToolOutput(
                tool=tool.name,
                status="ok",
                data=data,
                metadata=build_metadata(total, last_model),
            )
        last_error = err
        log.warning("工具 %s 第 %d 次校验失败: %s", tool.name, attempt, err)

    # 重试耗尽 -> 降级默认 + 告警（Pillar 2）
    out = _degraded(tool, "schema_violation", f"修复重试 {REPAIR_MAX_RETRIES} 次仍不符: {last_error}")
    out.metadata = build_metadata(total, last_model)
    return out


async def _chat_with_retry(
    ctx: ExecContext, system: str, message: str, *, max_tokens: int = 4096
):
    """LLM 调用 + 瞬态错误指数退避（err.md B1：限流/连接失败/5xx 不占修复重试额度）。

    超时（asyncio.TimeoutError）与非瞬态 API 错误（4xx 等）不重试，直接上抛。
    """
    assert ctx.llm_provider is not None
    delay = 1.0
    for i in range(LLM_TRANSIENT_MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                ctx.llm_provider.chat(system, message, max_tokens=max_tokens, cache_system=True),
                timeout=ctx.timeouts.llm,
            )
        except TransientLLMError as exc:
            if i >= LLM_TRANSIENT_MAX_RETRIES or ctx.cancel_event.is_set():
                raise
            log.warning("LLM 瞬态错误（第 %d/%d 次）: %s；%.1fs 后重试",
                        i + 1, LLM_TRANSIENT_MAX_RETRIES + 1, exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)
    raise AssertionError("unreachable")  # pragma: no cover


def _get_system_prompt(tool: Tool, ctx: ExecContext) -> str:
    if tool.skill_name and ctx.skill_registry:
        prompt = ctx.skill_registry.get(tool.skill_name)
        if prompt:
            return prompt
        log.warning("Skill 缺失: %s，降级 inline/默认 prompt（R13）", tool.skill_name)
    if tool.inline_prompt:
        return tool.inline_prompt
    return DEFAULT_SKILL_PROMPT


def _build_user(inp: ToolInput) -> str:
    payload = _sanitize_for_prompt({"args": inp.args, "upstream": inp.upstream})
    s = json.dumps(payload, ensure_ascii=False)
    if len(s) > _MAX_PROMPT_CHARS:
        log.warning("LLM user 消息 %d 字符超上限 %d，硬截断（建议减少资料量）", len(s), _MAX_PROMPT_CHARS)
        s = s[:_MAX_PROMPT_CHARS]
    return s


def _sanitize_for_prompt(obj: Any) -> Any:
    """递归清洗 LLM user 消息载体（err.md B2 prompt 守卫）：

    - 同层 dict 同时含 text 与 chunks 时丢弃 chunks（parse_material 输出中同一
      内容的第二份拷贝，下游无人消费，全量广播等于逐工具重复计费）；
    - 超长字符串截断（防单字段塞入整份资料）。
    """
    if isinstance(obj, dict):
        return {
            k: _sanitize_for_prompt(v)
            for k, v in obj.items()
            if not (k == "chunks" and "text" in obj)
        }
    if isinstance(obj, list):
        return [_sanitize_for_prompt(v) for v in obj]
    if isinstance(obj, str) and len(obj) > _MAX_PROMPT_FIELD_CHARS:
        return obj[:_MAX_PROMPT_FIELD_CHARS] + f"…[已截断，原长 {len(obj)} 字符]"
    return obj


def _repair_feedback(error: str | None) -> str:
    return (
        "\n\n[校验失败反馈] 上次输出未通过 schema 校验："
        f"{error}\n请修正后仅返回符合 schema 的合法 JSON 对象。"
    )


def _parse_and_validate(content: str, output_model: type[BaseModel] | None) -> tuple[dict[str, Any], str | None]:
    parsed = _extract_json(content)
    if parsed is None:
        return {}, f"无法解析为 JSON（原文前 200 字）: {content[:200]!r}"
    if output_model is None:
        return parsed, None
    try:
        validated = output_model.model_validate(parsed)
    except ValidationError as exc:
        return {}, f"schema 校验失败: {exc}"
    return validated.model_dump(by_alias=True), None


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象（容忍 ```json 代码块与前后多余文字）。"""
    if not text:
        return None
    s = text.strip()
    # 去除 ```json ... ``` 代码块
    if s.startswith("```"):
        s = s.split("```", 2)
        s = s[1] if len(s) >= 2 else text
        # err.md B14：仅当 "json" 是语言标记（后随空白/换行）时才剥离，
        # 避免正文恰好以 "json" 开头被误裁 4 字符
        if s[:4].lower() == "json" and len(s) > 4 and s[4] in "\r\n\t ":
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 兜底：取首个 { 到末个 }
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _degraded(tool: Tool, code: str, message: str) -> ToolOutput:
    """降级：有 default_data -> repaired；无 -> error。"""
    if tool.default_data is not None:
        return ToolOutput(
            tool=tool.name,
            status="repaired",
            data=dict(tool.default_data),
            errors=[ToolError(code=code, message=message)],
        )
    return ToolOutput(
        tool=tool.name,
        status="error",
        data={},
        errors=[ToolError(code=code, message=message)],
    )


def _error_output(tool: Tool, code: str, message: str) -> ToolOutput:
    return ToolOutput(
        tool=tool.name,
        status="error",
        data={},
        errors=[ToolError(code=code, message=message)],
        metadata=ToolMetadata(),
    )
