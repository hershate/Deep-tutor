"""用户管理 API（P4 多用户，iter 59；账号体系=管理员预设+创建）。

全部端点仅管理员（require_admin，非管理员 403）：
- GET    /api/users         用户列表（不含 password_hash）
- POST   /api/users         创建用户 {username, password, name?, is_admin?}
- PUT    /api/users/{id}    更新 {name?, is_admin?, password?（重置）}
- DELETE /api/users/{id}    删除用户（级联其会话/方案/供应商档案/用量 + 会话文件目录）

稳定性/安全护栏：
- 不能删除/降级自己（防管理员把自己锁在门外）；
- 不能删除/降级最后一名管理员（系统至少保留一名管理员）；
- 删除用户级联清理其数据（sessions ORM 级联 plans/materials 等 + 文件目录；
  provider profiles / api_usage 行删除，防 FK 违约）；
- username 唯一 409；密码最短 4 位。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.ratelimit import limiter
from app.api.errors import ApiError
from app.api.plans import cancel_running_generation
from app.core.security import hash_password
from app.core.storage import delete_kb_files, delete_session_files
from app.db import models
from app.db.models import User

log = logging.getLogger("deep_tutor.api.users")

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username or "",
        "name": u.name,
        "is_admin": bool(u.is_admin),
        "created_at": str(u.created_at),
    }


def _admin_count(db: Session) -> int:
    return db.query(User).filter_by(is_admin=True).count()


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=256)
    name: str = Field(default="", max_length=64)
    is_admin: bool = False


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=4, max_length=256)  # 重置密码；None -> 不变


@router.get("")
def list_users(db: Session = Depends(get_db), _admin: User = Depends(require_admin)) -> dict:
    rows = db.query(User).order_by(User.id).all()
    return {"users": [_user_out(u) for u in rows]}


@router.post("", status_code=201)
def create_user(
    body: UserCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> dict:
    if not limiter.check(f"users_write:{_admin.id}", 20):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    username = body.username.strip()
    if not username:
        raise ApiError("bad_request", "用户名不能为空", status=400)
    dup = db.query(User).filter_by(username=username).first()
    if dup is not None:
        raise ApiError("conflict", f"用户名已存在: {username}", status=409)
    u = User(
        name=body.name.strip() or username,
        username=username,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        # err.md A16：并发创建撞 uq_users_username -> 409（而非 500）
        db.rollback()
        raise ApiError("conflict", f"用户名已存在: {username}", status=409) from None
    db.refresh(u)
    return _user_out(u)


@router.put("/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    if not limiter.check(f"users_write:{admin.id}", 20):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    u = db.get(User, user_id)
    if u is None:
        raise ApiError("not_found", f"用户 {user_id} 不存在")
    if body.is_admin is not None and body.is_admin != bool(u.is_admin):
        if not body.is_admin:
            if u.id == admin.id:
                raise ApiError("conflict", "不能降级自己的管理员权限", status=409)
            if u.is_admin and _admin_count(db) <= 1:
                raise ApiError("conflict", "系统至少保留一名管理员", status=409)
        u.is_admin = body.is_admin
    if body.name is not None:
        u.name = body.name.strip() or u.name
    if body.password is not None:
        u.password_hash = hash_password(body.password)
        u.tokens_valid_after = time.time()  # err.md A9：重置密码吊销旧 JWT
    db.commit()
    db.refresh(u)
    return _user_out(u)


@router.delete("/{user_id}")
def delete_user(
    user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> dict:
    if not limiter.check(f"users_write:{admin.id}", 20):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    u = db.get(User, user_id)
    if u is None:
        raise ApiError("not_found", f"用户 {user_id} 不存在")
    if u.id == admin.id:
        raise ApiError("conflict", "不能删除当前登录的管理员账号", status=409)
    # iter 346（F5）：禁删任意管理员（前端 UserManager 亦对 is_admin 隐藏删除按钮）。
    # 旧仅守「仅剩 1 admin」可删，现收紧为所有 admin 不可删——如需移除先撤销其管理员角色。
    # 对齐业界 RBAC 防 lockout（Azure 等原生阻止删最后 Owner，此处更严：admin 一律禁删）。
    if u.is_admin:
        raise ApiError("conflict", "不能删除管理员账号（请先撤销其管理员角色再删除）", status=409)

    # iter 365：取消该用户运行中的 chat run（同 delete_thread 模式）。删 user -> ORM 级联
    # 删 Thread/Run 行，但 _run_chat 后台任务仍持独立 SessionLocal 在跑——下次 persist 撞
    # 失效 FK 抛 IntegrityError 被吞，当轮 LLM 费用已发生却写不进 api_usage -> 成本黑洞。
    from app.api.memory import _RUN_STREAMS

    for r in db.query(models.Run).filter_by(user_id=u.id, status=models.RunStatus.running).all():
        stream = _RUN_STREAMS.get(r.id)
        if stream is not None:
            stream.request_cancel()
    # 级联清理：会话（ORM 级联 plans/materials/quizzes 等 + 文件目录）-> 供应商档案/用量 -> 用户
    session_ids = [s.id for s in db.query(models.Session).filter_by(user_id=u.id).all()]
    for sid in session_ids:
        cancel_running_generation(sid)  # 防僵尸编排器任务 _finalize 撞 FK（同 DELETE /sessions）
        sess = db.get(models.Session, sid)
        if sess is not None:
            db.delete(sess)
    db.query(models.LlmProviderProfile).filter_by(user_id=u.id).delete()
    db.query(models.ApiUsage).filter_by(user_id=u.id).delete()
    db.delete(u)
    db.commit()
    for sid in session_ids:  # 文件清理失败不阻塞 DB 已删
        try:
            delete_session_files(sid)
        except Exception:  # noqa: BLE001
            log.warning("删除用户 %s 的会话文件失败: session=%s", user_id, sid)
    try:  # iter 213：知识库文档目录级联清理（kb_documents 行已随 ORM 级联删除）
        delete_kb_files(u.id)
    except Exception:  # noqa: BLE001
        log.warning("删除用户 %s 的知识库文件失败", user_id)
    return {"ok": True}
