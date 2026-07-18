"""健康检查测试（plan/09 §十四：DB 连通 / skills 加载 / 磁盘空间）。"""

from __future__ import annotations


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    comp = body["components"]
    assert comp["db"] == "ok"
    # err.md A25：未认证端点只给状态档位，不回显磁盘余量数值/组件计数
    assert comp["skills"] == "ok"
    assert comp["disk"] == "ok"


def test_health_no_auth_required(client):
    # 探活脚本无 token：健康检查公开（不含敏感信息）
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_db_error_degraded(client, monkeypatch):
    from contextlib import contextmanager

    from app.api import health as health_mod

    @contextmanager
    def _boom(*a, **k):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(health_mod.engine, "connect", _boom)
    r = client.get("/api/health")
    assert r.status_code == 200  # 恒 200，状态在 body
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["db"] == "error"
    assert "db down" not in r.text  # 不暴露异常细节（R35）


def test_health_low_disk_degraded(client, monkeypatch):
    from collections import namedtuple

    from app.api import health as health_mod

    Usage = namedtuple("usage", ["total", "used", "free"])
    monkeypatch.setattr(health_mod.shutil, "disk_usage", lambda p: Usage(0, 0, 50 * 1024 * 1024))
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["components"]["disk"] == "low"
