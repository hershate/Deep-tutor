"""实时事件流 API（后端插件 API v1.5.0 / 服务器推送扩展点）。

GET /api/events/stream  Server-Sent Events：把后端事件总线（app/plugins/bus.py）的
事件实时推送给已认证客户端。前端插件/外部替代前端据此构建实时 UI（学习进度更新、
方案生成完成、测验提交等），无需轮询——"用 API 完全替换前端"的实时性使能项。

设计要点：
- 鉴权：与宿主端点同一机制（JWT / AUTH_TOKEN / 开放本机模式）；SSE 经 fetch+
  ReadableStream 携带 Authorization 头（EventSource 不能带自定义头，前端用 sse.ts）。
- 用户隔离（P4 安全默认）：事件按归属用户过滤——payload 带 user_id 直接归属；
  带 session_id 经 DB 解析 session.user_id；既无 user_id 又非全局事件 -> 丢弃
  （不向他人泄露）。仅 startup/shutdown 等全局生命周期事件广播给全部连接。
- 心跳：每 15s 发一行 SSE 注释（: ping\\n\\n）防代理/浏览器缓冲挂起连接
  （同 plans.py 生成 SSE 的看门狗口径，iter 244 F9）。
- 清理：客户端断开 -> 生成器 finally 退订 sink，无连接/监听泄漏。

契约见 API.md「§事件流」。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.db.models import User
from app.plugins import bus

log = logging.getLogger("deep_tutor.api.events")

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_auth)])

# 全局生命周期事件（无用户归属，广播给全部连接）。其余事件须可归属用户才转发——
# 不可归属的非全局事件一律丢弃（safe-by-default，防跨用户泄露插件自定义事件）
GLOBAL_EVENTS: frozenset[str] = frozenset({"startup", "shutdown"})

_HEARTBEAT_S = 15.0


def resolve_owner(payload: dict[str, Any], db: Session | None = None) -> int | None:
    """解析事件的归属用户 id（用于按用户过滤 SSE 流）。

    - payload 带 user_id（int）-> 直接归属；
    - 否则带 session_id（int）-> 经 DB 解析 session.user_id（事件为人频，O(1) 查询可接受）；
    - 都无 -> None（调用方按 GLOBAL_EVENTS 决定广播或丢弃）。
    db 为 None 时开短生命周期会话查询（事件流路径用）。
    """
    uid = payload.get("user_id")
    if isinstance(uid, int) and uid > 0:
        return uid
    sid = payload.get("session_id")
    if isinstance(sid, int) and sid > 0:
        if db is None:
            from app.db.base import SessionLocal

            own = SessionLocal()
            try:
                return resolve_owner(payload, own)
            finally:
                own.close()
        from app.db.models import Session

        sess = db.get(Session, sid)
        return sess.user_id if sess is not None else None
    return None


def format_frame(event: str, payload: dict[str, Any], user_id: int, db: Session | None = None) -> str | None:
    """决定是否向 user_id 转发该事件，返回 SSE 帧 str（转发）或 None（过滤/丢弃）。

    pure 决策 + 归属解析：归属本用户 -> 转；归属他人 -> 丢；无归属且全局 -> 转；
    无归属且非全局 -> 丢（safe-by-default）。
    """
    owner = resolve_owner(payload, db)
    if owner is not None:
        if owner != user_id:
            return None  # P4 隔离：他人的事件不转发
    elif event not in GLOBAL_EVENTS:
        return None  # 不可归属的非全局事件：丢弃（防泄露）
    # iter 365 C9：event 名剥换行——插件自定义事件名含 \n 会注入额外 SSE 行（伪 data: 行）
    safe_event = (event or "").replace("\r", "").replace("\n", "")
    return f"event: {safe_event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def events_generator(
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    user_id: int,
    *,
    heartbeat_s: float = _HEARTBEAT_S,
) -> Any:
    """SSE 帧生成器：从 queue 取 (event, payload)，按用户过滤后 yield 帧；空闲发心跳。

    独立于 HTTP 层以便直接单元测试（避免无限流 + 线程的集成脆弱性）。客户端断开时
    调用方负责 queue 不再增长 + 取消本生成器；端点在 finally 退订 sink。
    """
    db = None
    try:
        from app.db.base import SessionLocal

        db = SessionLocal()  # 复用一个会话解析 session_id 归属（事件人频，单会话足够）
        while True:
            try:
                event, payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # SSE 注释行=心跳，不产生客户端事件，仅保活
                continue
            frame = format_frame(event, payload, user_id, db)
            if frame is not None:
                yield frame
    except asyncio.CancelledError:
        raise
    finally:
        if db is not None:
            db.close()


@router.get("/stream")
async def event_stream(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),  # noqa: ARG001  仅触发依赖鉴权；归属解析用独立会话
) -> StreamingResponse:
    """实时事件流（SSE）。连接期间转发该用户的事件（+ 全局生命周期事件）。

    客户端断开 -> 生成器被取消 -> finally 退订 sink（无监听泄漏）。
    """
    # iter 365 C8：有界队列防慢客户端 OOM——挂起/慢读的连接持续入队无界 Queue 会线性吃内存
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    def sink(event: str, payload: dict[str, Any]) -> None:
        # emit 可能来自同步线程池 -> call_soon_threadsafe 跨线程安全入队；
        # loop 已关（客户端断开后续迟到的 emit）-> 静默丢弃（不再有消费者）
        def _put(item):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # 慢客户端：丢事件防 OOM（心跳不受影响，客户端终会读或断开）

        try:
            loop.call_soon_threadsafe(_put, (event, dict(payload)))
        except RuntimeError:
            pass

    off = bus.subscribe_all(sink)

    async def stream():
        try:
            async for frame in events_generator(queue, user.id):
                yield frame
        finally:
            off()  # 退订 sink（客户端断开/取消时）

    return StreamingResponse(stream(), media_type="text/event-stream")
