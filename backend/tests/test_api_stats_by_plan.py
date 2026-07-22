"""学习时长按方案分布测试（iter 304）：按 plan 聚合 / 分钟降序 / 隔离 / 空态 / 401。"""

from __future__ import annotations

from datetime import date, timedelta

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
        sa = models.Session(user_id=admin.id, title="A")
        sb = models.Session(user_id=admin.id, title="B")
        so = models.Session(user_id=other.id, title="C")
        db.add_all([sa, sb, so])
        db.flush()
        pa = models.Plan(session_id=sa.id, version=1, content={"intent": {"topic": "方案甲"}})
        pb = models.Plan(session_id=sb.id, version=1, content={"intent": {"topic": "方案乙"}})
        po = models.Plan(session_id=so.id, version=1, content={"intent": {"topic": "他人方案"}})
        db.add_all([pa, pb, po])
        db.flush()
        today = date.today()
        db.add_all(
            [
                models.LearningLog(plan_id=pa.id, date=today, minutes=30),
                models.LearningLog(plan_id=pa.id, date=today - timedelta(days=1), minutes=20),  # 甲共 50
                models.LearningLog(plan_id=pb.id, date=today, minutes=10),  # 乙 10
                models.LearningLog(plan_id=po.id, date=today, minutes=999),  # 他人 -> 隔离
            ]
        )
        db.commit()
        return {"pa": pa.id, "pb": pb.id}
    finally:
        db.close()


def test_stats_by_plan_aggregates_and_isolates(client, auth_headers):
    ids = _seed()
    r = client.get("/api/progress/stats/by-plan", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # 甲 50 > 乙 10；他人隔离；分钟降序
    assert [i["plan_id"] for i in items] == [ids["pa"], ids["pb"]]
    a = next(i for i in items if i["plan_id"] == ids["pa"])
    assert a["minutes"] == 50
    assert a["topic"] == "方案甲"
    assert a["last_date"] == date.today().isoformat()
    b = next(i for i in items if i["plan_id"] == ids["pb"])
    assert b["minutes"] == 10


def test_stats_by_plan_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    assert client.get("/api/progress/stats/by-plan", headers=auth_headers).json() == {"items": []}


def test_stats_by_plan_requires_auth(client):
    assert client.get("/api/progress/stats/by-plan").status_code == 401
