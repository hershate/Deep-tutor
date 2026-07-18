"""用量统计 API（plan/03 §一 usage）。

GET /api/usage        汇总当前用户的 api_usage 行：token/费用/cache hit 率（成本计量 Pillar 3 可见性）。
GET /api/usage/daily  近 N 日按日费用/token/调用数（iter 131，用量页趋势图；无调用日补 0）。
P4 按用户隔离（iter 58）：仅统计当前用户（api_usage.user_id）用量。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_auth)])


@router.get("")
def get_usage(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    # err.md A19：SQL 聚合替代全表读入 Python 求和（用量增长后内存/耗时线性上升）
    total_in, total_out, total_cache_read, total_cost, calls = db.query(
        func.coalesce(func.sum(models.ApiUsage.tokens_in), 0),
        func.coalesce(func.sum(models.ApiUsage.tokens_out), 0),
        func.coalesce(func.sum(models.ApiUsage.cache_read), 0),
        func.coalesce(func.sum(models.ApiUsage.cost), 0.0),
        func.count(models.ApiUsage.id),
    ).filter_by(user_id=user.id).one()
    cache_hit_rate = (total_cache_read / total_in) if total_in else 0.0
    return {
        "total_tokens": int(total_in + total_out),
        "total_cost": round(float(total_cost), 6),
        "cache_hit_rate": round(cache_hit_rate, 4),
        "calls": int(calls),
    }


@router.get("/daily")
def get_usage_daily(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """近 N 日（含今天）按日聚合：cost/tokens/calls；无调用日补 0（前端柱状图连续）。

    日界口径：created_at 为 naive UTC（全库同口径），func.date 按 UTC 日分组；
    非 UTC 时区日界最多差数小时，对趋势图可接受。
    """
    start = date.today() - timedelta(days=days - 1)
    rows = (
        db.query(
            func.date(models.ApiUsage.created_at).label("d"),
            func.coalesce(func.sum(models.ApiUsage.cost), 0.0),
            func.coalesce(func.sum(models.ApiUsage.tokens_in + models.ApiUsage.tokens_out), 0),
            func.count(models.ApiUsage.id),
        )
        .filter(
            models.ApiUsage.user_id == user.id,
            models.ApiUsage.created_at >= datetime.combine(start, time.min),
        )
        .group_by(func.date(models.ApiUsage.created_at))
        .all()
    )
    by_date: dict[str, tuple[float, int, int]] = {
        str(r[0]): (float(r[1]), int(r[2]), int(r[3])) for r in rows
    }
    items = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        cost, tokens, calls = by_date.get(d, (0.0, 0, 0))
        items.append({"date": d, "cost": round(cost, 6), "tokens": tokens, "calls": calls})
    return {"items": items}
