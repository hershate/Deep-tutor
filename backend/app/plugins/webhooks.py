"""插件 Webhook 投递（后端插件 API v1.15.0 扩展点）。

api.webhooks.register(url, events, secret) 把指定事件经 bus 协程监听器非阻塞投递到外部 URL——
一行调用即完成"事件 → Slack/Discord/自动化 Webhook"推送（标准载荷 + 可选 HMAC 签名 + 超时 + 错误隔离），
替代插件自行 events.on + httpx + 签名 + 错误处理的 ~20 行模板。

设计要点：
- bus.on 注册协程监听器；bus._dispatch 自动 create_task（非阻塞 + _guard_await 错误隔离）。
- bus.reset（reset_plugins）清除全部监听器——webhook 监听器随之清理，无需单独 reset。
- _deliver 用 httpx.AsyncClient（10s 超时）；HTTP >=400 记 warning；异常记 exception 不外溢。
- HMAC-SHA256 签名（secret 非空时）→ X-Webhook-Signature 头，外部服务可验真。
- v1：events 须指定列表（不支持通配符）；后续轮可加 subscribe_all 全事件。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
from typing import Any, Callable

log = logging.getLogger("deep_tutor.plugins.webhooks")

_DELIVER_TIMEOUT = 10.0
# iter 365 C5：投递 task 须持强引用——loop.create_task 仅弱引用，GC 可 mid-execution 回收
# 致 webhook 静默未投递（业务监听器数据丢失级）。done 回调释放引用。
_bg_tasks: set[asyncio.Task] = set()
# iter 367（批次四）：同步线程池兜底投递的进程级线程上限——sink 在无运行循环的线程被
# 调用时不得阻塞当前线程（_deliver 含 10s 超时，N 慢端点 × 并发事件即可抽干 anyio
# 线程池默认 40 线程，饿死后续同步端点含登录）。超额非阻塞丢弃 + 告警（best-effort
# 背压；出站并发另受 _deliver 内 webhook_slot 全局约束）。
_SINK_THREADS_MAX = 8
_sink_sem = threading.Semaphore(_SINK_THREADS_MAX)


async def _deliver(url: str, body: bytes, headers: dict[str, str], *, timeout: float = _DELIVER_TIMEOUT) -> None:
    """POST body 到 url（httpx + 超时 + 错误隔离）。"""
    import httpx
    from app.core.concurrency import webhook_slot

    try:
        # iter 366：全局出站并发背压——100 用户同时触发同一 webhook 时，无界 httpx 出站连接
        # 可打满文件描述符/内存；超额在此排队（优雅降级，不丢投递）。
        async with webhook_slot():
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
                if resp.status_code >= 400:
                    log.warning("webhook %s 返回 HTTP %d", url, resp.status_code)
    except Exception:  # noqa: BLE001  投递失败隔离：记日志不外溢（bus _guard_await 已兜底）
        log.exception("webhook 投递失败: %s", url)


def make_listener(url: str, event_name: str, secret: str = "") -> Callable:
    """构造一个协程监听器（bus.on 注册用）：收到 payload → 构造标准载荷 + HMAC 签名 → _deliver。"""

    async def listener(payload: dict[str, Any]) -> None:
        body_dict = {"event": event_name, "data": payload}
        body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = sig
        await _deliver(url, body, headers)

    return listener


async def _deliver_event(url: str, event: str, payload: dict[str, Any], secret: str) -> None:
    """全事件投递协程（subscribe_all sink 用——event 名在运行时得知，非注册时已知）。"""
    body = json.dumps({"event": event, "data": payload}, ensure_ascii=False).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig
    await _deliver(url, body, headers)


def make_all_events_sink(url: str, secret: str = "") -> Callable[[str, dict[str, Any]], None]:
    """构造 subscribe_all sync sink：收到任意事件 → 非阻塞投递（loop 分支 create_task /
    无循环分支有界 daemon 线程）。

    与 make_listener 区别：bus.subscribe_all 的 sink 是 sync（接收 event+payload），
    事件名运行时得知。loop 分支同 bus._dispatch 协程监听器语义（create_task + 强引用）；
    无循环分支（同步端点线程池）iter 367 起不阻塞当前线程（见 _SINK_THREADS_MAX 注释）。
    """

    def _run_in_thread(event: str, payload: dict[str, Any], sem: threading.Semaphore) -> None:
        try:
            asyncio.run(_deliver_event(url, event, payload, secret))
        finally:
            sem.release()

    def sink(event: str, payload: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_deliver_event(url, event, payload, secret))
            _bg_tasks.add(task)  # 持强引用防 GC 中途回收（iter 365 C5）
            task.add_done_callback(_bg_tasks.discard)
        except RuntimeError:
            # 同步线程池无运行循环 -> 有界 daemon 线程执行，不阻塞当前线程（iter 367）；
            # 原地 asyncio.run 会占住线程池 worker 长达 _DELIVER_TIMEOUT
            if not _sink_sem.acquire(blocking=False):
                log.warning("webhook sink 线程达上限 %d，丢弃事件 %s -> %s", _SINK_THREADS_MAX, event, url)
                return
            threading.Thread(
                target=_run_in_thread, args=(event, payload, _sink_sem), daemon=True
            ).start()

    return sink
