"""独立笔记 API（iter 227 自由笔记本）：标题+正文，不依附方案/知识点，随建随记。

与 /knowledge/notes（知识点批注，挂知识节点）并存互补：那边是「这个知识点的笔记」，
这边是「我的笔记本」。安全约定同族：
- P4 按用户隔离：一切操作仅作用于当前用户的笔记；跨用户访问一律 404（不泄露存在性）。
- 每用户 ≤500 条（与 credentials/profiles 配额同族），超限 429。
- 标题与正文不可同时为空（防空壳笔记）；正文上限 20000 字（对齐 NodeNote _MAX_NOTE_CHARS）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/notes", tags=["notes"], dependencies=[Depends(require_auth)])

_MAX_NOTES_PER_USER = 500  # 每用户笔记条数上限（与其他资源配额同族）
_MAX_TITLE_CHARS = 256
_MAX_CONTENT_CHARS = 20_000
_LIST_PREVIEW_CHARS = 300  # 列表预览截断（完整内容走 GET /notes/{id}）


def _out(n: models.Note, *, preview: bool = False) -> dict:
    content = n.content or ""
    return {
        "id": n.id,
        "title": n.title,
        "content": content[:_LIST_PREVIEW_CHARS] if preview else content,
        "truncated": preview and len(content) > _LIST_PREVIEW_CHARS,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


def _owned_or_404(db: Session, note_id: int, user: User) -> models.Note:
    """当前用户拥有的笔记；不存在/属他人 -> 404（不泄露存在性）。"""
    n = db.get(models.Note, note_id)
    if n is None or n.user_id != user.id:
        raise ApiError("not_found", f"笔记 {note_id} 不存在")
    return n


class NoteIn(BaseModel):
    title: str = Field(default="", max_length=_MAX_TITLE_CHARS)
    content: str = Field(default="", max_length=_MAX_CONTENT_CHARS)


def _normalize(body: NoteIn) -> tuple[str, str]:
    title = body.title.strip()
    content = body.content.strip()
    if not title and not content:
        raise ApiError("bad_request", "标题与正文不可同时为空")
    return title, content


@router.get("")
def list_notes(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """笔记列表（按更新时间倒序），content 截断为 300 字符预览。"""
    rows = (
        db.query(models.Note)
        .filter_by(user_id=user.id)
        .order_by(models.Note.updated_at.desc(), models.Note.id.desc())
        .all()
    )
    return {"items": [_out(n, preview=True) for n in rows], "total": len(rows)}


@router.post("", status_code=201)
def create_note(body: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    title, content = _normalize(body)
    if db.query(models.Note).filter_by(user_id=user.id).count() >= _MAX_NOTES_PER_USER:
        raise ApiError("quota_exceeded", f"笔记数已达上限 {_MAX_NOTES_PER_USER} 条，请先删除不再需要的笔记")
    note = models.Note(user_id=user.id, title=title, content=content)
    db.add(note)
    db.commit()
    db.refresh(note)
    return _out(note)


@router.get("/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return _out(_owned_or_404(db, note_id, user))


@router.put("/{note_id}")
def update_note(
    note_id: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    note = _owned_or_404(db, note_id, user)
    note.title, note.content = _normalize(body)
    db.commit()
    db.refresh(note)
    return _out(note)


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    db.delete(_owned_or_404(db, note_id, user))
    db.commit()
    return {"ok": True}
