"""用量统计 API（plan/03 §一 usage）。

GET /api/usage  汇总当前用户的 api_usage 行：token/费用/cache hit 率（成本计量 Pillar 3 可见性）。
P4 按用户隔离（iter 58）：仅统计当前用户（api_usage.user_id）用量。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_auth)])


@router.get("")
def get_usage(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    rows = db.query(models.ApiUsage).filter_by(user_id=user.id).all()
    total_in = sum(r.tokens_in for r in rows)
    total_out = sum(r.tokens_out for r in rows)
    total_cache_read = sum(r.cache_read for r in rows)
    total_cost = sum(r.cost for r in rows)
    cache_hit_rate = (total_cache_read / total_in) if total_in else 0.0
    return {
        "total_tokens": total_in + total_out,
        "total_cost": round(total_cost, 6),
        "cache_hit_rate": round(cache_hit_rate, 4),
        "calls": len(rows),
    }
