"""iter 397-B2：事件循环冻结类问题的回归（A4 / A5 / A6 / E2 / E4）。

本轮修复的问题（均为「在事件循环线程内做重活」或「无界排队」）：

- **A4**：`api/memory.py` 的 run 创建临界段用**模块级全局 asyncio.Lock**，
  且**无界等待**。临界段内含 SQLite 写（写锁争用时可等 busy_timeout 10s），
  一个慢事务把**所有用户**的 chat run 创建排成串行队列（第 N 个请求等
  N×10s，尾延迟无界）。另：模块级 asyncio.Lock 只在**争用路径**绑定事件
  循环，换循环再争用会抛「bound to a different event loop」。
- **A5**：`_run_chat` 的 finally 里**在事件循环线程同步**调用
  `_force_run_terminal`（内部 SessionLocal + commit）-> 写锁被占时冻结全站。
- **A6**：`agent/agent_loop.py` 每 step 在事件循环线程同步查库（Run 主键读 +
  50 条含 JSON 大字段的 Message），写路径此前已移出、读路径漏了。
- **E2**：`POST /api/auth/login` 是**同步端点** -> 跑在 anyio 工作线程池
  （默认 40 槽）；每次尝试一次 pbkdf2 600k 轮（~0.19s CPU），40 个并发登录
  即可占满全部槽位，使所有同步端点瘫痪（未认证的 CPU 放大器）。
- **E4**：`tools/export_plan.py` 的 Markdown 渲染在事件循环线程同步执行
  （PDF 路径早已 to_thread，md 路径漏了）。
"""

from __future__ import annotations

import asyncio
import threading

import pytest


# ---------------------------------------------------------------------------
# A4：run 创建临界段——按事件循环缓存 + 有界等待
# ---------------------------------------------------------------------------


def test_loop_lock_is_per_event_loop():
    """模块级 asyncio.Lock 跨事件循环复用会在争用时抛「bound to a different loop」。"""
    from app.core.concurrency import loop_lock

    async def scenario():
        return loop_lock("iter397-probe")

    first = asyncio.run(scenario())
    second = asyncio.run(scenario())
    assert first is not second, "锁未按事件循环隔离（跨循环复用会抛 RuntimeError）"


def test_loop_lock_is_stable_within_same_loop():
    from app.core.concurrency import loop_lock

    async def scenario():
        a = loop_lock("iter397-probe2")
        b = loop_lock("iter397-probe2")
        assert a is b
        return True

    assert asyncio.run(scenario())


def test_acquire_bounded_times_out_without_leaking_lock():
    """有界获取：拿不到锁时返回 False，且**不丢锁**（超时瞬间拿到也要归还）。"""
    from app.core.concurrency import acquire_bounded, loop_lock

    async def scenario() -> bool:
        lock = loop_lock("iter397-bounded")
        assert await acquire_bounded(lock, 0.5) is True
        lock.release()
        # 持锁不放 -> 第二次获取应超时返回 False
        assert await acquire_bounded(lock, 0.5) is True
        got = await acquire_bounded(lock, 0.1)
        if got:  # 竞态：恰好拿到（不可能，此处锁已被持有）
            lock.release()
        # 超时后原锁仍被持有 -> 释放一次即可重新获取（未泄漏许可）
        lock.release()
        assert await acquire_bounded(lock, 0.5) is True
        lock.release()
        return got is False

    assert asyncio.run(scenario())


def test_run_create_lock_timeout_returns_503(client, auth_headers, monkeypatch):
    """拿不到 run 创建临界段 -> 503 chat_run_busy（快失败，不再无界排队）。"""
    from app.api import memory as mem_mod

    async def _always_busy(lock, timeout):
        return False

    monkeypatch.setattr(mem_mod, "acquire_bounded", _always_busy)
    tid = client.post("/api/threads", json={"title": "iter397-b2"}, headers=auth_headers).json()["id"]
    r = client.post(f"/api/threads/{tid}/runs", json={"message": "hi"}, headers=auth_headers)
    assert r.status_code == 503, r.text
    assert "chat_run_busy" in r.text


# ---------------------------------------------------------------------------
# A5：强制收敛 run 终态不得在事件循环线程同步写库
# ---------------------------------------------------------------------------


def test_force_run_terminal_runs_off_event_loop_thread(monkeypatch):
    """_run_chat 收口失败时强制收敛 run 终态 -> 必须移出事件循环线程。"""
    from app.api import memory as mem_mod

    seen: dict = {}

    def _spy(run_id, status):
        seen["thread"] = threading.current_thread()

    async def _boom(*args, **kwargs):
        raise RuntimeError("finalize 失败（iter397 回归用例）")

    monkeypatch.setattr(mem_mod, "_force_run_terminal", _spy)
    monkeypatch.setattr(mem_mod, "_finalize_chat", _boom)

    async def scenario() -> None:
        stream = mem_mod._ChatStream()
        await mem_mod._run_chat(
            run_id=1,
            thread_id=1,
            user_id=1,
            message="hi",
            stream=stream,
            max_steps=1,
            thinking=False,
            model_override=None,
        )

    asyncio.run(scenario())
    assert "thread" in seen, "强制收敛路径未被触发"
    assert seen["thread"] is not threading.main_thread(), "强制收敛在事件循环线程同步执行"


# ---------------------------------------------------------------------------
# A6：agent loop 每 step 的读库移出事件循环
# ---------------------------------------------------------------------------


def test_load_thread_messages_runs_off_event_loop_thread(monkeypatch):
    """agent loop 的历史加载（50 条含 JSON 大字段的 Message）不得在事件循环线程。"""
    from app.agent import agent_loop as al_mod

    seen: dict = {}
    original = al_mod.load_thread_messages

    def _spy(*args, **kwargs):
        seen["thread"] = threading.current_thread()
        return original(*args, **kwargs)

    monkeypatch.setattr(al_mod, "load_thread_messages", _spy)

    class _FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        def limit(self, n):
            return self

        def all(self):
            return []

        def update(self, values):
            return 0

        def first(self):
            return None

    class _FakeDB:
        def get(self, *args, **kwargs):
            return None  # 非 awaiting_approval -> 继续走历史加载

        def query(self, *args, **kwargs):
            return _FakeQuery()

        def add(self, *args, **kwargs):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

    async def scenario() -> None:
        ctx = _FakeCtx()
        await al_mod.run_agent_loop(
            ctx,
            thread_id=1,
            run_id=1,
            user_message="hi",
            provider=_FakeProvider(),
            tools=[],
            tool_executor=None,
            system="s",
            max_steps=1,
            emit=None,
            thinking=False,
        )

    class _FakeCtx:
        db = _FakeDB()
        user_id = 1
        cancel_event = threading.Event()
        # iter 397（A3）：ExecContext 新增「审批待确认」信号（与 cancel_event 分离）
        approval_pending = threading.Event()
        budget = None
        model_pricing = None
        thread_id = 1
        run_id = 1

    class _FakeProvider:
        _model = "test-model"

        async def stream_agentic(self, messages, system=None, tools=None, thinking=False):
            if False:  # pragma: no cover - 生成器语法占位
                yield None

    asyncio.run(scenario())
    assert "thread" in seen, "历史加载路径未被触发"
    assert seen["thread"] is not threading.main_thread(), "历史加载在事件循环线程同步执行"


# ---------------------------------------------------------------------------
# E2：登录口令校验移出 anyio 线程池 + 并发闸
# ---------------------------------------------------------------------------


def test_login_is_async_endpoint():
    """登录端点改 async——同步端点会在 anyio 工作线程池里占槽做纯 CPU 的 pbkdf2。"""
    import inspect

    from app.api import auth as auth_mod

    assert inspect.iscoroutinefunction(auth_mod.login)


def test_login_pbkdf2_runs_in_dedicated_pool(client, monkeypatch):
    """口令校验走独立小线程池（不占 anyio 默认池槽位）。"""
    from app.api import auth as auth_mod
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="iter397", username="iter397user", password_hash=hash_password("Passw0rd!"))
        db.add(u)
        db.commit()
    finally:
        db.close()

    seen: dict = {}
    original = auth_mod.verify_password

    def _spy(password, stored):
        seen["thread"] = threading.current_thread()
        return original(password, stored)

    monkeypatch.setattr(auth_mod, "verify_password", _spy)
    r = client.post("/api/auth/login", json={"username": "iter397user", "password": "Passw0rd!"})
    assert r.status_code == 200, r.text
    assert "thread" in seen
    assert seen["thread"].name.startswith("pwd-verify"), (
        f"口令校验未走独立池（实际线程 {seen['thread'].name}）"
    )


def test_password_verify_slot_limits_concurrency(monkeypatch):
    """并发口令校验闸：超出上限者等待超时 -> PasswordVerifyBusy（可重试语义）。"""
    from app.core import concurrency as conc_mod
    from app.core.concurrency import PasswordVerifyBusy, password_verify_slot

    monkeypatch.setattr(conc_mod.settings, "max_concurrent_password_verifications", 1)

    async def scenario() -> bool:
        slot = password_verify_slot(0.2)
        async with slot:
            with pytest.raises(PasswordVerifyBusy):
                async with password_verify_slot(0.2):
                    pass
        return True

    assert asyncio.run(scenario())


def test_login_strips_username(client, monkeypatch):
    """登录用户名归一化（建号时 strip，登录不 strip -> 带空白账号永远登不上）。"""
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="iter397", username="iter397strip", password_hash=hash_password("Passw0rd!"))
        db.add(u)
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "  iter397strip  ", "password": "Passw0rd!"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# E4：md 导出的 Markdown 渲染移出事件循环
# ---------------------------------------------------------------------------


def test_render_markdown_runs_off_event_loop_thread(monkeypatch):
    """方案 Markdown 渲染（可达 MB 级）不得在事件循环线程同步执行。"""
    from app.tools import export_plan as ep_mod

    seen: dict = {}
    original = ep_mod.render_markdown

    def _spy(plan):
        seen["thread"] = threading.current_thread()
        return original(plan)

    monkeypatch.setattr(ep_mod, "render_markdown", _spy)

    class _FakeCtx:
        data_dir = "data"

    async def scenario() -> None:
        await ep_mod.export_plan(
            _FakeToolInput(),  # type: ignore[arg-type]
            _FakeCtx(),  # type: ignore[arg-type]
        )

    class _FakeToolInput:
        session_id = "1"
        args = {"format": "md", "plan": {"title": "iter397", "stages": []}, "plan_id": "p1"}
        upstream: dict = {}

    asyncio.run(scenario())
    assert "thread" in seen
    assert seen["thread"] is not threading.main_thread(), "Markdown 渲染在事件循环线程同步执行"
