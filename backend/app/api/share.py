"""协作分享 API（P5）：方案只读分享链接。

- POST   /api/plans/{plan_id}/share  创建（或返回既有）分享链接（仅方案所有者）
- DELETE /api/plans/{plan_id}/share  撤销链接（仅方案所有者）
- GET    /api/shared/{token}         公开只读访问（无认证；只含方案内容，不含用户/会话/资料）

安全约定：token 为 192bit 随机串（secrets.token_urlsafe(24)），不可枚举；
公开端点永不返回 user/session/material 信息（R35 隐私）。
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_plan, require_auth
from app.api.errors import ApiError
from app.db.models import Plan, ShareLink, User

# 认证轨：挂在 /plans 下（与 plans.py 同前缀，所有权校验 404 防枚举）
router = APIRouter(prefix="/plans", tags=["share"], dependencies=[Depends(require_auth)])
# 公开轨：无认证
public_router = APIRouter(prefix="/shared", tags=["share"])


def _load_content(plan: Plan) -> dict:
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    return content or {}


@router.post("/{plan_id}/share", status_code=201)
def create_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    plan = get_owned_plan(db, plan_id, user)  # 他人方案 404
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is None:
        link = ShareLink(plan_id=plan.id, token=secrets.token_urlsafe(24))
        db.add(link)
        db.commit()
        db.refresh(link)
    return {"token": link.token, "path": f"/shared/{link.token}", "created_at": link.created_at.isoformat()}


@router.delete("/{plan_id}/share", status_code=204)
def revoke_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    plan = get_owned_plan(db, plan_id, user)
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is not None:
        db.delete(link)
        db.commit()


@router.get("/{plan_id}/share")
def get_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """查询当前方案的分享状态（无链接 -> {"shared": false}）。"""
    plan = get_owned_plan(db, plan_id, user)
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is None:
        return {"shared": False}
    return {"shared": True, "token": link.token, "path": f"/shared/{link.token}", "created_at": link.created_at.isoformat()}


@public_router.get("/{token}")
def read_shared(token: str, db: Session = Depends(get_db)) -> dict:
    link = db.query(ShareLink).filter_by(token=token).first()
    if link is None:
        raise ApiError("not_found", "分享链接不存在或已撤销")
    plan = db.get(Plan, link.plan_id)
    if plan is None:
        raise ApiError("not_found", "方案已删除")
    content = _load_content(plan)
    return {
        "title": content.get("title") or content.get("topic") or "学习方案",
        "created_at": plan.created_at.isoformat() if plan.created_at else "",
        "content": content,
    }
