"""_SessionStream 事件缓冲 + _relay 续接单元测试（plan/10 §十二 断线续接游标）。

不联网、不经 HTTP；直接测缓冲语义与续接重放。异步场景用 asyncio.run 驱动（无 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio

from app.api.plans import _MAX_BUFFERED_EVENTS, _SessionStream, _relay


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
