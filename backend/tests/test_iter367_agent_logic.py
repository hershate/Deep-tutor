"""iter 367 批次三（Agent 逻辑）回归测试。

覆盖：
- thinking 签名链路：_StreamAccumulator 重组含 signature；load_thread_messages
  剔除无签名 thinking（Anthropic 回传契约）
- 悬空 tool_use 自愈：尾部 / 中途多组悬空补占位 tool_result（防会话永久 400）
- BudgetTracker.add 并发不丢账（材料流多 worker 归集）
- 单元补料筛选用量归集：预算累计 + api_usage 补记（防成本黑洞）
- approve：CAS 后第二次 409、workspace_committed 置位、note 只建一条
- _finalize_chat 不降级已审批的 done run
- cancel_run 硬取消：流式长读取中的 run 随 task.cancel 收敛（不再挂满 SDK 超时）
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from app.agent.agent_loop import (
    AgentRunResult,
    _StreamAccumulator,
    load_thread_messages,
)
from app.agent.budget import BudgetTracker
from app.agent.context import ExecContext
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import AgenticStreamChunk, LLMUsage


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


def _mk_thread_with_run(status=None, committed: bool = False):
    db = SessionLocal()
    u = models.User(name="u367", username="u367", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t367")
    db.add(t)
    db.commit()
    db.refresh(t)
    run = None
    if status is not None:
        run = models.Run(
            thread_id=t.id,
            user_id=u.id,
            kind=models.RunKind.chat,
            status=status,
            workspace_committed=committed,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    return db, u, t, run


# --- thinking 签名链路 ---


def test_stream_accumulator_rebuilds_thinking_with_signature():
    acc = _StreamAccumulator()
    acc.feed(AgenticStreamChunk(thinking="先想一下"))
    acc.feed(AgenticStreamChunk(signature="sig-abc123"))
    acc.feed(AgenticStreamChunk(text="答案"))
    blocks = acc.to_content_blocks()
    thinking = [b for b in blocks if isinstance(b, dict) and b.get("type") == "thinking"]
    assert thinking, "thinking 块应被重建"
    assert thinking[0]["signature"] == "sig-abc123", "回传历史必需的签名不得丢失"
    assert any(b.get("type") == "text" and b.get("text") == "答案" for b in blocks)


def test_load_thread_messages_strips_unsigned_thinking():
    """无签名 thinking 回传会被 Anthropic 拒——加载历史时剔除；清空的消息补占位。"""
    db, u, t, _ = _mk_thread_with_run()
    try:
        db.add_all(
            [
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.user,
                    content=[{"type": "text", "text": "hi"}],
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    # 崩溃/旧数据：裸 thinking（无 signature）+ 正常 text
                    content=[
                        {"type": "thinking", "thinking": "裸思考"},
                        {"type": "text", "text": "回答"},
                    ],
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    # 整条只有裸 thinking -> 剔除后须补占位防空 content
                    content=[{"type": "thinking", "thinking": "只有思考"}],
                ),
            ]
        )
        db.commit()
        msgs = load_thread_messages(db, t.id, limit=10)
        assert len(msgs) == 3
        assert msgs[1]["content"] == [{"type": "text", "text": "回答"}]
        assert msgs[2]["content"] == [{"type": "text", "text": "…"}], "空 content 会被 API 拒，须占位"
    finally:
        db.close()


def test_load_thread_messages_keeps_signed_thinking():
    db, u, t, _ = _mk_thread_with_run()
    try:
        db.add(
            models.Message(
                thread_id=t.id,
                role=models.MessageRole.assistant,
                content=[
                    {"type": "thinking", "thinking": "思考", "signature": "sig-1"},
                    {"type": "text", "text": "答"},
                ],
            )
        )
        db.commit()
        msgs = load_thread_messages(db, t.id, limit=10)
        assert msgs[0]["content"][0]["type"] == "thinking"
        assert msgs[0]["content"][0]["signature"] == "sig-1", "有效签名 thinking 不应被误删"
    finally:
        db.close()


# --- 悬空 tool_use 自愈 ---


def test_load_thread_messages_repairs_dangling_tail_tool_use():
    """进程在工具执行期间崩溃（assistant 落库、tool_result 未写）-> 补占位，防永久 400。"""
    db, u, t, _ = _mk_thread_with_run()
    try:
        db.add(
            models.Message(
                thread_id=t.id,
                role=models.MessageRole.assistant,
                content=[{"type": "tool_use", "id": "tu_x", "name": "echo", "input": {}}],
            )
        )
        db.commit()
        msgs = load_thread_messages(db, t.id, limit=10)
        assert len(msgs) == 2, "应追加一条占位 tool_result user 消息"
        assert msgs[1]["role"] == "user"
        tr = msgs[1]["content"][0]
        assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu_x"
        assert tr["is_error"] is True
        # 幂等：重复加载结果一致
        msgs2 = load_thread_messages(db, t.id, limit=10)
        assert len(msgs2) == 2
    finally:
        db.close()


def test_load_thread_messages_repairs_multiple_dangling():
    """中途 + 尾部多组悬空（连续崩溃/旧脏数据）都要修——只修最后一组仍会 400。"""
    db, u, t, _ = _mk_thread_with_run()
    try:
        db.add_all(
            [
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    content=[{"type": "tool_use", "id": "tu_1", "name": "echo", "input": {}}],
                ),
                # 跟了纯文本 user（无 tool_result）-> tu_1 悬空
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.user,
                    content=[{"type": "text", "text": "还在吗"}],
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    content=[{"type": "tool_use", "id": "tu_2", "name": "echo", "input": {}}],
                ),
                # 尾部悬空：无后续消息
            ]
        )
        db.commit()
        msgs = load_thread_messages(db, t.id, limit=10)
        # tu_1 的占位并入其后 user 消息（tool_result + 原 text 同消息合法）
        assert msgs[1]["role"] == "user"
        kinds = [(b.get("type"), b.get("tool_use_id") or b.get("text")) for b in msgs[1]["content"]]
        assert ("tool_result", "tu_1") in kinds
        assert ("text", "还在吗") in kinds
        # tu_2 尾部悬空 -> 插入新 user 消息
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"][0]["tool_use_id"] == "tu_2"
    finally:
        db.close()


def test_load_thread_messages_healthy_history_untouched():
    db, u, t, _ = _mk_thread_with_run()
    try:
        db.add_all(
            [
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    content=[{"type": "tool_use", "id": "tu_ok", "name": "echo", "input": {}}],
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.user,
                    content=[{"type": "tool_result", "tool_use_id": "tu_ok", "content": "ok"}],
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    content=[{"type": "text", "text": "完成"}],
                ),
            ]
        )
        db.commit()
        msgs = load_thread_messages(db, t.id, limit=10)
        assert len(msgs) == 3, "健康历史不得被注入占位"
        assert msgs[1]["content"] == [{"type": "tool_result", "tool_use_id": "tu_ok", "content": "ok"}]
    finally:
        db.close()


# --- BudgetTracker 并发 ---


def test_budget_add_concurrent_no_lost_update():
    """材料流多 worker 并发归集筛选成本：add 须原子（锁），否则 float += 丢账。"""
    tracker = BudgetTracker(limit=0)  # 不限
    n_threads, n_adds, unit = 8, 3000, 0.001

    def hammer():
        for _ in range(n_adds):
            tracker.add(unit)

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    expected = n_threads * n_adds * unit
    assert abs(tracker.spent - expected) < 1e-9, f"并发 add 丢账：{tracker.spent} != {expected}"


# --- 单元补料筛选用量归集 ---


class _ScreenProvider:
    """按脚本逐次返回 chat 结果（content + usage）。"""

    def __init__(self, contents: list[str], tokens_in: int = 10):
        self._contents = list(contents)
        self._tokens_in = tokens_in
        self.calls = 0

    async def chat(self, system, user, **kwargs):  # noqa: ARG002
        self.calls += 1
        return SimpleNamespace(
            content=self._contents.pop(0),
            usage=LLMUsage(input_tokens=self._tokens_in, output_tokens=2, model="claude-sonnet-5"),
        )


def test_screen_unit_resources_accumulates_usage_across_retries():
    """重试各次的 usage 都要累计返回（此前被丢弃 -> 成本黑洞）。"""
    from app.tools.search_resources import _SCREEN_MAX_ATTEMPTS, _screen_unit_resources

    res_url = "https://example.com/a"
    bad = "不是 JSON"
    good = json.dumps([{"url": res_url, "suitable": True, "label": "ok", "note": ""}])
    # 首次坏响应 + 最终好响应（_SCREEN_MAX_ATTEMPTS 次内）
    provider = _ScreenProvider([bad] * (_SCREEN_MAX_ATTEMPTS - 1) + [good], tokens_in=10)
    ctx = ExecContext(session_id="s", trace_id="t", llm_provider=provider)
    resources = [{"title": "A", "url": res_url, "snippet": "正文"}]
    screened, usage = asyncio.run(_screen_unit_resources(resources, "主题", ctx))
    assert provider.calls == _SCREEN_MAX_ATTEMPTS
    assert screened and screened[0]["url"] == res_url
    assert usage is not None
    assert usage.input_tokens == 10 * _SCREEN_MAX_ATTEMPTS, "各次调用（含失败重试）的 usage 均须累计"


def test_screen_unit_resources_returns_usage_even_when_exhausted():
    """重试耗尽全部丢弃时，已花费的 usage 仍须返回（钱已烧，不能从账上消失）。"""
    from app.tools.search_resources import _SCREEN_MAX_ATTEMPTS, _screen_unit_resources

    provider = _ScreenProvider(["垃圾"] * _SCREEN_MAX_ATTEMPTS, tokens_in=15)
    ctx = ExecContext(session_id="s", trace_id="t", llm_provider=provider)
    resources = [{"title": "A", "url": "https://example.com/a", "snippet": "正文"}]
    screened, usage = asyncio.run(_screen_unit_resources(resources, "主题", ctx))
    assert screened == []
    assert usage is not None and usage.input_tokens == 15 * _SCREEN_MAX_ATTEMPTS


def test_account_screen_usage_records_budget_and_api_usage():
    from app.tools.search_resources import _account_screen_usage

    db, u, t, _ = _mk_thread_with_run()
    uid = u.id
    db.close()
    usage = LLMUsage(input_tokens=100, output_tokens=20, model="claude-sonnet-5")
    ctx = ExecContext(session_id="s", trace_id="t", budget=BudgetTracker(limit=0), user_id=uid)
    asyncio.run(_account_screen_usage(usage, ctx))
    assert ctx.budget.spent > 0, "筛选用量须计入会话预算"
    check = SessionLocal()
    try:
        rows = check.query(models.ApiUsage).filter_by(user_id=uid).all()
        assert rows, "筛选用量须补记 api_usage（日预算/用量页数据源）"
        assert rows[-1].tokens_in == 100
    finally:
        check.close()


def test_account_screen_usage_skips_api_write_without_user():
    from app.tools.search_resources import _account_screen_usage

    usage = LLMUsage(input_tokens=100, output_tokens=20, model="claude-sonnet-5")
    ctx = ExecContext(session_id="s", trace_id="t", budget=BudgetTracker(limit=0))  # 无 user_id
    asyncio.run(_account_screen_usage(usage, ctx))
    assert ctx.budget.spent > 0, "预算仍须累计"


# --- approve：CAS + committed 标志 ---


def test_approve_once_then_second_409_and_committed(client, auth_headers):
    """CAS 后第二次 approve 409；note 只建一条；workspace_committed 置位（防 24h 误删）。"""
    cur_id = client.get("/api/auth/me", headers=auth_headers).json()["id"]
    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    db = SessionLocal()
    run = models.Run(thread_id=t["id"], user_id=cur_id, status=models.RunStatus.awaiting_approval)
    db.add(run)
    db.commit()
    db.refresh(run)
    rel = f"test_iter367_approve_{run.id}.json"
    (Path(settings.data_dir) / rel).write_text("笔记内容", encoding="utf-8")
    art = models.RunArtifact(thread_id=t["id"], run_id=run.id, name="p", path=rel, kind="draft")
    db.add(art)
    db.commit()
    db.refresh(art)
    db.add(
        models.RunStep(
            run_id=run.id,
            type="approval",
            input={"name": "p", "target_kind": "note", "artifact_id": art.id},
        )
    )
    db.commit()
    rid = run.id
    db.close()

    r1 = client.post(f"/api/runs/{rid}/approve", json={"target_kind": "note"}, headers=auth_headers)
    assert r1.status_code == 200
    r2 = client.post(f"/api/runs/{rid}/approve", json={"target_kind": "note"}, headers=auth_headers)
    assert r2.status_code == 409, "并发/重复 approve 不得重复建 note（CAS 抢占）"

    db = SessionLocal()
    try:
        run = db.get(models.Run, rid)
        assert run.status == models.RunStatus.done
        assert run.workspace_committed is True, "已审批 run 须置 committed（否则 24h 维护 rmtree 已提升产物）"
        notes = db.query(models.Note).filter_by(user_id=cur_id, title="p").all()
        assert len(notes) == 1
    finally:
        db.close()


# --- finalize 不降级已审批终态 ---


def test_finalize_chat_does_not_downgrade_approved_done():
    """approve 已把 run 落为 done 后，agent 任务尾随 interrupted 结果不得降级覆盖。"""
    from app.api.memory import _ChatStream, _finalize_chat

    db, u, t, run = _mk_thread_with_run(status=models.RunStatus.done, committed=True)
    try:
        stream = _ChatStream()  # 测试上下文创建：_loop=None，仅用 publish/mark_terminal
        stream.result = AgentRunResult(
            status="interrupted", final_text="", steps=1,
            usage=LLMUsage(input_tokens=1, output_tokens=1, model="fake"),
            error="cancelled",
        )
        _finalize_chat(db, run.id, t.id, stream)
        db.expire_all()
        run2 = db.get(models.Run, run.id)
        assert run2.status == models.RunStatus.done, "已审批 done 不得被 interrupted 降级"
        assert run2.workspace_committed is True
    finally:
        db.close()


# --- cancel_run 硬取消 ---


class _HangingProvider(AnthropicProvider):
    """yield 首块后长睡——模拟 SDK 流式读取不可打断（cancel_event 步边界失效场景）。"""

    def __init__(self):
        self._model = "fake-model"
        self._client = None

    async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
        yield AgenticStreamChunk(text="开始")
        await asyncio.sleep(3600)
        yield AgenticStreamChunk(text="不会到达")


def test_cancel_run_hard_cancels_hanging_stream(client, auth_headers, monkeypatch):
    """iter 367：流式长读取中的 run 随 /cancel 的 task.cancel 收敛为 interrupted。"""
    from app.api.memory import _RUN_STREAMS

    provider = _HangingProvider()
    monkeypatch.setattr("app.api.memory.get_provider", lambda *a, **k: provider)
    t = client.post("/api/threads", json={"title": "硬取消"}, headers=auth_headers).json()
    holder = {}

    def run_sse():
        holder["resp"] = client.post(
            f"/api/threads/{t['id']}/runs",
            json={"message": "hi"},
            headers={**auth_headers, "Accept": "text/event-stream"},
        )

    bg = threading.Thread(target=run_sse)
    bg.start()
    rid = None
    deadline = time.time() + 15
    while time.time() < deadline:
        db = SessionLocal()
        run = (
            db.query(models.Run)
            .filter_by(thread_id=t["id"], status=models.RunStatus.running)
            .first()
        )
        db.close()
        if run is not None:
            st = _RUN_STREAMS.get(run.id)
            if st is not None and st.task is not None:
                rid = run.id
                break
        time.sleep(0.05)
    assert rid is not None, "run 未注册或 task 未绑定"

    r = client.post(f"/api/runs/{rid}/cancel", headers=auth_headers)
    assert r.status_code == 200
    bg.join(timeout=30)
    assert not bg.is_alive(), "SSE 未随硬取消收敛（修复前 stream 读取不可打断，挂满 SDK 600s 超时）"

    events = _parse_sse(holder["resp"].text)
    assert events[-1][0] == "done"
    assert events[-1][1]["status"] == "interrupted"
    db = SessionLocal()
    try:
        assert db.get(models.Run, rid).status == models.RunStatus.interrupted
    finally:
        db.close()
