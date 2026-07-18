"""浏览器剪藏端点测试（P4 iter 64）：POST /api/materials/clip 建会话 + 加资料一步到位。"""

from __future__ import annotations

from app.api import materials as materials_mod
from app.tools.base import ToolOutput


def _sessions_and_materials():
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        return db.query(models.Session).all(), db.query(models.Material).all()
    finally:
        db.close()


def test_clip_text_creates_session_and_material(client, auth_headers):
    r = client.post(
        "/api/materials/clip",
        json={"kind": "text", "content": "React 入门笔记：组件、props、state……"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "text"
    assert body["open_path"] == f"/generate/{body['session_id']}"
    assert body["title"].startswith("React 入门笔记")

    sessions, mats = _sessions_and_materials()
    assert len(sessions) == 1 and len(mats) == 1
    assert mats[0].session_id == sessions[0].id
    assert mats[0].parsed_chars > 0


def test_clip_text_empty_422(client, auth_headers):
    r = client.post(
        "/api/materials/clip", json={"kind": "text", "content": "   "}, headers=auth_headers
    )
    assert r.status_code == 422  # empty_content（与 /materials/text 一致）
    assert r.json()["error"]["code"] == "empty_content"
    sessions, _ = _sessions_and_materials()
    assert sessions == []  # 校验在建会话之前，无孤儿


def test_clip_url_bad_scheme_400(client, auth_headers):
    r = client.post(
        "/api/materials/clip", json={"kind": "url", "content": "ftp://example.com/x"}, headers=auth_headers
    )
    assert r.status_code == 400
    sessions, _ = _sessions_and_materials()
    assert sessions == []


def test_clip_url_ssrf_blocked_and_no_orphan_session(client, auth_headers):
    """SSRF 拦截（解析失败）-> 400 且刚建会话被清理（防孤儿）。"""
    r = client.post(
        "/api/materials/clip", json={"kind": "url", "content": "http://127.0.0.1/"}, headers=auth_headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ssrf_blocked"
    sessions, mats = _sessions_and_materials()
    assert sessions == [] and mats == []


def test_clip_url_success(client, auth_headers, monkeypatch):
    """url 剪藏成功路径（mock parse_material 防真实联网）。"""

    async def fake_parse(inp, ctx):
        return ToolOutput(
            tool="parse_material",
            status="ok",
            data={"text": "页面正文", "meta": {"source_type": "url", "title": "示例页"}},
        )

    monkeypatch.setattr(materials_mod, "parse_material", fake_parse)
    r = client.post(
        "/api/materials/clip",
        json={"kind": "url", "content": "https://example.com/article", "title": "示例文章"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "url"
    assert body["title"] == "示例文章"

    sessions, mats = _sessions_and_materials()
    assert len(sessions) == 1 and len(mats) == 1
    assert mats[0].kind.value == "url"


def test_clip_requires_auth(client):
    assert client.post("/api/materials/clip", json={"kind": "text", "content": "x"}).status_code == 401


def test_clip_session_owned_by_current_user(client):
    """JWT 用户剪藏 -> 会话归属该用户（他人 404，防枚举）。"""
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="alice", username="alice", password_hash=hash_password("pw1234")))
        db.add(User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()

    ra = client.post("/api/auth/login", json={"username": "alice", "password": "pw1234"})
    ha = {"Authorization": f"Bearer {ra.json()['token']}"}
    r = client.post("/api/materials/clip", json={"kind": "text", "content": "alice 的剪藏"}, headers=ha)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    rb = client.post("/api/auth/login", json={"username": "bob", "password": "pw1234"})
    hb = {"Authorization": f"Bearer {rb.json()['token']}"}
    # bob 看不到 alice 的剪藏会话（generate 404）
    assert client.post("/api/plans/generate", json={"session_id": sid}, headers=hb).status_code == 404
    # alice 自己可以（进入流程，非 404；无 key -> SSE error 终态 200）
    assert client.post("/api/plans/generate", json={"session_id": sid}, headers=ha).status_code == 200
