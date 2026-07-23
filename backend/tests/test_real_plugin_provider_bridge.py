"""provider_bridge 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/provider_bridge.py 真实源码，验证 providers+llm+routes+storage 协同：
- providers：注册 "openai" 协议 handler
- llm：complete(provider_kind="openai") 经宿主调用 handler（日预算守卫 + 计量同口径）
- routes：PUT/GET /config、POST /chat
- storage：持久化凭证

外部 LLM 经 monkeypatch httpx.AsyncClient 捕获（避免真实 OpenAI 调用，同边界测试脚本惯例）。
"""

from __future__ import annotations

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


# --- mock httpx.AsyncClient（handler 内 OpenAI 调用）---


class _MockResp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class _MockClient:
    calls: list = []
    response: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        self.calls.append({"url": url, "json": k.get("json")})
        return _MockResp(self.response)


def _no_budget(monkeypatch):
    monkeypatch.setattr("app.api.deps.check_daily_budget", lambda db, user_id: None)


# --- 装载与发现 ---


def test_loads(tmp_path):
    _load_real("provider_bridge", tmp_path)
    s = _status_map()["provider_bridge"]
    assert s.state == "loaded"
    assert "openai" in s.providers


def test_kinds_endpoint(tmp_path, auth_headers):
    app = _load_real("provider_bridge", tmp_path)
    _ensure_admin()
    r = TestClient(app).get("/api/providers/kinds", headers=auth_headers)
    assert r.status_code == 200
    plugins = [p["kind"] for p in r.json()["plugins"]]
    assert "openai" in plugins


# --- 配置路由 ---


def test_config_set_and_get(tmp_path, auth_headers):
    app = _load_real("provider_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.put(
        "/api/plugins/provider_bridge/config",
        json={"api_key": "sk-test", "base_url": "https://api.openai.com", "model": "gpt-4o"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["has_key"] is True
    g = client.get("/api/plugins/provider_bridge/config", headers=auth_headers).json()
    assert g["configured"] is True
    assert g["model"] == "gpt-4o"
    assert "api_key" not in g  # 不回显明文 key


def test_config_requires_admin(tmp_path, auth_headers):
    """PUT /config 经 require_admin -> require_auth：无凭证 401。"""
    app = _load_real("provider_bridge", tmp_path)
    client = TestClient(app)
    r = client.put(
        "/api/plugins/provider_bridge/config",
        json={"api_key": "sk-test"},
    )
    assert r.status_code == 401


# --- POST /chat（complete provider_kind + mock httpx）---


def test_chat_with_mock_httpx(tmp_path, auth_headers, monkeypatch):
    _MockClient.calls = []
    _MockClient.response = {
        "choices": [{"message": {"content": "AI 回复：闭包像背包。"}, "finish_reason": "stop"}],
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    monkeypatch.setattr("httpx.AsyncClient", _MockClient)
    _no_budget(monkeypatch)
    app = _load_real("provider_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.put(
        "/api/plugins/provider_bridge/config",
        json={"api_key": "sk-test", "base_url": "https://api.openai.com", "model": "gpt-4o"},
        headers=auth_headers,
    )
    r = client.post(
        "/api/plugins/provider_bridge/chat", json={"prompt": "解释闭包"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert "背包" in r.json()["text"]
    assert r.json()["model"] == "gpt-4o"
    assert len(_MockClient.calls) == 1
    assert "/v1/chat/completions" in _MockClient.calls[0]["url"]
    # 验证 handler 透传 system/messages 给 OpenAI 协议
    msgs = _MockClient.calls[0]["json"]["messages"]
    assert any(m["role"] == "user" for m in msgs)


def test_chat_no_key_is_bad_gateway(tmp_path, auth_headers, monkeypatch):
    """未配 key -> handler 抛 RuntimeError -> 宿主分类 bad_gateway(502)。"""
    _no_budget(monkeypatch)
    app = _load_real("provider_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.post(
        "/api/plugins/provider_bridge/chat", json={"prompt": "x"}, headers=auth_headers
    )
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "bad_gateway"
