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

import hashlib
import hmac
import json
import logging
from typing import Any, Callable

log = logging.getLogger("deep_tutor.plugins.webhooks")

_DELIVER_TIMEOUT = 10.0


async def _deliver(url: str, body: bytes, headers: dict[str, str], *, timeout: float = _DELIVER_TIMEOUT) -> None:
    """POST body 到 url（httpx + 超时 + 错误隔离）。"""
    import httpx

    try:
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
