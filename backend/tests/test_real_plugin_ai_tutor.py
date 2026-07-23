"""ai_tutor 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/ai_tutor.py 真实源码，验证 llm+tools.call+routes 协同：
- llm：/explain 经 api.llm.complete 生成解释（mock provider）；无供应商优雅降级
- tools.call：/breakdown 经 api.tools.call 组合核心 parse_material（纯 Python，稳定）
- routes：/status 探测供应商

LLM 路径用 _StubProvider + monkeypatch（同 test_plugin_llm.py 范式），TestClient 同进程 patch 生效。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.llm.base import LLMProvider, LLMResponse, LLMUsage

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


class _StubProvider(LLMProvider):
    def __init__(self, content: str = "闭包像一个会记住外层变量的背包。"):
        self._content = content

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return LLMResponse(
            content=self._content,
            usage=LLMUsage(input_tokens=11, output_tokens=7, model="claude-sonnet-5"),
            stop_reason="end_turn",
        )


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr("app.llm.get_provider", lambda user_id=None: provider)


def _patch_no_provider(monkeypatch):
    def _raise(user_id=None):
        raise RuntimeError("无供应商")

    monkeypatch.setattr("app.llm.get_provider", _raise)


def _no_budget(monkeypatch):
    monkeypatch.setattr("app.api.deps.check_daily_budget", lambda db, user_id: None)


# --- 装载 ---


def test_loads(tmp_path):
    _load_real("ai_tutor", tmp_path)
    s = _status_map()["ai_tutor"]
    assert s.state == "loaded"
    assert "/api/plugins/ai_tutor" in s.routes


# --- GET /status（is_configured 探测）---


def test_status_no_provider(tmp_path, auth_headers, monkeypatch):
    _patch_no_provider(monkeypatch)
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).get("/api/plugins/ai_tutor/status", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_status_with_provider(tmp_path, auth_headers, monkeypatch):
    _patch_provider(monkeypatch, _StubProvider())
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).get("/api/plugins/ai_tutor/status", headers=auth_headers)
    assert r.json()["configured"] is True


# --- POST /explain（llm.complete）---


def test_explain_no_provider_returns_hint(tmp_path, auth_headers, monkeypatch):
    """无供应商 -> 优雅降级返回 hint（不 503，前端据此引导配置）。"""
    _patch_no_provider(monkeypatch)
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).post(
        "/api/plugins/ai_tutor/explain", json={"text": "闭包", "kind": "analogy"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["configured"] is False
    assert "hint" in r.json()


def test_explain_with_provider(tmp_path, auth_headers, monkeypatch):
    _patch_provider(monkeypatch, _StubProvider(content="闭包像会记住外层变量的背包。"))
    _no_budget(monkeypatch)
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).post(
        "/api/plugins/ai_tutor/explain", json={"text": "闭包", "kind": "analogy"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["configured"] is True
    assert d["kind"] == "analogy"
    assert "背包" in d["explanation"]
    assert d["model"]


def test_explain_kind_fallback(tmp_path, auth_headers, monkeypatch):
    """未知 kind -> 回退 analogy prompt（白名单），不报错。"""
    _patch_provider(monkeypatch, _StubProvider(content="类比结果。"))
    _no_budget(monkeypatch)
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).post(
        "/api/plugins/ai_tutor/explain", json={"text": "x", "kind": "unknown"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "unknown"  # 原样回显，但 prompt 走 analogy 兜底


# --- POST /breakdown（tools.call 组合核心 parse_material）---


def test_breakdown_tools_call(tmp_path, auth_headers):
    """tools.call 组合核心 parse_material（Python 工具，不需 provider）——验证组合工作流。"""
    app = _load_real("ai_tutor", tmp_path)
    _ensure_admin()
    r = TestClient(app).post(
        "/api/plugins/ai_tutor/breakdown", json={"text": "Hello world. Foo bar baz."}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "ok"
    assert d["chars"] > 0
    assert d["chunk_count"] >= 1


# --- 认证 ---


def test_endpoints_require_auth(tmp_path):
    app = _load_real("ai_tutor", tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/ai_tutor/status").status_code == 401
    assert client.post("/api/plugins/ai_tutor/explain", json={"text": "x"}).status_code == 401
