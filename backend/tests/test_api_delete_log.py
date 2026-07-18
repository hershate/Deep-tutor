"""学习日志删除测试（iter 150）：删除成功 / 他人 404 / 不存在 404 / 401 / 删除后统计正确。"""

from __future__ import annotations

from datetime import date

from app.db.base import SessionLocal


def _seed() -> dict:
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
        p1 = models.Plan(session_id=s1.id, version=1, content={})
        p2 = models.Plan(session_id=s2.id, version=1, content={})
        db.add_all([p1, p2])
        db.flush()
        l1 = models.LearningLog(plan_id=p1.id, date=date.today(), minutes=30)
        l2 = models.LearningLog(plan_id=p1.id, date=date.today(), minutes=20)
        l3 = models.LearningLog(plan_id=p2.id, date=date.today(), minutes=99)
        db.add_all([l1, l2, l3])
        db.commit()
        return {"plan_id": p1.id, "l1": l1.id, "l2": l2.id, "l3": l3.id}
    finally:
        db.close()


def test_delete_learning_log(client, auth_headers):
    ids = _seed()
    r = client.delete(f"/api/progress/logs/{ids['l1']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "id": ids["l1"]}
    # 列表只剩 l2，累计分钟更新
    r = client.get(f"/api/progress/logs/{ids['plan_id']}", headers=auth_headers)
    body = r.json()
    assert [lg["id"] for lg in body["logs"]] == [ids["l2"]]
    assert body["total_minutes"] == 20
    # 再删 -> 404
    assert client.delete(f"/api/progress/logs/{ids['l1']}", headers=auth_headers).status_code == 404


def test_delete_learning_log_isolation(client, auth_headers):
    ids = _seed()
    # 他人日志 404（不泄露存在性）
    assert client.delete(f"/api/progress/logs/{ids['l3']}", headers=auth_headers).status_code == 404
    # 不存在 404
    assert client.delete("/api/progress/logs/99999", headers=auth_headers).status_code == 404


def test_delete_learning_log_requires_auth(client):
    ids = _seed()
    assert client.delete(f"/api/progress/logs/{ids['l1']}").status_code == 401
