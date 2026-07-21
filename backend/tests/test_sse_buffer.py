"""_SessionStream 事件缓冲 + _relay 续接单元测试（plan/10 §十二 断线续接游标）。

不联网、不经 HTTP；直接测缓冲语义与续接重放。异步场景用 asyncio.run 驱动（无 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio

from app.api.plans import (
    _MAX_BUFFERED_EVENTS,
    _RETENTION_SECONDS,
    _STREAMS,
    _SessionStream,
    _relay,
    cancel_running_generation,
)


# --- _SessionStream 同步语义 ---


def test_publish_assigns_increasing_ids():
    s = _SessionStream(1, "t")
    assert s.publish("tool_start", {"tool": "a"}) == 1
    assert s.publish("tool_end", {"tool": "a"}) == 2
    assert s.publish("done", {"plan_id": 9}) == 3
    assert s.seq == 3


def test_subscribe_replays_all_history():
    s = _SessionStream(1, "t")
    s.publish("tool_start", {"tool": "a"})
    s.publish("tool_end", {"tool": "a"})
    q = s.subscribe(0)
    assert q.qsize() == 2
    assert q.get_nowait() == (1, "tool_start", {"tool": "a"})
    assert q.get_nowait() == (2, "tool_end", {"tool": "a"})


def test_subscribe_replays_only_after_last_event_id():
    s = _SessionStream(1, "t")
    s.publish("a", {})  # 1
    s.publish("b", {})  # 2
    s.publish("c", {})  # 3
    q = s.subscribe(2)  # 仅 id > 2
    assert q.qsize() == 1
    assert q.get_nowait() == (3, "c", {})


def test_fanout_to_multiple_subscribers():
    s = _SessionStream(1, "t")
    q1 = s.subscribe(0)
    q2 = s.subscribe(0)
    s.publish("e1", {"x": 1})
    assert q1.get_nowait() == (1, "e1", {"x": 1})
    assert q2.get_nowait() == (1, "e1", {"x": 1})


def test_subscribe_then_publish_receives_history_then_live():
    s = _SessionStream(1, "t")
    s.publish("pre", {})  # 1 订阅前历史
    q = s.subscribe(0)
    s.publish("post", {})  # 2 订阅后实时
    assert q.get_nowait() == (1, "pre", {})
    assert q.get_nowait() == (2, "post", {})


def test_bounded_buffer_drops_oldest():
    s = _SessionStream(1, "t")
    for i in range(_MAX_BUFFERED_EVENTS + 10):
        s.publish("e", {"i": i})
    assert len(s._events) == _MAX_BUFFERED_EVENTS
    # 最旧 10 条被丢；现存最旧 id = 11
    assert s._events[0][0] == 11
    # 订阅 last_event_id=0 只能从现存最旧重放（降级，不报错）
    q = s.subscribe(0)
    assert q.qsize() == _MAX_BUFFERED_EVENTS
    assert q.get_nowait()[0] == 11


def test_mark_terminal_sets_terminal_id():
    s = _SessionStream(1, "t")
    eid = s.publish("done", {"plan_id": 1})
    assert s.terminal_id is None
    s.mark_terminal(eid)
    assert s.terminal_id == eid


def test_unsubscribe_removes_subscriber_idempotent():
    s = _SessionStream(1, "t")
    q = s.subscribe(0)
    assert q in s._subs
    s.unsubscribe(q)
    assert q not in s._subs
    s.unsubscribe(q)  # 重复移除不报错


# --- _relay 续接/重放（异步，asyncio.run 驱动） ---


def test_relay_reconnect_replays_missed_then_terminal():
    """断连后以 Last-Event-ID 重连：补投错过的事件 + 终态。"""

    async def scenario():
        s = _SessionStream(1, "t")
        s.publish("tool_start", {"tool": "a"})  # 1
        s.publish("tool_end", {"tool": "a"})  # 2

        # 第一个连接消费到 id=1 后断开（错过 2）
        gen1 = _relay(s, 0)
        chunk1 = await gen1.__anext__()
        assert "tool_start" in chunk1
        await gen1.aclose()  # 模拟客户端断连（触发 unsubscribe）

        # 生成完成：发 done + 标记终态
        s.publish("done", {"plan_id": 7})  # 3
        s.mark_terminal(3)

        # 重连：Last-Event-ID=1 -> 应补投 2 + done
        chunks = []
        async for c in _relay(s, 1):
            chunks.append(c)
        assert len(chunks) == 2
        assert "tool_end" in chunks[0]
        assert "done" in chunks[1]
        assert "plan_id" in chunks[1]

    asyncio.run(scenario())


def test_relay_late_reconnect_gets_terminal_from_history():
    """终态后迟到重连：从历史重放全部含终态，投递后退出。"""

    async def scenario():
        s = _SessionStream(1, "t")
        s.publish("tool_start", {"tool": "a"})  # 1
        eid = s.publish("done", {"plan_id": 5})  # 2
        s.mark_terminal(eid)

        chunks = []
        async for c in _relay(s, 0):
            chunks.append(c)
        assert len(chunks) == 2
        assert "tool_start" in chunks[0]
        assert "done" in chunks[1]

    asyncio.run(scenario())


def test_relay_ignores_keepalive_comment_in_event_logic():
    """心跳为 SSE 注释行，不影响事件 id 序列（_relay 仅投递真事件）。"""

    async def scenario():
        s = _SessionStream(1, "t")
        s.publish("tool_start", {"tool": "a"})  # 1
        # 不标终态；_relay 会因 timeout 进入心跳分支，但单测中事件已就绪不会等 15s
        gen = _relay(s, 0)
        chunk = await gen.__anext__()
        assert "tool_start" in chunk
        await gen.aclose()

    asyncio.run(scenario())


def test_cancel_running_generation_schedules_cancel():
    """cancel_running_generation 在任务所属循环上调度 cancel（线程安全；无活动任务 -> False）。"""

    async def scenario():
        s = _SessionStream(999, "t")
        _STREAMS[999] = s
        # 无 task -> False
        assert cancel_running_generation(999) is False

        async def slow():
            await asyncio.sleep(100)

        # 活动任务 -> True，且取消生效
        task = asyncio.create_task(slow())
        s.task = task
        assert cancel_running_generation(999) is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()

        # 终态（task 存在但 terminal）-> False
        s2 = _SessionStream(998, "t")
        _STREAMS[998] = s2
        t2 = asyncio.create_task(slow())
        s2.task = t2
        s2.mark_terminal(1)
        assert cancel_running_generation(998) is False
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass

        # 不存在的会话 -> False
        assert cancel_running_generation(12345) is False

    asyncio.run(scenario())


def test_occupy_cancels_previous_cleanup_timer(monkeypatch):
    """iter 237：同 session_id 在保留窗口内重新占位 -> 取消旧清理定时器，新流不被误删。

    复现链路（修复前 bug）：_finalize -> _schedule_cleanup 排 300s 后 _STREAMS.pop(session_id)
    -> 用户在窗口内重试/重生成 -> _occupy_generation 覆盖 _STREAMS[session_id] 但不取消旧
    定时器 -> 旧定时器到点 pop 掉正在运行的新流（配额少算/无法取消/续接拿不到缓冲）。
    修复：_schedule_cleanup 把句柄存到旧流 _cleanup_timer，_occupy_generation 取消之。
    """
    import app.api.plans as plans_mod

    sid = 4242

    async def scenario():
        loop = asyncio.get_running_loop()
        # 旧流终态 + 排程清理（模拟 _schedule_cleanup；不建 DB，直接构造等价状态）
        old = _SessionStream(sid, "old-trace")
        old.mark_terminal(old.publish("done", {"plan_id": 1}))
        _STREAMS[sid] = old
        handle = loop.call_later(_RETENTION_SECONDS, _STREAMS.pop, sid, None)
        old._cleanup_timer = handle

        # 窗口内重新占位（重试/重生成）：须取消旧定时器。
        # _occupy_generation 需要 db；用 monkeypatch 桩掉 _set_state，传 None db。
        monkeypatch.setattr(plans_mod, "_set_state", lambda *a, **k: None)
        new_stream = plans_mod._occupy_generation(None, sid, "raw")

        assert _STREAMS[sid] is new_stream
        assert old._cleanup_timer is None  # 句柄已取消并清空
        assert handle.cancelled()  # 旧定时器确已取消

        # 到点后旧 pop 不应触发（已取消）；手动推进事件循环验证新流仍在
        await asyncio.sleep(0)
        assert _STREAMS.get(sid) is new_stream
        _STREAMS.pop(sid, None)  # 清理测试现场

    asyncio.run(scenario())
