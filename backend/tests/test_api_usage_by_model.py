"""按模型用量统计测试（iter 303）：分模型聚合 / 费用降序 / 隔离 / 空态 / 401。"""

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
        db.add_all(
            [
                models.ApiUsage(user_id=admin.id, provider="anthropic", model="claude-sonnet-5", tokens_in=100, tokens_out=50, cache_read=0, cache_write=0, cost=0.001),
                models.ApiUsage(user_id=admin.id, provider="anthropic", model="claude-sonnet-5", tokens_in=200, tokens_out=100, cache_read=0, cache_write=0, cost=0.002),
                models.ApiUsage(user_id=admin.id, provider="anthropic", model="claude-haiku-4-5", tokens_in=50, tokens_out=20, cache_read=0, cache_write=0, cost=0.0001),
                # 他人模型行（隔离验证）
                models.ApiUsage(user_id=other.id, provider="anthropic", model="claude-sonnet-5", tokens_in=999, tokens_out=999, cache_read=0, cache_write=0, cost=9.9),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_usage_by_model_groups_and_isolates(client, auth_headers):
    _seed()
    r = client.get("/api/usage/by-model", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # sonnet 合并 2 行、haiku 1 行；other 隔离；费用降序 sonnet(0.003) > haiku(0.0001)
    assert [i["model"] for i in items] == ["claude-sonnet-5", "claude-haiku-4-5"]
    sonnet = next(i for i in items if i["model"] == "claude-sonnet-5")
    assert sonnet["cost"] == 0.003
    assert sonnet["tokens"] == 450  # (100+50)+(200+100)
    assert sonnet["calls"] == 2
    haiku = next(i for i in items if i["model"] == "claude-haiku-4-5")
    assert haiku["cost"] == 0.0001
    assert haiku["calls"] == 1


def test_usage_by_model_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    assert client.get("/api/usage/by-model", headers=auth_headers).json() == {"items": []}


def test_usage_by_model_requires_auth(client):
    assert client.get("/api/usage/by-model").status_code == 401
