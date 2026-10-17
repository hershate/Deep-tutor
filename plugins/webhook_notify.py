"""Webhook 通知（后端插件，复合 webhooks+routes+storage）。

把学习事件（方案生成 / 打卡 / 测验）经 api.webhooks 推送到外部 URL（Slack / Discord / 自动化）。
Webhook 配置经 POST /config 动态设置并持久化（跨重启自动恢复），无需改代码或重启。

实际用途：完成学习方案、每日打卡、测验提交时自动通知到 Slack/Discord/n8n 等外部平台。

复合 API（验证扩展点协同）：
- api.webhooks：注册事件→URL 投递（HMAC 签名 + 非阻塞 + 错误隔离，一行替代 events.on+httpx）
- api.routes：POST /config（管理员设置/更新）、GET /config（查看）、DELETE /config（停用）
- api.storage：持久化 webhook 配置（url/events/secret，跨重启）

稳定性：_apply 幂等（更新前先 unregister 旧 handle 再注册新）；配置缺失不注册；secret 不回显。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

PLUGIN_META = {
    "id": "webhook_notify",
    "name": "Webhook 通知",
    "version": "0.1.0",
    "description": "学习事件推送到外部 URL（webhooks+routes+storage）",
    "min_api": "1.15",  # webhooks(1.15) + routes(1.0) + storage(1.11)
}


class WebhookConfig(BaseModel):
    url: str
    events: list[str]
    secret: str = ""


def register(api):
    state: dict = {"handle": None}

    def _apply() -> None:
        """据 storage 当前配置（重新）注册 webhook：先 unregister 旧 handle，再注册新（幂等）。"""
        if state["handle"] is not None:
            state["handle"].unregister()
            state["handle"] = None
        cfg = api.storage.get("webhook_config")
        if isinstance(cfg, dict) and cfg.get("url"):
            state["handle"] = api.webhooks.register(
                cfg["url"], cfg.get("events") or None, secret=cfg.get("secret", "")
            )

    _apply()  # 启动恢复（真实启动时从持久化配置重建 webhook 订阅）

    router = APIRouter()

    @router.post("/config")
    def set_config(body: WebhookConfig, user=Depends(api.deps.require_admin)):
        api.storage.set(
            "webhook_config", {"url": body.url, "events": list(body.events), "secret": body.secret}
        )
        _apply()
        return {"ok": True, "url": body.url, "events": list(body.events), "has_secret": bool(body.secret)}

    @router.get("/config")
    def get_config(user=Depends(api.deps.require_admin)):
        # iter 368：查看与设置/删除同门禁——webhook 目标 URL 属集成配置（可能含内网
        # 地址或 URL 内嵌凭证），此前任意登录用户可读
        cfg = api.storage.get("webhook_config")
        if not isinstance(cfg, dict) or not cfg.get("url"):
            return {"configured": False}
        return {
            "configured": True,
            "url": cfg["url"],
            "events": cfg.get("events", []),
            "has_secret": bool(cfg.get("secret")),
        }

    @router.delete("/config")
    def del_config(user=Depends(api.deps.require_admin)):
        api.storage.delete("webhook_config")
        if state["handle"] is not None:
            state["handle"].unregister()
            state["handle"] = None
        return {"ok": True}

    api.routes.mount(router)
    api.log.info("webhook_notify 已加载：学习事件 Webhook 通知")
