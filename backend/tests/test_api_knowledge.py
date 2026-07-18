"""个人知识库聚合端点测试（iter 100）：跨方案节点 / 过滤 / 状态统计 / 用户隔离。"""

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
        s1 = models.Session(user_id=admin.id, title="会话A")
        s2 = models.Session(user_id=admin.id, title="会话B")
        s3 = models.Session(user_id=other.id, title="他人会话")
        db.add_all([s1, s2, s3])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "主题甲"}})
        p2 = models.Plan(session_id=s2.id, version=2, content={"intent": {"topic": "主题乙"}})
        p3 = models.Plan(session_id=s3.id, version=1, content={"intent": {"topic": "他人主题"}})
        db.add_all([p1, p2, p3])
        db.flush()
        n1 = models.KnowledgeNode(plan_id=p1.id, sid="k1", name="二叉树", difficulty=3, must_learn=True)
        n2 = models.KnowledgeNode(plan_id=p1.id, sid="k2", name="哈希表", difficulty=2)
        n3 = models.KnowledgeNode(plan_id=p2.id, sid="k1", name="动态规划", difficulty=5)
        n4 = models.KnowledgeNode(plan_id=p3.id, sid="k1", name="他人节点", difficulty=1)
        db.add_all([n1, n2, n3, n4])
        db.flush()
        db.add(models.Progress(plan_id=p1.id, node_id=n1.id, status=models.ProgressStatus.done, mastery=0.9))
        db.add(models.Progress(plan_id=p1.id, node_id=n2.id, status=models.ProgressStatus.learning, mastery=0.4))
        db.commit()
        return {"p1": p1.id, "p2": p2.id}
    finally:
        db.close()


def test_knowledge_aggregates_across_plans(client, auth_headers):
    _seed()
    r = client.get("/api/knowledge", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    names = {i["name"] for i in body["items"]}
    assert names == {"二叉树", "哈希表", "动态规划"}  # 他人节点被隔离
    assert body["stats"] == {"not_started": 1, "learning": 1, "done": 1}
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["二叉树"]["status"] == "done" and by_name["二叉树"]["mastery"] == 0.9
    assert by_name["二叉树"]["must_learn"] is True
    assert by_name["动态规划"]["status"] == "not_started"
    assert by_name["动态规划"]["plan_topic"] == "主题乙"


def test_knowledge_filters(client, auth_headers):
    ids = _seed()
    r = client.get("/api/knowledge?q=二叉", headers=auth_headers)
    assert [i["name"] for i in r.json()["items"]] == ["二叉树"]
    r = client.get("/api/knowledge?status=done", headers=auth_headers)
    assert [i["name"] for i in r.json()["items"]] == ["二叉树"]
    r = client.get(f"/api/knowledge?plan_id={ids['p2']}", headers=auth_headers)
    assert [i["name"] for i in r.json()["items"]] == ["动态规划"]


def test_knowledge_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/knowledge", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["total"] == 0


def test_knowledge_requires_auth(client):
    assert client.get("/api/knowledge").status_code == 401
