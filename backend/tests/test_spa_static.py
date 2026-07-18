"""SPA 静态托管（FRONTEND_DIST）测试：免 nginx 本地直起（iter 67）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import create_app


@pytest.fixture()
def spa_client(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html><body>spa</body></html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(settings, "frontend_dist", str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


def test_root_serves_index(spa_client):
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "spa" in r.text


def test_unknown_frontend_route_falls_back_to_index(spa_client):
    # 前端路由（如 /learn/123）由 React Router 接管，后端回退 index.html
    for path in ("/login", "/learn/123", "/generate/9"):
        r = spa_client.get(path)
        assert r.status_code == 200, path
        assert "spa" in r.text


def test_static_asset_served(spa_client):
    r = spa_client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_api_not_shadowed(spa_client, auth_headers):
    # /api 路由优先于静态挂载：正常 API 响应（JSON），不是 index.html
    r = spa_client.get("/api/plans", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_api_unknown_path_returns_json_404_not_html(spa_client, auth_headers):
    r = spa_client.get("/api/no-such-endpoint", headers=auth_headers)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "spa" not in r.text


def test_unauthenticated_api_still_401(spa_client):
    r = spa_client.get("/api/plans")
    assert r.status_code == 401


def test_disabled_without_frontend_dist(tmp_path, monkeypatch, auth_headers):
    monkeypatch.setattr(settings, "frontend_dist", "")
    with TestClient(create_app()) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/plans", headers=auth_headers).status_code == 200


def test_missing_index_disables_mount(tmp_path, monkeypatch):
    # 目录存在但无 index.html（未构建）-> 不挂载，告警而非运行期 500
    monkeypatch.setattr(settings, "frontend_dist", str(tmp_path))
    with TestClient(create_app()) as c:
        assert c.get("/").status_code == 404
