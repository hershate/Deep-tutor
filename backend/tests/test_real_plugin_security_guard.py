"""security_guard 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/security_guard.py 真实源码，验证 middleware+events+storage+routes 协同：
- middleware：全响应加安全头 + 内存请求计数（iter 334 api.middleware 真实用例）
- events：startup 恢复 / shutdown 落盘累计计数
- storage：持久化请求计数（跨重启）
- routes：GET /stats
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


# --- 装载 ---


def test_loads(tmp_path):
    _load_real("security_guard", tmp_path)
    s = _status_map()["security_guard"]
    assert s.state == "loaded"
    assert "security_guard" in s.middleware
    assert "/api/plugins/security_guard" in s.routes


# --- middleware：安全头 + 不阻断 ---


def test_security_headers_on_plugin_endpoint(tmp_path, auth_headers):
    app = _load_real("security_guard", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/plugins/security_guard/stats", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_security_headers_on_unrelated_endpoint(tmp_path, auth_headers):
    """中间件对所有响应加头（不只插件端点），不阻断宿主端点。"""
    app = _load_real("security_guard", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/health", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"  # 宿主端点也被加头


# --- 请求计数 ---


def test_request_counting(tmp_path, auth_headers):
    app = _load_real("security_guard", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    for _ in range(3):
        client.get("/api/plugins/security_guard/stats", headers=auth_headers)
    r = client.get("/api/plugins/security_guard/stats", headers=auth_headers)
    d = r.json()
    assert d["request_counts"].get("GET", 0) >= 4
    assert d["total_requests"] >= 4
    assert "X-Content-Type-Options" in d["security_headers"]


def test_stats_requires_auth(tmp_path):
    app = _load_real("security_guard", tmp_path)
    assert TestClient(app).get("/api/plugins/security_guard/stats").status_code == 401


# --- 跨重启持久化（events startup/shutdown + storage）---


def test_counts_persist_via_shutdown_startup(tmp_path, auth_headers):
    from app.plugins import bus
    from app.plugins.loader import reset_plugins

    app = _load_real("security_guard", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    client.get("/api/plugins/security_guard/stats", headers=auth_headers)
    client.get("/api/plugins/security_guard/stats", headers=auth_headers)
    bus.emit("shutdown", {})  # 落盘 counts（on_shutdown -> storage.set）
    reset_plugins()

    from app.main import create_app
    from app.plugins.loader import load_plugins

    app2 = create_app()
    load_plugins(app2, tmp_path)
    bus.emit("startup", {})  # 恢复 counts（on_startup -> storage.get）
    client2 = TestClient(app2)
    r = client2.get("/api/plugins/security_guard/stats", headers=auth_headers)
    assert r.json()["request_counts"].get("GET", 0) >= 2  # 恢复的累计值仍在
