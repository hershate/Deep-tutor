"""用户级日预算覆盖测试（iter 176 参数配置）。

PUT /api/settings/prefs 设置/清除 user_prefs.daily_budget_usd；
有效值贯通 GET /settings、GET /usage 与日预算守卫 check_daily_budget。
"""

from __future__ import annotations

import pytest

from app.db import models
from app.db.base import SessionLocal
from app.db.models import User


def _seed_admin() -> None:
    db = SessionLocal()
    try:
        if db.query(User).filter_by(username="admin").first() is None:
            db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
            db.commit()
    finally:
        db.close()


def test_prefs_default_no_override(client, auth_headers):
    _seed_admin()
    s = client.get("/api/settings", headers=auth_headers).json()
    assert s["daily_budget_is_override"] is False
    assert s["daily_budget_effective"] == s["daily_budget_usd"]


def test_prefs_set_and_clear_override(client, auth_headers):
    _seed_admin()
    r = client.put("/api/settings/prefs", json={"daily_budget_usd": 5.0}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["daily_budget_usd"] == 5.0
    assert body["daily_budget_is_override"] is True

    # 贯通 GET /settings 与 /usage
    s = client.get("/api/settings", headers=auth_headers).json()
    assert s["daily_budget_effective"] == 5.0
    assert s["daily_budget_is_override"] is True
    u = client.get("/api/usage", headers=auth_headers).json()
    assert u["daily_budget_usd"] == 5.0

    # null 清除覆盖 -> 回退默认，user_prefs 行删除
    r2 = client.put("/api/settings/prefs", json={"daily_budget_usd": None}, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["daily_budget_is_override"] is False
    s2 = client.get("/api/settings", headers=auth_headers).json()
    assert s2["daily_budget_is_override"] is False
    db = SessionLocal()
    try:
        assert db.query(models.UserPref).filter_by(key="daily_budget_usd").count() == 0
    finally:
        db.close()


@pytest.mark.parametrize("bad", [-1, 100001])
def test_prefs_invalid_value_422(client, auth_headers, bad):
    _seed_admin()
    r = client.put("/api/settings/prefs", json={"daily_budget_usd": bad}, headers=auth_headers)
    assert r.status_code == 422


def test_prefs_override_governs_budget_guard(client, auth_headers):
    """覆盖值低于当日已花费 -> 守卫 402；默认值本不会触发（证明走的是覆盖值）。"""
    from datetime import datetime, timezone

    from app.api.deps import check_daily_budget

    _seed_admin()
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username="admin").one()
        db.add(
            models.ApiUsage(
                user_id=user.id,
                model="m",
                tokens_in=1,
                tokens_out=1,
                cost=2.0,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        db.commit()
        # 默认 10 不会触发；覆盖 1.0 后当日已花 2.0 -> 402
        client.put("/api/settings/prefs", json={"daily_budget_usd": 1.0}, headers=auth_headers)
        from app.api.errors import ApiError

        with pytest.raises(ApiError) as exc:
            check_daily_budget(db, user.id)
        assert exc.value.code == "budget_exceeded"
        assert exc.value.status == 402
    finally:
        db.close()
