"""webhook_notify 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/webhook_notify.py 真实源码，验证 webhooks+routes+storage 协同：
- webhooks：POST /config 动态注册事件→URL 投递；事件触发推送（mock _deliver 捕获）
- routes：POST/GET/DELETE /config（管理员写、用户读）
- storage：配置持久化，跨重启自动恢复订阅

monkeypatch app.plugins.webhooks._deliver 捕获投递（避免真实 HTTP，同 test_plugin_webhooks.py）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

_PLUGINS_SRC = Path(__file__).resolve().parents[2] / "plugins"


def _load_real(name: str, tmp_path: Path):
    src = _PLUGINS_SRC / f"{name}.py"
    assert src.is_file(), f"真实插件源码缺失: {src}"
    shutil.copy2(src, tmp_path / f"{name}.py")
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    return app


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


def _ensure_admin():
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        if not db.query(models.User).filter_by(is_admin=True).first():
            db.add(models.User(name="admin", username="admin", password_hash="x", is_admin=True))
            db.commit()
    finally:
        db.close()


def _patch_deliver(monkeypatch, calls: list):
    async def _mock(url, body, headers, *, timeout=10.0):
        calls.append({"url": url, "body": body, "headers": headers})

    monkeypatch.setattr("app.plugins.webhooks._deliver", _mock)


# --- 装载 ---


def test_loads(tmp_path):
    _load_real("webhook_notify", tmp_path)
    s = _status_map()["webhook_notify"]
    assert s.state == "loaded"
    assert "/api/plugins/webhook_notify" in s.routes


# --- routes：config 增删查 ---


def test_config_set_and_get(tmp_path, auth_headers):
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://hook.example/x", "events": ["plan_created"], "secret": "s"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is True
    g = client.get("/api/plugins/webhook_notify/config", headers=auth_headers).json()
    assert g["configured"] is True
    assert g["url"] == "http://hook.example/x"
    assert g["events"] == ["plan_created"]
    assert g["has_secret"] is True  # secret 不回显明文，仅标志


def test_config_requires_auth(tmp_path):
    app = _load_real("webhook_notify", tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://h.x", "events": ["plan_created"]},
    )
    assert r.status_code == 401  # require_admin -> require_auth 先拦


def test_get_config_when_empty(tmp_path, auth_headers):
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    assert client.get("/api/plugins/webhook_notify/config", headers=auth_headers).json() == {
        "configured": False
    }


# --- webhooks：事件投递（mock _deliver）---


def test_webhook_delivers_on_registered_event(tmp_path, auth_headers, monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://hook.example/x", "events": ["plan_created"], "secret": ""},
        headers=auth_headers,
    )
    from app.plugins import bus

    # iter 364：bus.emit 同步上下文协程监听器改后台 daemon 线程（不阻塞），测试需确认投递
    # 完成，故用 emit_async（await 监听器）+ asyncio.run 驱动
    asyncio.run(bus.emit_async("plan_created", {"topic": "React", "plan_id": 1}))
    assert len(calls) == 1
    assert calls[0]["url"] == "http://hook.example/x"
    body = json.loads(calls[0]["body"])  # _deliver 收 bytes（序列化载荷）
    assert body["event"] == "plan_created"
    assert body["data"]["topic"] == "React"


def test_unmatched_event_not_delivered(tmp_path, auth_headers, monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://h.x", "events": ["plan_created"]},
        headers=auth_headers,
    )
    from app.plugins import bus

    bus.emit("progress_updated", {"plan_id": 1})  # 未注册
    bus.emit("quiz_submitted", {"correct": True})  # 未注册
    assert calls == []


def test_hmac_secret_signed(tmp_path, auth_headers, monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://h.x", "events": ["plan_created"], "secret": "topsecret"},
        headers=auth_headers,
    )
    from app.plugins import bus

    asyncio.run(bus.emit_async("plan_created", {"topic": "x"}))
    assert "X-Webhook-Signature" in calls[0]["headers"]


def test_delete_config_stops_delivery(tmp_path, auth_headers, monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://h.x", "events": ["plan_created"]},
        headers=auth_headers,
    )
    client.delete("/api/plugins/webhook_notify/config", headers=auth_headers)
    from app.plugins import bus

    asyncio.run(bus.emit_async("plan_created", {"topic": "x"}))
    assert calls == []  # 删除后 unregister，不投递


# --- 跨重启持久化 ---


def test_config_persists_across_reload(tmp_path, auth_headers, monkeypatch):
    calls: list = []
    _patch_deliver(monkeypatch, calls)
    app = _load_real("webhook_notify", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.post(
        "/api/plugins/webhook_notify/config",
        json={"url": "http://h.x", "events": ["plan_created"]},
        headers=auth_headers,
    )
    from app.plugins.loader import reset_plugins

    reset_plugins()
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app2 = create_app()
    load_plugins(app2, tmp_path)  # _apply 从 storage 恢复 webhook 订阅
    from app.plugins import bus

    asyncio.run(bus.emit_async("plan_created", {"topic": "x"}))
    assert len(calls) == 1  # reload 后配置持久化，仍投递
