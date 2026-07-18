"""学习打卡日历测试（iter 139）：按日聚合 / 跨方案同日求和 / 补 0 / 用户隔离 / 401 / days 边界。"""

from __future__ import annotations

from datetime import date, timedelta

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
        s2 = models.Session(user_id=admin.id, title="B")
        s3 = models.Session(user_id=other.id, title="C")
        db.add_all([s1, s2, s3])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={})
        p2 = models.Plan(session_id=s2.id, version=1, content={})
        p3 = models.Plan(session_id=s3.id, version=1, content={})
        db.add_all([p1, p2, p3])
        db.flush()
        today = date.today()
        yesterday = today - timedelta(days=1)
        db.add_all(
            [
                models.LearningLog(plan_id=p1.id, date=today, minutes=30),
                models.LearningLog(plan_id=p2.id, date=today, minutes=20),  # 跨方案同日 -> 求和 50
                models.LearningLog(plan_id=p1.id, date=yesterday, minutes=45),
                models.LearningLog(plan_id=p3.id, date=today, minutes=999),  # 他人 -> 隔离
            ]
        )
        db.commit()
    finally:
        db.close()


def test_stats_calendar_aggregates_and_zero_fills(client, auth_headers):
    _seed()
    r = client.get("/api/progress/stats/calendar?days=7", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 7
    today = date.today()
    assert items[-1]["date"] == today.isoformat()
    by_date = {i["date"]: i["minutes"] for i in items}
    assert by_date[today.isoformat()] == 50  # 30+20 跨方案求和，他人 999 隔离
    assert by_date[(today - timedelta(days=1)).isoformat()] == 45
    for i in items[:5]:  # 无打卡日补 0
        assert i["minutes"] == 0


def test_stats_calendar_days_bounds(client, auth_headers):
    _seed()
    r = client.get("/api/progress/stats/calendar", headers=auth_headers)  # 默认 84
    assert len(r.json()["items"]) == 84
    assert client.get("/api/progress/stats/calendar?days=6", headers=auth_headers).status_code == 422
    assert client.get("/api/progress/stats/calendar?days=367", headers=auth_headers).status_code == 422


def test_stats_calendar_requires_auth(client):
    assert client.get("/api/progress/stats/calendar").status_code == 401
