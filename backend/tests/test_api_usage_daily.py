"""用量按日聚合测试（iter 131）：GROUP BY 按日 / 无调用日补 0 / 用户隔离 / 401 / days 边界。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

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
        today = date.today()
        yesterday = today - timedelta(days=1)

        def usage(uid: int, day: date, cost: float, tin: int, tout: int) -> models.ApiUsage:
            return models.ApiUsage(
                user_id=uid,
                cost=cost,
                tokens_in=tin,
                tokens_out=tout,
                cache_read=0,
                created_at=datetime.combine(day, time(12, 0)),
            )

        db.add_all(
            [
                usage(admin.id, today, 0.10, 100, 50),
                usage(admin.id, today, 0.20, 200, 100),  # 同日两行 -> 聚合
                usage(admin.id, yesterday, 0.05, 40, 10),
                usage(other.id, today, 9.99, 9999, 9999),  # 他人 -> 隔离
            ]
        )
        db.commit()
    finally:
        db.close()


def test_usage_daily_aggregates_and_zero_fills(client, auth_headers):
    _seed()
    r = client.get("/api/usage/daily?days=7", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 7
    today = date.today()
    assert items[-1]["date"] == today.isoformat()  # 连续 7 天，末位为今天
    by_date = {i["date"]: i for i in items}
    t = by_date[today.isoformat()]
    assert t["calls"] == 2
    assert t["tokens"] == 450  # (100+50)+(200+100)
    assert abs(t["cost"] - 0.30) < 1e-9
    y = by_date[(today - timedelta(days=1)).isoformat()]
    assert y["calls"] == 1 and y["tokens"] == 50 and abs(y["cost"] - 0.05) < 1e-9
    # 无调用日补 0
    for i in items[:5]:
        assert i["calls"] == 0 and i["tokens"] == 0 and i["cost"] == 0.0


def test_usage_daily_days_bounds(client, auth_headers):
    _seed()
    r = client.get("/api/usage/daily", headers=auth_headers)  # 默认 14
    assert len(r.json()["items"]) == 14
    r = client.get("/api/usage/daily?days=1", headers=auth_headers)
    assert len(r.json()["items"]) == 1
    assert client.get("/api/usage/daily?days=0", headers=auth_headers).status_code == 422
    assert client.get("/api/usage/daily?days=91", headers=auth_headers).status_code == 422


def test_usage_daily_requires_auth(client):
    assert client.get("/api/usage/daily").status_code == 401
