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
import threading
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import get_current_user_id, require_auth
from app.api.errors import ApiError
from app.api.sse_utils import SSE_HEADERS
from app.core.security import decode_token
from app.plugins import bus

log = logging.getLogger("deep_tutor.api.events")

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_auth)])

# 全局生命周期事件（无用户归属，广播给全部连接）。其余事件须可归属用户才转发——
# 不可归属的非全局事件一律丢弃（safe-by-default，防跨用户泄露插件自定义事件）
GLOBAL_EVENTS: frozenset[str] = frozenset({"startup", "shutdown"})

_HEARTBEAT_S = 15.0

# iter 395：并发事件流连接上限（单用户 + 全局）——此前**无上限**：每条连接持有一个
# 100 槽队列 + 一个 bus sink，单客户端可开任意条（内存线性增长）；且每次 emit 需
# 遍历全部 sink（O(连接数)）-> 事件风暴下 CPU 放大。对照 plans/memory 生成流均有
# 全局槽位上限。实测前端每应用实例仅 1 条（EventsBridge），多标签页也就数条——
# 故阈值取宽（单用户 16 / 全局 256），只为挡住「开满 thousands 条」这类滥用。
_MAX_STREAMS_PER_USER = 16
_MAX_STREAMS_TOTAL = 256
_streams_lock = threading.Lock()
_streams_by_user: dict[int, int] = {}
_streams_total = 0


def _acquire_stream_slot(user_id: int) -> None:
    """占用一条事件流槽位；超限抛 429（防单客户端开满连接耗尽内存/事件分发）。"""
    global _streams_total
    with _streams_lock:
        per_user = _streams_by_user.get(user_id, 0)
        if per_user >= _MAX_STREAMS_PER_USER or _streams_total >= _MAX_STREAMS_TOTAL:
            raise ApiError(
                "rate_limited",
                f"事件流连接数达上限（单用户 {_MAX_STREAMS_PER_USER} 条），请关闭多余连接后重试",
                status=429,
            )
        _streams_by_user[user_id] = per_user + 1
        _streams_total += 1


def _release_stream_slot(user_id: int) -> None:
    """释放事件流槽位（连接断开时；计数器只减不增，异常路径也不会为负）。"""
    global _streams_total
    with _streams_lock:
        n = _streams_by_user.get(user_id, 0)
        if n <= 1:
            _streams_by_user.pop(user_id, None)
        else:
            _streams_by_user[user_id] = n - 1
        _streams_total = max(0, _streams_total - 1)

# iter 381：移除 session_id -> owner 的 60s TTL 缓存（iter 367 曾为「每事件×每订阅者
# 查库」加的收敛层）。两个理由：
# 1. 负载前提已不成立——现事件面仅 plan_created / materials_completed 不带 user_id
#    （每方案生成 2 次走 session_id 解析），其余核心事件全部直归属（零查询）；
#    直查 = 每订阅者 × 每方案 2 次主键读，可忽略。
# 2. TTL 缓存有跨用户错发窗口——删会话后 SQLite 复用 rowid，60s 内新会话（可能属
#    其他用户）的事件按缓存的旧 owner 过滤：旧 owner 收到他人 plan_created/
#    materials_completed（含 topic/单元数 = 跨用户信息泄露）且正主收不到自己的
#    事件（前端 toast/实时刷新失效）。即时查库即真实归属，窗口彻底关死。


def _session_owner(db: Session, sid: int) -> int | None:
    from app.db.models import Session as SessionModel

    sess = db.get(SessionModel, sid)
    return sess.user_id if sess is not None else None


def resolve_owner(payload: dict[str, Any], db: Session | None = None) -> int | None:
    """解析事件的归属用户 id（用于按用户过滤 SSE 流）。

    - payload 带 user_id（int）-> 直接归属；
    - 否则带 session_id（int）-> 经 DB 即时解析 session.user_id（iter 381 起直查，
      见上方注释的窗口论证）；
    - 都无 -> None（调用方按 GLOBAL_EVENTS 决定广播或丢弃）。
    db 为 None 时开短生命周期会话查询（事件流路径用）。

    iter 395：``bool`` 不算 int（``isinstance(True, int)`` 为真 -> ``user_id=True``
    会被判给 user 1，白拿他人事件）。
    """
    uid = payload.get("user_id")
    if type(uid) is int and uid > 0:
        return uid
    sid = payload.get("session_id")
    if type(sid) is int and sid > 0:
        own: Session | None = None
        if db is None:
            from app.db.base import SessionLocal

            own = SessionLocal()
            query_db: Session = own
        else:
            query_db = db
        try:
            return _session_owner(query_db, sid)
        finally:
            if own is not None:
                own.close()
    return None


def format_frame(
    event: str,
    payload: dict[str, Any],
    user_id: int,
    db: Session | None = None,
    owner: int | None = None,
) -> str | None:
    """决定是否向 user_id 转发该事件，返回 SSE 帧 str（转发）或 None（过滤/丢弃）。

    pure 决策 + 归属解析：归属本用户 -> 转；归属他人 -> 丢；无归属且全局 -> 转；
    无归属且非全局 -> 丢（safe-by-default）。

    owner：已解析的归属（iter 395：事件流在**线程池**里解析归属后传入，避免同步
    DB 读占住事件循环）；None -> 本函数内解析（同步调用方/单测口径）。

    iter 395：payload 不可 JSON 序列化时（插件塞 datetime/bytes/自定义对象）不再
    让整条流崩断——降级为 ``default=str`` 序列化；仍失败则丢帧并记日志。
    """
    if owner is None:
        owner = resolve_owner(payload, db)
    if owner is not None:
        if owner != user_id:
            return None  # P4 隔离：他人的事件不转发
    elif event not in GLOBAL_EVENTS:
        return None  # 不可归属的非全局事件：丢弃（防泄露）
    # iter 365 C9：event 名剥换行——插件自定义事件名含 \n 会注入额外 SSE 行（伪 data: 行）
    safe_event = (event or "").replace("\r", "").replace("\n", "")
    try:
        data = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # noqa: PERF203  极端 payload（循环引用等）
        log.warning("事件 payload 不可序列化，丢弃该帧: %s", safe_event)
        return None
    return f"event: {safe_event}\ndata: {data}\n\n"


async def _resolve_owner_async(payload: dict[str, Any]) -> int | None:
    """归属解析的**非阻塞**版（iter 395）。

    事件流是 async 生成器：同步 DB 读（SessionLocal + db.get）会占住事件循环
    线程——连接池耗尽时 ``pool.connect()`` 最多阻塞 30s -> 全站冻结（与 iter
    364/393 记录的写路径同型失效，但发生在读路径）。故带 session_id 的事件把
    查库放进线程池；带 user_id 的事件零 DB 零开销。
    """
    uid = payload.get("user_id")
    if type(uid) is int and uid > 0:
        return uid
    sid = payload.get("session_id")
    if type(sid) is int and sid > 0:
        return await asyncio.to_thread(resolve_owner, {"session_id": sid})
    return None


def _user_still_valid(user_id: int, token_iat: int | None) -> bool:
    """iter 381：SSE 流身份重验——用户仍存在 + JWT 未被吊销水位越过（与 deps 的
    JWT 业务校验同口径，含 +1s iat 容差）。PAT/开放模式（token_iat=None）只验用户
    存在性。短会话主键读，每心跳周期（15s）一次，微秒级。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if u is None:
            return False
        if token_iat is not None and token_iat + 1 <= (u.tokens_valid_after or 0):
            return False
        return True
    finally:
        db.close()


async def events_generator(
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    user_id: int,
    *,
    heartbeat_s: float = _HEARTBEAT_S,
    token_iat: int | None = None,
    reauth: bool = True,
) -> Any:
    """SSE 帧生成器：从 queue 取 (event, payload)，按用户过滤后 yield 帧；空闲发心跳。

    独立于 HTTP 层以便直接单元测试（避免无限流 + 线程的集成脆弱性）。客户端断开时
    调用方负责 queue 不再增长 + 取消本生成器；端点在 finally 退订 sink。

    iter 366：不再持有常驻 DB 会话——归属解析（format_frame -> resolve_owner）仅在
    payload 带 session_id 时开短生命周期会话（多数事件带 user_id 直归属，零 DB），
    避免 100 用户各一条常驻 EventsBridge SSE 钉死 100+ 连接池槽位。
    iter 381：心跳周期重验身份（reauth）——SSE 流只在建立时认证一次，此前吊销后
    （管理员删用户 / 改密更新 tokens_valid_after）已建立的流靠心跳保活**永续**接收
    该用户全部实时事件。重验失败即 return 断流（客户端按既有退避重连，重新走完整
    认证链 -> 401）。单测用 reauth=False 跳过（无真实用户行）。

    iter 395 两处修正：
    1. **重验不再依赖队列空闲**：此前只在 ``queue.get()`` 超时（15s 无事件）分支
       重验；事件持续不断时（如生成中 progress_updated 连发）永远走不到该分支 ->
       已吊销用户的流持续收事件。重验改为**按单调时钟的截止时间**，与队列是否空闲
       无关。
    2. **同步 DB 读移出事件循环**：归属解析（带 session_id 的事件）与身份重验的
       查库均放线程池（``asyncio.to_thread``），避免连接池耗尽时冻结全站。
    """
    import time

    next_reauth = time.monotonic() + heartbeat_s
    while True:
        item: tuple[str, dict[str, Any]] | None = None
        try:
            item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
        except asyncio.TimeoutError:
            item = None
        now = time.monotonic()
        if reauth and now >= next_reauth:
            next_reauth = now + heartbeat_s
            if not await asyncio.to_thread(_user_still_valid, user_id, token_iat):
                return
        if item is None:
            yield ": ping\n\n"  # SSE 注释行=心跳，不产生客户端事件，仅保活
            continue
        event, payload = item
        owner = await _resolve_owner_async(payload)
        frame = format_frame(event, payload, user_id, owner=owner)
        if frame is not None:
            yield frame


@router.get("/stream")
async def event_stream(
    request: Request, user_id: int = Depends(get_current_user_id)
) -> StreamingResponse:
    """实时事件流（SSE）。连接期间转发该用户的事件（+ 全局生命周期事件）。

    客户端断开 -> 生成器被取消 -> finally 退订 sink（无监听泄漏）。
    iter 366：用 get_current_user_id（解析后即释放会话），流期不钉连接池槽位。
    iter 381：从 Bearer JWT 提取 iat 随流传入——心跳周期重验吊销水位（见
    events_generator docstring）；非 JWT 凭证（AUTH_TOKEN/PAT/开放模式）iat 为
    None，只重验用户存在性。直调单测可传 headers 为空 dict 的 stub。
    """
    token_iat: int | None = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        try:
            claims = decode_token(auth[7:].strip())
            iat = claims.get("iat")
            token_iat = int(iat) if isinstance(iat, (int, float)) else None
        except Exception:  # noqa: BLE001  非 JWT 的 Bearer（PAT 等）或解码失败 -> 只验存在性
            token_iat = None
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

    # iter 395：先占槽（超限直接 429，不入总线），再订阅；订阅异常则归还槽位。
    _acquire_stream_slot(user_id)
    try:
        off = bus.subscribe_all(sink)
    except Exception:
        _release_stream_slot(user_id)
        raise

    released = False

    def _cleanup() -> None:
        """幂等清理：退订 sink + 归还槽位（多次调用安全）。"""
        nonlocal released
        off()
        if not released:
            released = True
            _release_stream_slot(user_id)

    async def stream():
        try:
            async for frame in events_generator(queue, user_id, token_iat=token_iat):
                yield frame
        finally:
            _cleanup()

    # iter 395：BackgroundTask 兜底——客户端在**响应体开始前**断开时
    # body_iterator 从未被迭代，未启动的 async generator 的 finally 不会执行
    # （Python 语义）-> 槽位与 bus sink 永久泄漏（单用户可把全站 SSE 打满）。
    # Starlette 在响应结束后必跑 background（含提前断开路径），故清理不能只挂在
    # 生成器的 finally 上；_cleanup 幂等，两处都调用安全。
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
        background=BackgroundTask(_cleanup),
    )

    # iter 367（批次四）：补 SSE_HEADERS（no-cache/keep-alive/禁 nginx 缓冲）——其余
    # SSE 端点（plans/memory）均已带，漏在此处时反代环境下事件流会被缓冲至超时
    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
