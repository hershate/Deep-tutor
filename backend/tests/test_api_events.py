"""实时事件流 API 测试（后端插件 API v1.5.0，API.md「§事件流」）。

覆盖：
- bus.subscribe_all：接收全部事件 / sink 异常隔离 / off() / reset()
- resolve_owner：user_id 直归属 / session_id→DB 解析 / 都无 None
- format_frame：本用户转 / 他人丢 / session 归属匹配 / 全局广播 / 不可归属丢弃
- events_generator：队列帧转发 + 过滤 + 心跳（空队列短超时）
- 端点鉴权 401（require_auth 在流式前生效）
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.base import SessionLocal


# --- bus.subscribe_all ---


def test_subscribe_all_receives_every_event():
    from app.plugins import bus

    seen: list[tuple[str, dict]] = []
    off = bus.subscribe_all(lambda e, p: seen.append((e, dict(p))))
    bus.emit("plan_created", {"plan_id": 1, "session_id": 2})
    bus.emit("progress_updated", {"user_id": 5})
    off()
    bus.emit("quiz_submitted", {"user_id": 5})  # 退订后不再收
    assert seen == [
        ("plan_created", {"plan_id": 1, "session_id": 2}),
        ("progress_updated", {"user_id": 5}),
    ]


def test_subscribe_all_sink_exception_isolated():
    """sink 抛异常不影响其余 sink 与 emit 调用方（与 listener 同一隔离语义）。"""
    from app.plugins import bus

    ok: list[str] = []

    def boom(e, p):
        raise RuntimeError("sink crash")

    off1 = bus.subscribe_all(boom)
    off2 = bus.subscribe_all(lambda e, p: ok.append(e))
    bus.emit("plan_created", {"user_id": 1})  # 不向调用方抛
    off1()
    off2()
    assert ok == ["plan_created"]  # 炸 sink 不影响好 sink


def test_subscribe_all_reset_clears_sinks():
    from app.plugins import bus

    seen: list[str] = []
    bus.subscribe_all(lambda e, p: seen.append(e))
    bus.reset()
    bus.emit("startup", {})
    assert seen == []


def test_subscribe_all_rejects_non_callable():
    from app.plugins import bus

    with pytest.raises(TypeError):
        bus.subscribe_all("not callable")  # type: ignore[arg-type]


# --- resolve_owner ---


def test_resolve_owner_user_id_direct():
    from app.api.events import resolve_owner

    assert resolve_owner({"user_id": 7}) == 7
    assert resolve_owner({"user_id": "7"}) is None  # 非 int 不认
    assert resolve_owner({}) is None


def test_resolve_owner_session_id_resolves_via_db():
    """session_id -> DB 解析 session.user_id（plan_created/materials_completed 走此路）。"""
    from app.api.events import resolve_owner
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="o", username="o", password_hash="", is_admin=False)
        db.add(u)
        db.flush()
        sess = models.Session(user_id=u.id, title="t")
        db.add(sess)
        db.flush()
        sess_id, uid = sess.id, u.id
        db.commit()
    finally:
        db.close()
    # 传 db（复用会话）与不传 db（自开短会话）都解析正确
    assert resolve_owner({"session_id": sess_id}, db=None) == uid


# --- format_frame（过滤决策）---


def _seed_session(owner_admin: bool = True) -> tuple[int, int]:
    """落一用户+一会话，返回 (user_id, session_id)。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="x", username="x", password_hash="", is_admin=owner_admin)
        db.add(u)
        db.flush()
        sess = models.Session(user_id=u.id, title="t")
        db.add(sess)
        db.flush()
        uid, sid = u.id, sess.id
        db.commit()
        return uid, sid
    finally:
        db.close()


def test_format_frame_own_user_event_forwarded():
    from app.api.events import format_frame

    frame = format_frame("progress_updated", {"user_id": 9, "plan_id": 1}, user_id=9)
    assert frame is not None
    assert frame.startswith("event: progress_updated\n")
    assert '"user_id": 9' in frame


def test_format_frame_other_user_event_dropped():
    from app.api.events import format_frame

    assert format_frame("progress_updated", {"user_id": 8}, user_id=9) is None


def test_format_frame_session_scoped_filters_by_owner():
    from app.api.events import format_frame

    uid, sid = _seed_session()
    db = SessionLocal()
    try:
        # 归属本用户 -> 转
        assert format_frame("plan_created", {"session_id": sid}, user_id=uid, db=db) is not None
        # 归属他人 -> 丢
        assert format_frame("plan_created", {"session_id": sid}, user_id=uid + 999, db=db) is None
    finally:
        db.close()


def test_format_frame_global_event_forwarded_to_all():
    from app.api.events import format_frame

    # startup/shutdown 无用户归属 -> 广播给所有人
    assert format_frame("startup", {}, user_id=1) is not None
    assert format_frame("startup", {}, user_id=999) is not None


def test_format_frame_unscoppable_nonglobal_dropped():
    """无 user_id/session_id 且非全局事件 -> 丢弃（防插件自定义事件跨用户泄露）。"""
    from app.api.events import format_frame

    assert format_frame("myplugin.ready", {"foo": "bar"}, user_id=1) is None


# --- events_generator ---


def test_events_generator_yields_own_event_and_filters_others():
    from app.api.events import events_generator

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(("progress_updated", {"user_id": 3, "plan_id": 1}))
    queue.put_nowait(("progress_updated", {"user_id": 99, "plan_id": 2}))  # 他人 -> 过滤
    queue.put_nowait(("startup", {}))  # 全局 -> 转
    gen = events_generator(queue, user_id=3, heartbeat_s=10)
    f1 = asyncio.run(gen.__anext__())
    f2 = asyncio.run(gen.__anext__())
    assert "user_id\": 3" in f1 and "progress_updated" in f1
    assert "startup" in f2  # 99 被过滤，下一帧是 startup
    asyncio.run(gen.aclose())


def test_events_generator_heartbeat_on_idle():
    """空队列超时 -> 发心跳注释行（不产生客户端事件，仅保活）。"""
    from app.api.events import events_generator

    queue: asyncio.Queue = asyncio.Queue()
    gen = events_generator(queue, user_id=1, heartbeat_s=0.05)
    frame = asyncio.run(gen.__anext__())
    assert frame == ": ping\n\n"
    asyncio.run(gen.aclose())


# --- 端点鉴权 ---


def test_event_stream_requires_auth(client):
    """require_auth 在 StreamingResponse 创建前生效 -> 未认证 401（不进入无限流）。"""
    r = client.get("/api/events/stream")
    assert r.status_code == 401
