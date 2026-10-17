"""iter 381 R24：更新面 × 服务端内存态一致性——事件流两缺口回归。

- 缺口 1（跨用户错发窗口）：resolve_owner 的 session_id->owner 60s TTL 缓存在
  「删会话后 SQLite 复用 rowid」场景把新会话事件按缓存的旧 owner 过滤——旧 owner
  收到他人 plan_created/materials_completed（跨用户信息泄露）且正主收不到（前端
  toast/实时刷新失效）。修复 = 移除缓存即时查库（events.py 注释有负载论证）。
  本测试端到端钉死：同 sid 换 owner 后归属即时翻转。
- 缺口 2（吊销后已建流永续）：GET /api/events/stream 只在建立时认证一次，SSE 靠
  15s 心跳保活无寿命——管理员删用户 / 改密（tokens_valid_after）后已建立的流
  继续接收该用户全部实时事件。修复 = events_generator 心跳周期重验身份
  （用户存在 + JWT 吊销水位，与 deps 的 JWT 业务校验同口径含 +1s 容差），
  失败即断流；客户端按既有退避重连 -> 重新认证 -> 401。
"""

from __future__ import annotations

import asyncio
import pytest

from app.db import models
from app.db.base import SessionLocal


def _mk_user(name: str) -> int:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


# --- 缺口 1：rowid 复用 × 归属即时性 ---


def test_sid_reuse_owner_flips_immediately():
    """删会话后新会话复用同 sid（SQLite rowid 复用）：事件归属即时跟随新 owner，
    旧 owner 不再收到（旧 TTL 缓存语义下此场景恰好反向——正主丢失+旧主错收）。"""
    from app.api.events import format_frame

    db = SessionLocal()
    try:
        uid_a = _mk_user("ev381_a")
        uid_b = _mk_user("ev381_b")
        sess_a = models.Session(user_id=uid_a, title="a")
        db.add(sess_a)
        db.commit()
        db.refresh(sess_a)
        sid = sess_a.id
        db.delete(sess_a)
        db.commit()
        sess_b = models.Session(user_id=uid_b, title="b")
        db.add(sess_b)
        db.commit()
        db.refresh(sess_b)
        reused = sess_b.id == sid
    finally:
        db.close()
    if not reused:  # SQLite 复用最新空位 rowid；防御性跳过（引擎行为变化时显式暴露）
        import pytest

        pytest.skip("rowid 未复用（引擎行为变化，窗口场景不成立）")

    payload = {"session_id": sid, "plan_id": 1, "topic": "secret-topic"}
    # 正主（新 owner）收到；旧 owner 不再收到（含 topic 的载荷不泄露）
    assert format_frame("plan_created", payload, uid_b) is not None
    assert format_frame("plan_created", payload, uid_a) is None


# --- 缺口 2：心跳周期身份重验（单事件循环内跑完场景，Queue 绑定首个循环不可跨 run） ---


def test_reauth_valid_user_heartbeats():
    """用户存在且未吊销 -> 心跳持续（重验不误伤正常连接）。"""
    from app.api.events import events_generator

    uid = _mk_user("ev381_alive")

    async def scenario():
        gen = events_generator(asyncio.Queue(), user_id=uid, heartbeat_s=0.02, token_iat=1000)
        assert await gen.__anext__() == ": ping\n\n"
        assert await gen.__anext__() == ": ping\n\n"
        await gen.aclose()

    asyncio.run(scenario())


def test_reauth_deleted_user_stream_closes():
    """用户被删 -> 下个心跳周期断流（不再永续接收事件）。"""
    from app.api.events import events_generator

    uid = _mk_user("ev381_gone")
    db = SessionLocal()
    try:
        db.delete(db.get(models.User, uid))
        db.commit()
    finally:
        db.close()

    async def scenario():
        gen = events_generator(asyncio.Queue(), user_id=uid, heartbeat_s=0.02)
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(scenario())


def test_reauth_revoked_jwt_stream_closes():
    """改密吊销（tokens_valid_after 越过 token 签发时刻 iat）-> 断流；同口径
    +1s 容差：水位恰为 iat+1（deps 同款语义）也吊销。"""
    from app.api.events import events_generator

    uid = _mk_user("ev381_revoked")
    db = SessionLocal()
    try:
        db.get(models.User, uid).tokens_valid_after = 2_000_000_000.0  # epoch 秒，远越 iat=1000
        db.commit()
    finally:
        db.close()

    async def scenario(iat: int):
        gen = events_generator(asyncio.Queue(), user_id=uid, heartbeat_s=0.02, token_iat=iat)
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(scenario(1000))
    # 容差边界：水位恰为 iat+1（deps 的 +1s 语义）也吊销
    db = SessionLocal()
    try:
        db.get(models.User, uid).tokens_valid_after = 1001.0
        db.commit()
    finally:
        db.close()
    asyncio.run(scenario(1000))


def test_reauth_pat_mode_only_checks_existence():
    """非 JWT 凭证（token_iat=None）：只验用户存在性——吊销水位不参与（PAT 的
    失效面是删行/删用户，由存在性覆盖）。"""
    from app.api.events import events_generator

    uid = _mk_user("ev381_pat")
    db = SessionLocal()
    try:
        db.get(models.User, uid).tokens_valid_after = 2_000_000_000.0
        db.commit()
    finally:
        db.close()

    async def scenario():
        gen = events_generator(asyncio.Queue(), user_id=uid, heartbeat_s=0.02, token_iat=None)
        assert await gen.__anext__() == ": ping\n\n"  # 有水位但无 iat -> 不断流
        await gen.aclose()

    asyncio.run(scenario())
