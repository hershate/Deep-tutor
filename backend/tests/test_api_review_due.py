"""全局今日待复习端点测试（iter 119）：到期筛选 / 排序 / 隔离 / 401。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.db.base import SessionLocal


def _seed():
    """三个节点：低掌握度+旧更新（到期）、高掌握度+旧更新（14 天间隔，未到期）、低掌握度+今更新（明才到期）。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        sess = models.Session(user_id=admin.id, title="复习会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "复习主题"}})
        db.add(plan)
        db.flush()
        n1 = models.KnowledgeNode(plan_id=plan.id, sid="k1", name="到期节点")
        n2 = models.KnowledgeNode(plan_id=plan.id, sid="k2", name="远期节点")
        n3 = models.KnowledgeNode(plan_id=plan.id, sid="k3", name="今日新学")
        db.add_all([n1, n2, n3])
        db.flush()
        old = datetime.now() - timedelta(days=10)
        db.add_all(
            [
                models.Progress(
                    plan_id=plan.id, node_id=n1.id,
                    status=models.ProgressStatus.learning, mastery=0.3, updated_at=old,
                ),
                models.Progress(
                    plan_id=plan.id, node_id=n2.id,
                    status=models.ProgressStatus.done, mastery=0.95, updated_at=old,
                ),
                models.Progress(
                    plan_id=plan.id, node_id=n3.id,
                    status=models.ProgressStatus.learning, mastery=0.3, updated_at=datetime.now(),
                ),
            ]
        )
        # 他人到期节点（隔离验证）
        other_sess = models.Session(user_id=other.id, title="他人会话")
        db.add(other_sess)
        db.flush()
        other_plan = models.Plan(session_id=other_sess.id, version=1, content={"intent": {}})
        db.add(other_plan)
        db.flush()
        n4 = models.KnowledgeNode(plan_id=other_plan.id, sid="k1", name="他人到期")
        db.add(n4)
        db.flush()
        db.add(
            models.Progress(
                plan_id=other_plan.id, node_id=n4.id,
                status=models.ProgressStatus.learning, mastery=0.1, updated_at=old,
            )
        )
        db.commit()
        return {"plan_id": plan.id}
    finally:
        db.close()


def test_review_due_filters_and_isolates(client, auth_headers):
    _seed()
    r = client.get("/api/progress/review/due", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    names = [i["name"] for i in body["items"]]
    assert names == ["到期节点"]  # 高掌握度 14 天间隔未到期；今日新学明天才到期；他人被隔离
    assert body["total"] == 1
    item = body["items"][0]
    assert item["plan_topic"] == "复习主题"
    assert item["node_id"] == "k1"
    assert item["mastery"] == 0.3
    assert item["next_review"] <= datetime.now().date().isoformat()


def test_review_due_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/progress/review/due", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_review_due_requires_auth(client):
    assert client.get("/api/progress/review/due").status_code == 401
