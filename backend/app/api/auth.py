"""认证 API（P4 多用户，iter 57）。

POST /api/auth/login  用户名+密码 -> JWT（前端存 localStorage 后随 Bearer 发送）
GET  /api/auth/me     当前用户信息（JWT 或 AUTH_TOKEN 回退身份）

login 不设 require_auth（认证本身）；失败统一 401（不区分用户不存在/密码错，防枚举）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.core.security import create_token, dummy_verify, verify_password
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

# 登录限流（err.md A8：防在线爆破；按 IP+用户名维度，每分钟 10 次）
_LOGIN_RATE_PER_MIN = 10
# 账号维度聚合桶：ip+username 桶可被多 IP 分布式爆破绕过（每桶独立计数），
# 账号维度再设一道较宽上限（正常用户不会从 >20 个不同 IP 一分钟内登录同一账号）
_LOGIN_ACCT_RATE_PER_MIN = 20


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
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    # err.md A8：限流 + 时序拉平（不存在的用户也执行一次 pbkdf2 运算，
    # 消除「用户不存在短路 vs 200k 轮校验」的响应时间差，防用户名枚举）
    client_host = request.client.host if request.client else "unknown"
    if not limiter.check(f"login:{client_host}:{body.username}", _LOGIN_RATE_PER_MIN):
        raise ApiError("rate_limited", "登录尝试过于频繁，请稍后再试", status=429)
    if not limiter.check(f"login:acct:{body.username}", _LOGIN_ACCT_RATE_PER_MIN):
        raise ApiError("rate_limited", "该账号登录尝试过于频繁，请稍后再试", status=429)
    user = db.query(User).filter_by(username=body.username).first()
    if user is None:
        dummy_verify(body.password)
        raise ApiError("unauthorized", "用户名或密码错误", status=401)
    if not verify_password(body.password, user.password_hash):
        raise ApiError("unauthorized", "用户名或密码错误", status=401)
    return {"token": create_token(user.id, user.username or ""), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return _user_out(user)
