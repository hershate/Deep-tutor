"""全局统一搜索测试（iter 249）：四域命中 / 用户隔离 / 空白 q / limit 截断 / 限流 / snippet / 认证。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed():
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        s1 = models.Session(user_id=admin.id, title="A")
        s2 = models.Session(user_id=other.id, title="B")
        db.add_all([s1, s2])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "Python 入门"}})
        p2 = models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "他人 Python"}})
        db.add_all([p1, p2])
        db.flush()
        db.add_all(
            [
                models.KnowledgeNode(plan_id=p1.id, sid="k1", name="Python 基础语法", difficulty=1),
                models.KnowledgeNode(plan_id=p2.id, sid="k1", name="他人 Python 节点", difficulty=1),
                models.Note(user_id=admin.id, title="学习备忘", content="先学 Python 再学框架"),
                models.Note(user_id=other.id, title="他人 Python 笔记", content="x"),
                models.KbDocument(user_id=admin.id, title="Python 官方教程", kind=models.MaterialKind.url),
                models.KbDocument(user_id=other.id, title="他人 Python 文档", kind=models.MaterialKind.url),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_search_matches_all_domains(client, auth_headers):
    _seed()
    r = client.get("/api/search?q=Python", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["q"] == "Python"

    plans = body["plans"]
    assert plans["total"] == 1  # 他人「他人 Python」被隔离
    assert plans["items"][0]["topic"] == "Python 入门"
    assert plans["items"][0]["session_id"]

    notes = body["notes"]
    assert notes["total"] == 1
    assert notes["items"][0]["title"] == "学习备忘"
    assert "Python" in notes["items"][0]["snippet"]

    kb = body["kb_docs"]
    assert kb["total"] == 1
    assert kb["items"][0]["title"] == "Python 官方教程"
    assert kb["items"][0]["kind"] == "url"

    nodes = body["nodes"]
    assert nodes["total"] == 1
    assert nodes["items"][0]["name"] == "Python 基础语法"
    assert nodes["items"][0]["plan_topic"] == "Python 入门"


def test_search_blank_q_returns_empty(client, auth_headers):
    _seed()
    for url in ("/api/search", "/api/search?q=%20%20"):
        body = client.get(url, headers=auth_headers).json()
        for group in ("plans", "notes", "kb_docs", "nodes"):
            assert body[group] == {"total": 0, "items": []}, url


def test_search_limit_caps_items_not_total(client, auth_headers):
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        db.add_all([models.Note(user_id=admin.id, title=f"Python 笔记 {i}", content="") for i in range(3)])
        db.commit()
    finally:
        db.close()
    body = client.get("/api/search?q=Python&limit=1", headers=auth_headers).json()
    assert body["notes"]["total"] == 3
    assert len(body["notes"]["items"]) == 1


def test_search_note_snippet_around_match(client, auth_headers):
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        long_content = "前" * 200 + "目标词" + "后" * 200
        db.add(models.Note(user_id=admin.id, title="t", content=long_content))
        db.commit()
    finally:
        db.close()
    body = client.get("/api/search?q=目标词", headers=auth_headers).json()
    snippet = body["notes"]["items"][0]["snippet"]
    assert "目标词" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")  # 前后均有截断标记
    assert len(snippet) < len(long_content)


def test_search_rate_limited(client, auth_headers, monkeypatch):
    from app.api import search as search_mod

    monkeypatch.setattr(search_mod, "_SEARCH_RATE_PER_MIN", 2)
    assert client.get("/api/search?q=a", headers=auth_headers).status_code == 200
    assert client.get("/api/search?q=a", headers=auth_headers).status_code == 200
    r = client.get("/api/search?q=a", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_search_requires_auth(client):
    assert client.get("/api/search?q=x").status_code == 401


def test_search_q_too_long_422(client, auth_headers):
    assert client.get("/api/search?q=" + "x" * 101, headers=auth_headers).status_code == 422
