"""认证 API（P4 多用户，iter 57）。

POST /api/auth/login  用户名+密码 -> JWT（前端存 localStorage 后随 Bearer 发送）
GET  /api/auth/me     当前用户信息（JWT 或 AUTH_TOKEN 回退身份）

login 不设 require_auth（认证本身）；失败统一 401（不区分用户不存在/密码错，防枚举）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.errors import ApiError
from app.core.security import create_token, verify_password
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username or "",
        "name": user.name,
        "is_admin": bool(user.is_admin),
    }


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter_by(username=body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError("unauthorized", "用户名或密码错误", status=401)
    return {"token": create_token(user.id, user.username or ""), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return _user_out(user)
