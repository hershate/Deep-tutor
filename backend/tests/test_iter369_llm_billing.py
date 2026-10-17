"""iter 369 第三轮审计 B13：LLM 供应层与计费闭环回归。

覆盖：
- AsyncAnthropic 共享缓存 FIFO 8 -> LRU 32（多用户 >8 凭证下热点 key 不再被反复淘汰重建）
  + 显式 HTTP 超时（SDK 默认 read=600s）
- stream()/stream_agentic() 异常/取消路径显式关流（SDK AsyncStream 无 __del__）
- stream() 异常前补发部分 usage（与 stream_agentic iter 365 B1 同口径，防计量黑洞）
- agent_loop 硬取消（task.cancel）打断流读取时补记本轮已计费 tokens
- check_daily_budget 可索引谓词（本地日界语义保持：跨日行不计数、当日行计数）
- get_active_profile 多 active 行定序（并发 activate 竞态下稳定取最早档案）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx

from app.agent.agent_loop import AgentToolResult, run_agent_loop
from app.agent.context import ExecContext
from app.db import models
from app.db.base import SessionLocal
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import AgenticStreamChunk, LLMUsage, TransientLLMError


# --- 共享 client 缓存：LRU + 超时 ---


def test_client_cache_lru_hit_stays(monkeypatch):
    from app.llm import anthropic_provider as ap

    ap.reset_client_cache()
    monkeypatch.setattr(ap, "_CLIENT_CACHE_MAX", 4)
    k1 = ap._shared_client("k1", None)
    ap._shared_client("k2", None)
    assert ap._shared_client("k1", None) is k1  # 命中（不重建）
    ap._shared_client("x0", None)
    ap._shared_client("x1", None)  # 满 4
    ap._shared_client("x2", None)  # 淘汰最久未用 = k2（旧 FIFO 同样淘汰 k2）
    assert ("k2", None) not in ap._clients
    assert ("k1", None) in ap._clients  # LRU 命中续位 -> k1 驻留（旧 FIFO 下将被淘汰）
    ap.reset_client_cache()


def test_client_cache_explicit_timeout():
    from app.llm import anthropic_provider as ap

    ap.reset_client_cache()
    c = ap._shared_client("timeout-probe", None)
    assert isinstance(c.timeout, httpx.Timeout)
    assert c.timeout.read == 300.0  # 显式超时（SDK 默认 read=600s）
    ap.reset_client_cache()


# --- stream()/stream_agentic()：异常关流 + 部分 usage 补发 ---


class _FakeAsyncStream:
    def __init__(self, script: list):
        self._script = list(script)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._script:
            raise StopAsyncIteration
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.closed = True


class _FakeMessages:
    def __init__(self, script: list):
        self._script = script
        self.last_stream = None

    async def create(self, **kwargs):
        assert kwargs.get("stream") is True
        stream = _FakeAsyncStream(list(self._script))
        self.last_stream = stream
        return stream


def _mk_provider(script: list) -> tuple[AnthropicProvider, _FakeMessages]:
    msgs = _FakeMessages(script)
    provider = AnthropicProvider("k", "claude-fake", client=SimpleNamespace(messages=msgs))
    return provider, msgs


def _conn_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(message="boom", request=httpx.Request("POST", "https://api.anthropic.com"))


def _message_start(input_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                cache_read_input_tokens=None,
                cache_creation_input_tokens=None,
            )
        ),
    )


def test_stream_yields_partial_usage_and_closes_on_error():
    provider, msgs = _mk_provider(
        [
            _message_start(42),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="hi")),
            _conn_error(),
        ]
    )
    chunks = []

    async def run():
        try:
            async for c in provider.stream("sys", "u"):
                chunks.append(c)
        except TransientLLMError:
            pass  # 预期：连接错误映射为瞬态

    asyncio.run(run())
    usage = [c for c in chunks if c.usage]
    assert usage and usage[0].usage.input_tokens == 42  # 异常前已计费 tokens 补发
    assert msgs.last_stream.closed is True  # 异常路径显式关流


def test_stream_closes_on_normal_completion():
    provider, msgs = _mk_provider(
        [
            _message_start(5),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(output_tokens=7),
                delta=SimpleNamespace(stop_reason="end_turn"),
            ),
        ]
    )
    chunks = []

    async def run():
        async for c in provider.stream("sys", "u"):
            chunks.append(c)

    asyncio.run(run())
    assert chunks[-1].usage.input_tokens == 5 and chunks[-1].usage.output_tokens == 7
    assert msgs.last_stream.closed is True  # 正常路径 close 幂等


def test_stream_agentic_closes_on_error():
    provider, msgs = _mk_provider(
        [
            _message_start(11),
            _conn_error(),
        ]
    )
    chunks = []

    async def run():
        try:
            async for c in provider.stream_agentic([], system="s"):
                chunks.append(c)
        except TransientLLMError:
            pass

    asyncio.run(run())
    usage = [c for c in chunks if c.usage]
    assert usage and usage[0].usage.input_tokens == 11
    assert msgs.last_stream.closed is True


# --- agent_loop：硬取消打断流读取 -> 补记本轮已计费 tokens ---


def test_hard_cancel_records_partial_usage():
    db = SessionLocal()
    u = models.User(name="u", username="u", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running)
    db.add(r)
    db.commit()
    db.refresh(r)
    hang = asyncio.Event()

    class _HangingProvider:
        async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
            # message_start 的 usage 已到达（真实流中 input_tokens 在此已计费）
            yield AgenticStreamChunk(usage=LLMUsage(input_tokens=77, output_tokens=2, model="fake"))
            await hang.wait()  # 挂起模拟流读取中途被硬取消

    async def executor(tu, ctx):  # noqa: ARG001
        return AgentToolResult(content="ok")

    async def main():
        ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id)
        ctx.thread_id = t.id
        ctx.run_id = r.id
        task = asyncio.ensure_future(
            run_agent_loop(
                ctx,
                thread_id=t.id,
                run_id=r.id,
                user_message="x",
                provider=_HangingProvider(),
                tools=[],
                tool_executor=executor,
            )
        )
        await asyncio.sleep(0.15)  # 跑到流挂起点
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 预期：取消语义保持（重抛）

    asyncio.run(main())
    usages = db.query(models.ApiUsage).filter_by(user_id=u.id).all()
    assert any(au.tokens_in == 77 for au in usages), "硬取消路径的已计费 tokens 必须入账"
    db.close()


# --- check_daily_budget：本地日界谓词（可索引形式） ---


def test_check_daily_budget_local_day_predicate():
    from app.api.deps import check_daily_budget
    from app.api.errors import ApiError

    db = SessionLocal()
    u = models.User(name="b", username="b", password_hash="x")
    db.add(u)
    db.flush()
    db.add(models.UserPref(user_id=u.id, key="daily_budget_usd", value="5"))
    db.add(models.ApiUsage(user_id=u.id, cost=10.0, created_at=datetime.utcnow() - timedelta(days=2)))
    db.commit()

    check_daily_budget(db, u.id)  # 两天前的花费不计数（本地日界）

    db.add(models.ApiUsage(user_id=u.id, cost=3.0))  # created_at 默认 now（naive UTC）
    db.commit()
    check_daily_budget(db, u.id)  # 3 < 5 不拦

    db.add(models.ApiUsage(user_id=u.id, cost=3.0))
    db.commit()
    try:
        check_daily_budget(db, u.id)
        raise AssertionError("6 >= 5 应抛 budget_exceeded")
    except ApiError as exc:
        assert exc.code == "budget_exceeded"
    finally:
        db.close()


# --- get_active_profile：多 active 行定序 ---


def test_get_active_profile_deterministic_order():
    from app.llm import get_active_profile

    db = SessionLocal()
    u = models.User(name="p", username="p", password_hash="x")
    db.add(u)
    db.flush()
    # 并发 activate 竞态形态：两条 active（id 小者先启用）
    db.add(models.LlmProviderProfile(user_id=u.id, name="first", api_key="k1", model="m1", is_active=True))
    db.flush()
    db.add(models.LlmProviderProfile(user_id=u.id, name="second", api_key="k2", model="m2", is_active=True))
    db.commit()

    for _ in range(5):
        prof = get_active_profile(u.id, db=db)
        assert prof is not None and prof["name"] == "first"  # 稳定取最早启用（非随机）
    db.close()
