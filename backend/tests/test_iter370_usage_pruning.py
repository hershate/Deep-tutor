"""iter 370（R6 裁决缺省项 F4）：api_usage 保留裁剪回归。

无保留策略时每 LLM 调用一行的计量表长期无限增长；maintenance 每日轮删超期行，
settings.api_usage_retention_days 可配（默认 180，0=禁用）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.maintenance import prune_api_usage
from app.db import models
from app.db.base import SessionLocal


def _seed_usage(days_ago: list[int]) -> list[int]:
    db = SessionLocal()
    u = models.User(name="u", username=f"prune-{id(days_ago)}-{len(days_ago)}", password_hash="x")
    db.add(u)
    db.flush()
    ids = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for d in days_ago:
        row = models.ApiUsage(user_id=u.id, provider="anthropic", model="m", tokens_in=1, tokens_out=1, cost=0.01)
        row.created_at = now - timedelta(days=d)  # 列为 naive UTC
        db.add(row)
        db.flush()
        ids.append(row.id)
    db.commit()
    db.close()
    return ids


def test_prune_deletes_only_expired(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "api_usage_retention_days", 30)
    _seed_usage([90, 200, 3])  # 90/200 天超期，3 天在窗内（按 created_at 全表裁剪，与用户无关）
    removed = prune_api_usage()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        assert removed >= 2  # 其它测试残留行不影响下界断言
        # 3 天前的行必在
        assert db.query(models.ApiUsage).filter(models.ApiUsage.created_at >= now - timedelta(days=4)).count() >= 1
        # 90/200 天的行必不在
        assert db.query(models.ApiUsage).filter(models.ApiUsage.created_at < now - timedelta(days=60)).count() == 0
    finally:
        db.close()


def test_prune_disabled_keeps_all(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "api_usage_retention_days", 0)
    _seed_usage([400])
    assert prune_api_usage() == 0
    db = SessionLocal()
    try:
        assert db.query(models.ApiUsage).filter(models.ApiUsage.created_at < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=300)).count() >= 1
    finally:
        db.close()


def test_run_maintenance_reports_pruned(monkeypatch):
    from app.core.maintenance import run_maintenance
    from app.core.settings import settings

    monkeypatch.setattr(settings, "api_usage_retention_days", 30)
    _seed_usage([100])
    stats = run_maintenance()
    assert stats["api_usage_pruned"] >= 1
