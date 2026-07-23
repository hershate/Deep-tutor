"""后端插件 API v1.15.0 api.webhooks 测试（API.md「§Webhook 投递」）。

monkeypatch _deliver 捕获投递参数（避免真实 HTTP），验证：事件触发→投递、HMAC 签名、
多事件注册、unregister 停止、入参校验、错误隔离。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest

from app.plugins import bus
from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


def _facade() -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id="whtest", name="whtest", version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id="whtest", data_dir="/tmp", status=status)


def _patch_deliver(monkeypatch, calls: list):
    """替换 webhooks._deliver 为记录调用的 mock。"""

    async def _mock(url, body, headers, *, timeout=10.0):
        calls.append({"url": url, "body": json.loads(body), "headers": headers})

    monkeypatch.setattr("app.plugins.webhooks._deliver", _mock)


# --- 基本 ---


def test_event_triggers_delivery(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    api.webhooks.register("https://hooks.slack.com/x", events=["plan_created"])

    async def main():
        bus.emit("plan_created", {"topic": "React", "plan_id": 1})
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert len(calls) == 1
    assert calls[0]["url"] == "https://hooks.slack.com/x"
    assert calls[0]["body"]["event"] == "plan_created"
    assert calls[0]["body"]["data"]["topic"] == "React"


def test_unmatched_event_not_delivered(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    api.webhooks.register("https://hooks.example.com", events=["plan_created"])

    async def main():
        bus.emit("progress_updated", {"plan_id": 1})
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert len(calls) == 0  # 仅 plan_created 注册，progress_updated 不触发


# --- HMAC 签名 ---


def test_hmac_signature(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    secret = "my-secret-key"
    api.webhooks.register("https://hooks.example.com", events=["quiz_submitted"], secret=secret)

    async def main():
        bus.emit("quiz_submitted", {"quiz_id": 1, "correct": True})
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert len(calls) == 1
    sig_header = calls[0]["headers"].get("X-Webhook-Signature", "")
    assert sig_header  # 有签名
    # 验证签名正确性
    body_bytes = json.dumps({"event": "quiz_submitted", "data": {"quiz_id": 1, "correct": True}}, ensure_ascii=False).encode("utf-8")
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    assert sig_header == expected


def test_no_secret_no_signature(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    api.webhooks.register("https://hooks.example.com", events=["plan_created"])

    async def main():
        bus.emit("plan_created", {"topic": "x"})
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert "X-Webhook-Signature" not in calls[0]["headers"]


# --- 多事件 + unregister ---


def test_multiple_events(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    api.webhooks.register("https://hooks.example.com", events=["plan_created", "progress_updated"])

    async def main():
        bus.emit("plan_created", {"topic": "a"})
        bus.emit("progress_updated", {"plan_id": 1})
        bus.emit("quiz_submitted", {})  # 未注册 -> 不投递
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert len(calls) == 2


def test_unregister_stops_delivery(monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    api = _facade()
    handle = api.webhooks.register("https://hooks.example.com", events=["plan_created"])

    async def main():
        bus.emit("plan_created", {"topic": "first"})
        await asyncio.sleep(0.1)
        handle.unregister()
        bus.emit("plan_created", {"topic": "second"})
        await asyncio.sleep(0.1)

    asyncio.run(main())
    assert len(calls) == 1  # 仅第一次投递


# --- 入参校验 ---


def test_register_validation():
    api = _facade()
    with pytest.raises(ValueError, match="url"):
        api.webhooks.register("ftp://bad", events=["x"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="events"):
        api.webhooks.register("https://x", events=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="events"):
        api.webhooks.register("https://x", events=[""])  # type: ignore[arg-type]


# --- 状态可观测 ---


def test_status_records_webhook():
    from fastapi import FastAPI

    st = PluginStatus(id="whtest2", name="whtest2", version="", file="", state="loaded")
    api = BackendPluginAPI(app=FastAPI(), plugin_id="whtest2", data_dir="/tmp", status=st)
    api.webhooks.register("https://hooks.example.com", events=["plan_created"])
    assert "https://hooks.example.com" in st.webhooks
