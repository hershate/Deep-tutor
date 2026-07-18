"""个人笔记总览测试（iter 161）：更新倒序 / 预览截断 / 空笔记排除 / 用户隔离 / 401。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.db.base import SessionLocal


def _seed() -> None:
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
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "方案甲"}})
        p2 = models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "他人方案"}})
        db.add_all([p1, p2])
        db.flush()
        n1 = models.KnowledgeNode(plan_id=p1.id, sid="a", name="节点A", difficulty=1)
        n2 = models.KnowledgeNode(plan_id=p1.id, sid="b", name="节点B", difficulty=2)
        n3 = models.KnowledgeNode(plan_id=p2.id, sid="c", name="他人节点", difficulty=1)
        db.add_all([n1, n2, n3])
        db.flush()
        now = datetime.now()
        db.add_all(
            [
                models.NodeNote(user_id=admin.id, node_id=n1.id, content="短笔记", updated_at=now - timedelta(hours=1)),
                models.NodeNote(user_id=admin.id, node_id=n2.id, content="长" * 400, updated_at=now),
                models.NodeNote(user_id=other.id, node_id=n3.id, content="他人笔记", updated_at=now),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_notes_overview_order_and_truncate(client, auth_headers):
    _seed()
    r = client.get("/api/knowledge/notes", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2  # 他人笔记隔离
    first, second = body["items"]
    # updated_at 倒序：n2（最新）在前
    assert first["node_name"] == "节点B"
    assert first["plan_topic"] == "方案甲"
    assert len(first["content_preview"]) == 300 and first["truncated"] is True
    assert second["node_name"] == "节点A"
    assert second["content_preview"] == "短笔记" and second["truncated"] is False
    assert second["updated_at"]


def test_notes_overview_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/knowledge/notes", headers=auth_headers)
    assert r.json() == {"items": [], "total": 0}


def test_notes_overview_requires_auth(client):
    assert client.get("/api/knowledge/notes").status_code == 401
