"""复习队列端点测试（iter 281，/review 翻卡复习页数据源）。

覆盖：到期筛选 / 笔记富化（有笔记/无笔记）/ 难度·必学透传 / 用户隔离 / total 全量语义 / 空态 / 401。
due 计算与 /review/due 同源（_due_items），故到期筛选与隔离口径与 test_api_review_due 一致；
本测试聚焦 review_queue 独有的笔记富化与字段透传。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.db.base import SessionLocal


def _seed():
    """到期节点 k1（带笔记+难度+必学）、到期节点 k2（无笔记），加一个未到期但带笔记的节点（不应出现）。"""
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
        n1 = models.KnowledgeNode(plan_id=plan.id, sid="k1", name="到期带笔记", difficulty=4, must_learn=True)
        n2 = models.KnowledgeNode(plan_id=plan.id, sid="k2", name="到期无笔记", difficulty=2, must_learn=False)
        n3 = models.KnowledgeNode(plan_id=plan.id, sid="k3", name="未到期带笔记", difficulty=3, must_learn=False)
        db.add_all([n1, n2, n3])
        db.flush()
        old = datetime.now() - timedelta(days=10)
        db.add_all(
            [
                models.Progress(plan_id=plan.id, node_id=n1.id, status=models.ProgressStatus.learning, mastery=0.3, updated_at=old),
                models.Progress(plan_id=plan.id, node_id=n2.id, status=models.ProgressStatus.learning, mastery=0.4, updated_at=old),
                # 高掌握度 + 旧更新 -> 14 天间隔，未到期（不应进队列）
                models.Progress(plan_id=plan.id, node_id=n3.id, status=models.ProgressStatus.done, mastery=0.95, updated_at=old),
            ]
        )
        # k1 有本人笔记（翻卡背面内容）；k3 也有笔记但未到期（验证不误入）
        db.add_all(
            [
                models.NodeNote(user_id=admin.id, node_id=n1.id, content="我对 k1 的理解笔记"),
                models.NodeNote(user_id=admin.id, node_id=n3.id, content="未到期节点的笔记"),
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
        db.add(models.Progress(plan_id=other_plan.id, node_id=n4.id, status=models.ProgressStatus.learning, mastery=0.1, updated_at=old))
        db.commit()
        return {"plan_id": plan.id}
    finally:
        db.close()


def test_review_queue_enriches_note_and_isolates(client, auth_headers):
    _seed()
    r = client.get("/api/progress/review/queue", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    by_sid = {i["node_id"]: i for i in body["items"]}
    # 仅 k1/k2 到期（k3 高掌握度 14 天间隔未到期；他人 k4 被隔离）
    assert set(by_sid) == {"k1", "k2"}
    assert body["total"] == 2

    k1 = by_sid["k1"]
    assert k1["plan_topic"] == "复习主题"
    assert k1["name"] == "到期带笔记"
    assert k1["mastery"] == 0.3
    assert k1["next_review"] <= datetime.now().date().isoformat()
    # 富化字段：笔记原文 + 难度 + 必学
    assert k1["note"] == "我对 k1 的理解笔记"
    assert k1["difficulty"] == 4
    assert k1["must_learn"] is True

    # k2 无笔记 -> note 为空串（前端据此降级为自评提示）
    k2 = by_sid["k2"]
    assert k2["note"] == ""
    assert k2["difficulty"] == 2
    assert k2["must_learn"] is False


def test_review_queue_sorted_by_due_then_mastery(client, auth_headers):
    _seed()
    body = client.get("/api/progress/review/queue", headers=auth_headers).json()
    # k1(0.3) 与 k2(0.4) 旧更新 -> next_review 同为今天；同日按 mastery 升序 -> k1 在前
    names = [i["name"] for i in body["items"]]
    assert names == ["到期带笔记", "到期无笔记"]


def test_review_queue_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/progress/review/queue", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_review_queue_requires_auth(client):
    assert client.get("/api/progress/review/queue").status_code == 401
