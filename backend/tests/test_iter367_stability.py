"""iter 367 批次四（稳定性）回归测试。

覆盖：
- webhook all-events sink 无循环分支：不阻塞调用线程（有界 daemon 线程投递）；
  线程达上限时超额非阻塞丢弃
- events.resolve_owner：session_id -> 归属用户 TTL 缓存（此前每事件×每订阅者开
  短会话查 DB，事件循环上同步执行）
- /api/events/stream SSE 响应头（no-cache/keep-alive/禁反代缓冲）
"""

from __future__ import annotations

import asyncio
import threading
import time

import app.plugins.webhooks as wh
from app.db import models
from app.db.base import SessionLocal


# --- webhook sink 线程分支 ---


def test_all_events_sink_thread_branch_nonblocking(monkeypatch):
    """无运行循环的线程（同步端点线程池）调 sink：投递走有界线程，不阻塞调用方。"""
    done = threading.Event()

    async def slow_deliver(url, event, payload, secret):  # noqa: ARG001
        await asyncio.sleep(0.3)
        done.set()

    monkeypatch.setattr(wh, "_deliver_event", slow_deliver)
    sink = wh.make_all_events_sink("http://example.com/hook", "s3cret")
    t0 = time.monotonic()
    sink("evt_x", {"a": 1})  # pytest 主线程无运行循环 -> 线程分支
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"sink 不得阻塞调用线程（耗时 {elapsed:.2f}s；修复前原地 asyncio.run 阻塞至投递完成）"
    assert done.wait(timeout=5), "投递线程应完成"
    time.sleep(0.1)  # 释放信号量在线程 finally，稍等后校验
    assert wh._sink_sem._value == wh._SINK_THREADS_MAX, "投递完成后信号量须归还"


def test_all_events_sink_thread_branch_drops_when_saturated(monkeypatch):
    """线程达上限：超额事件非阻塞丢弃 + 告警（best-effort 背压，不排队堆积）。"""
    called: list[str] = []

    async def fast_deliver(url, event, payload, secret):  # noqa: ARG001
        called.append(event)

    monkeypatch.setattr(wh, "_deliver_event", fast_deliver)
    sink = wh.make_all_events_sink("http://example.com/hook")
    for _ in range(wh._SINK_THREADS_MAX):
        assert wh._sink_sem.acquire(blocking=False)  # 手动占满
    try:
        t0 = time.monotonic()
        sink("evt_drop", {})
        assert time.monotonic() - t0 < 0.05, "饱和时须立即丢弃返回"
        time.sleep(0.2)
        assert called == [], "超额事件不应投递"
    finally:
        for _ in range(wh._SINK_THREADS_MAX):
            wh._sink_sem.release()


def test_all_events_sink_loop_branch_still_nonblocking():
    """回归：事件循环线程内 create_task 分支不受改动影响（非阻塞 + 完成投递）。"""
    done = threading.Event()

    async def scenario() -> float:
        async def quick_deliver(url, event, payload, secret):  # noqa: ARG001
            done.set()

        wh._deliver_event = quick_deliver  # 直接打补丁，scenario 内还原
        original = wh._deliver_event
        sink = wh.make_all_events_sink("http://example.com/hook")
        t0 = time.monotonic()
        sink("evt_loop", {})
        elapsed = time.monotonic() - t0
        await asyncio.sleep(0.05)  # 让 create_task 的投递协程跑完
        wh._deliver_event = original
        return elapsed

    elapsed = asyncio.run(scenario())
    assert elapsed < 0.1
    assert done.is_set()


# --- events.resolve_owner session_id 解析（iter 381 起：直查无缓存） ---


def test_resolve_owner_sid_direct_query(monkeypatch):
    """iter 381 语义更新：session_id -> owner 每次即时查库（原 60s TTL 缓存因
    删会话后 rowid 复用的跨用户错发窗口移除，见 events.py 注释）。本测试钉死
    「即时性」——连续解析同 sid 各查一次（无陈旧值），归属变化立即可见。"""
    from app.api import events as ev

    db = SessionLocal()
    u = models.User(name="u367b", username="u367b", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    sess = models.Session(user_id=u.id, title="s367")
    db.add(sess)
    db.commit()
    db.refresh(sess)
    sid, uid = sess.id, u.id
    db.close()

    calls: list[int] = []
    orig = ev._session_owner

    def counting(query_db, query_sid):
        calls.append(query_sid)
        return orig(query_db, query_sid)

    monkeypatch.setattr(ev, "_session_owner", counting)
    r1 = ev.resolve_owner({"session_id": sid})
    r2 = ev.resolve_owner({"session_id": sid})
    r3 = ev.resolve_owner({"session_id": sid})
    assert (r1, r2, r3) == (uid, uid, uid)
    assert len(calls) == 3, f"每次解析都应即时查库（实际查询 {len(calls)} 次）"


# --- events SSE 响应头 ---


def test_event_stream_sse_headers():
    from app.api.events import SSE_HEADERS, event_stream

    class _StubReq:  # iter 381：端点新增 request 参数（提取 Bearer iat 供心跳重验）
        headers: dict = {}

    resp = asyncio.run(event_stream(request=_StubReq(), user_id=1))
    assert resp.headers["cache-control"] == "no-cache", "反代/浏览器缓冲会把事件流攒到超时"
    assert resp.headers["x-accel-buffering"] == SSE_HEADERS["X-Accel-Buffering"]
    asyncio.run(resp.body_iterator.aclose())  # 退订 sink，防测试进程内泄漏
