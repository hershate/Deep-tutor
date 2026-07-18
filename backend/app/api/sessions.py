"""会话 API（plan/03 §一 sessions）。

POST /api/sessions  创建会话
DELETE /api/sessions/{id}  删除会话（级联 DB + 文件）
GET /api/sessions/{id}/materials  会话源资料清单（iter 104 增强，plan/03 未定义）
DELETE /api/sessions/{id}/materials/{mid}  删除单个源资料（iter 104 增强：传错资料可移除后重生成）

流程微调（记录于 stage.md）：先建 session 再上传材料（materials.session_id NOT NULL，
免 schema 迁移且更稳健）；plan/03 原 POST /sessions {material_ids} 简化为 {title?}。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_session, require_auth
from app.api.errors import ApiError
from app.api.plans import cancel_running_generation
from app.core.storage import delete_material_files, delete_session_files
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_auth)])

# 每用户会话数上限（err.md A24：此前会话创建无配额，DB 行/文件目录无界增长）
_MAX_SESSIONS_PER_USER = 200


class SessionCreate(BaseModel):
    title: str = Field(default="", max_length=256)


class SessionOut(BaseModel):
    session_id: int
    title: str


@router.post("", response_model=SessionOut)
def create_session(
    body: SessionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SessionOut:
    count = db.query(models.Session).filter_by(user_id=user.id).count()
    if count >= _MAX_SESSIONS_PER_USER:
        raise ApiError(
            "quota_exceeded",
            f"会话数已达上限 {_MAX_SESSIONS_PER_USER} 个，请先删除不再需要的会话",
        )
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


# --- 会话源资料管理（iter 104 增强）：清单 + 单个删除 ---
# 场景：上传/粘贴错资料后此前只能整个会话删掉重来；现可单独移除，再重生成方案。
# 删除不影响已生成的方案/自学单元（源资料仅是生成的输入，无 FK 被引用）。


class SessionMaterialOut(BaseModel):
    material_id: int
    kind: str
    source: str  # 可读摘要：文件名 / URL / 文本前 200 字
    parsed_chars: int
    meta: dict
    created_at: datetime | None


@router.get("/{session_id}/materials")
def list_session_materials(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """会话源资料清单（按 id 升序）。P4 隔离：他人会话 404。"""
    sess = get_owned_session(db, session_id, user)
    rows = (
        db.query(models.Material)
        .filter_by(session_id=sess.id)
        .order_by(models.Material.id)
        .all()
    )
    items = [
        SessionMaterialOut(
            material_id=m.id,
            kind=m.kind.value if hasattr(m.kind, "value") else str(m.kind),
            source=m.source,
            parsed_chars=m.parsed_chars,
            meta=m.meta or {},
            created_at=m.created_at,
        ).model_dump()
        for m in rows
    ]
    return {"items": items, "total": len(items)}


@router.delete("/{session_id}/materials/{material_id}")
def delete_material(
    session_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """删除单个源资料（DB 行 + 原件/解析文本文件）。

    - 运行态 409：生成中删资料会与编排器读取竞争（同 plans.py R33 模式）。
    - 资料不存在/不属于该会话 -> 404（不泄露存在性）。
    - 文件清理失败不阻塞（DB 已删，与 delete_session 同策略）。
    """
    sess = get_owned_session(db, session_id, user)
    state = db.get(models.OrchestratorState, session_id)
    if state is not None and state.status == models.OrchestratorStatus.running:
        raise ApiError("conflict", "该会话正在生成中，请等待完成或取消后再删除资料", status=409)
    mat = db.get(models.Material, material_id)
    if mat is None or mat.session_id != sess.id:
        raise ApiError("not_found", f"资料 {material_id} 不存在")
    db.delete(mat)
    db.commit()
    try:
        delete_material_files(session_id, material_id)
    except Exception:  # noqa: BLE001  文件清理失败不阻塞 DB 已删
        pass
    return {"ok": True}
