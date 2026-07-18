"""方案列表主题搜索测试（iter 128）：q 子串过滤 / 分页 total 一致 / 隔离 / 空白忽略。"""

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
        db.add_all(
            [
                models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "Python 入门"}}),
                models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "数据结构进阶"}}),
                models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "Python 爬虫"}}),
                models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "他人 Python"}}),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_plans_search_filters_by_topic(client, auth_headers):
    _seed()
    r = client.get("/api/plans?q=Python", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2  # 他人「他人 Python」被隔离
    assert {i["topic"] for i in body["items"]} == {"Python 入门", "Python 爬虫"}

    r = client.get("/api/plans?q=数据结构", headers=auth_headers)
    assert r.json()["total"] == 1

    r = client.get("/api/plans?q=不存在", headers=auth_headers)
    assert r.json() == {"items": [], "total": 0}


def test_plans_search_blank_ignored(client, auth_headers):
    _seed()
    r = client.get("/api/plans?q=%20%20", headers=auth_headers)  # 纯空白 -> 不过滤
    assert r.json()["total"] == 3
    r = client.get("/api/plans", headers=auth_headers)
    assert r.json()["total"] == 3


def test_plans_search_requires_auth(client):
    assert client.get("/api/plans?q=x").status_code == 401
