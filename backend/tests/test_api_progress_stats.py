"""全局学习统计端点测试（iter 122）：分钟数汇总 / streak 语义 / 隔离 / 空库 / 401。"""

from __future__ import annotations

from datetime import date, timedelta

from app.db.base import SessionLocal


def _seed(log_days: list[int], other_days: list[int] | None = None):
    """log_days：距今天的天数列表（0=今天），每天一条 30 分钟；other_days 为他人打卡。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        sess = models.Session(user_id=admin.id, title="统计会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "T"}})
        db.add(plan)
        db.flush()
        today = date.today()
        for ago in log_days:
            db.add(
                models.LearningLog(plan_id=plan.id, date=today - timedelta(days=ago), minutes=30)
            )
        if other_days:
            other_sess = models.Session(user_id=other.id, title="他人会话")
            db.add(other_sess)
            db.flush()
            other_plan = models.Plan(session_id=other_sess.id, version=1, content={})
            db.add(other_plan)
            db.flush()
            for ago in other_days:
                db.add(
                    models.LearningLog(
                        plan_id=other_plan.id, date=today - timedelta(days=ago), minutes=99
                    )
                )
        db.commit()
    finally:
        db.close()


def test_stats_minutes_and_streak(client, auth_headers):
    _seed([0, 1, 2, 10], other_days=[0, 1])
    r = client.get("/api/progress/stats/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_minutes"] == 120  # 4 条 ×30（他人 99 分钟不计）
    assert body["week_minutes"] == 90  # 10 天前那条不在近 7 日
    assert body["streak_days"] == 3  # 今天+昨天+前天连续
    assert body["total_logs"] == 4


def test_stats_streak_allows_today_missing(client, auth_headers):
    """今天还没打卡不清零：昨天+前天 -> streak 2。"""
    _seed([1, 2, 3])
    r = client.get("/api/progress/stats/summary", headers=auth_headers)
    assert r.json()["streak_days"] == 3


def test_stats_streak_breaks_on_gap(client, auth_headers):
    """断档即断链：今天+前天 -> streak 1；仅 5 天前 -> 0。"""
    _seed([0, 2])
    assert client.get("/api/progress/stats/summary", headers=auth_headers).json()["streak_days"] == 1


def test_stats_empty_and_auth(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/progress/stats/summary", headers=auth_headers)
    assert r.json() == {"total_minutes": 0, "week_minutes": 0, "streak_days": 0, "total_logs": 0}
    assert client.get("/api/progress/stats/summary").status_code == 401
