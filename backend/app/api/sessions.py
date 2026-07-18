"""会话 API（plan/03 §一 sessions）。

POST /api/sessions  创建会话
DELETE /api/sessions/{id}  删除会话（级联 DB + 文件）

流程微调（记录于 stage.md）：先建 session 再上传材料（materials.session_id NOT NULL，
免 schema 迁移且更稳健）；plan/03 原 POST /sessions {material_ids} 简化为 {title?}。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_session, require_auth
from app.api.plans import cancel_running_generation
from app.core.storage import delete_session_files
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_auth)])


class SessionCreate(BaseModel):
    title: str = Field(default="", max_length=256)


class SessionOut(BaseModel):
    session_id: int
    title: str


@router.post("", response_model=SessionOut)
def create_session(
    body: SessionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SessionOut:
    sess = models.Session(user_id=user.id, title=body.title)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return SessionOut(session_id=sess.id, title=sess.title)


@router.delete("/{session_id}")
def delete_session(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    sess = get_owned_session(db, session_id, user)  # P4 隔离：他人会话 404（不泄露存在性）
    # running 态：先 cancel 编排器任务再删（plan/10 §十一），防僵尸任务 _finalize 撞 FK
    cancel_running_generation(session_id)
    # 级联：DB 行（relationship cascade=all,delete-orphan）+ 文件目录
    db.delete(sess)
    db.commit()
    try:
        delete_session_files(session_id)
    except Exception:  # noqa: BLE001  文件清理失败不阻塞 DB 已删
        pass
    return {"ok": True}
