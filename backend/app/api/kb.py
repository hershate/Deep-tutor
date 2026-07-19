"""知识库 API（iter 213：LLMWiki 式独立文档库 /kb）。

用户直接上传数据到知识库（不依赖学习方案），库内可直接查看原件或提供给 AI。
存储：原件 data/kb/<user_id>/originals/，解析文本 parsed/，DB（kb_documents）存索引
（plan/01 §五）。安全件复用 materials 上传管线：_read_capped 限量读取（防 OOM）、
_validate_upload 扩展名+magic bytes（R20）、_hash_source 内容哈希去重（plan/11 §九）、
SSRF 防护随 parse_material（verify_url._is_safe_url）、URL 抓取与 materials/clip
共享 `url:{uid}` 限流桶（plan/09 §十八）。

路由顺序：静态段（upload/text/url）先于 {document_id} 声明——int 路径参数不匹配
非数字段，但 FastAPI 按声明序匹配模板，静态段在前杜绝歧义。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.context import ExecContext
from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.api.materials import _URL_RATE_PER_MIN, _hash_source, _read_capped, _validate_upload
from app.core.ratelimit import limiter
from app.core.settings import settings
from app.core.storage import delete_kb_document_files, save_kb_upload, write_kb_parsed_text
from app.db import models
from app.db.models import User
from app.tools.base import ToolInput
from app.tools.parse_material import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, parse_material

log = logging.getLogger("deep_tutor.api.kb")

router = APIRouter(prefix="/kb", tags=["kb"], dependencies=[Depends(require_auth)])

# 配额（沿用 plan/09 §二十 思路）：每用户知识库文档数上限
_MAX_KB_DOCS_PER_USER = 200


def _ctx(user_id: int) -> ExecContext:
    return ExecContext(session_id=f"kb-{user_id}", trace_id=f"kb-doc-{user_id}")


def _kind_for_ext(ext: str) -> models.MaterialKind:
    """按扩展名归类（IMAGE/AUDIO/VIDEO 各自归族，其余归 file；ext 带点如 ".pdf"）。"""
    if ext in IMAGE_EXTS:
        return models.MaterialKind.image
    if ext in AUDIO_EXTS:
        return models.MaterialKind.audio
    if ext in VIDEO_EXTS:
        return models.MaterialKind.video
    return models.MaterialKind.file


def _doc_out(d: models.KbDocument, *, deduped: bool = False) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "kind": d.kind.value if hasattr(d.kind, "value") else str(d.kind),
        "ext": d.ext,
        "source": d.source,
        "has_original": d.original_path is not None,
        "parsed_chars": d.parsed_chars,
        "meta": {**(d.meta or {}), **({"deduped": True} if deduped else {})},
        "content_hash": d.content_hash,
        "created_at": str(d.created_at),
        "updated_at": str(d.updated_at),
    }


def _owned_doc_or_404(db: Session, document_id: int, user: User) -> models.KbDocument:
    """当前用户拥有的知识库文档；不存在或属他人 -> 404（防枚举他人数据 id）。"""
    doc = db.get(models.KbDocument, document_id)
    if doc is None or doc.user_id != user.id:
        raise ApiError("not_found", f"知识库文档 {document_id} 不存在")
    return doc


def _check_kb_quota(db: Session, user_id: int) -> None:
    """每用户知识库文档数 ≤200；超限 -> 429 quota_exceeded。"""
    count = db.scalar(
        select(func.count(models.KbDocument.id)).where(models.KbDocument.user_id == user_id)
    ) or 0
    if count >= _MAX_KB_DOCS_PER_USER:
        raise ApiError(
            "quota_exceeded",
            f"知识库文档数已达上限 {_MAX_KB_DOCS_PER_USER} 篇，请先删除不再需要的文档",
        )


def _dedup_lookup(db: Session, user_id: int, content_hash: str) -> models.KbDocument | None:
    """同用户相同内容哈希的已解析文档 -> 直接复用（plan/11 §九），不重复解析/扣配额。"""
    return db.scalar(
        select(models.KbDocument).where(
            models.KbDocument.user_id == user_id,
            models.KbDocument.content_hash == content_hash,
            models.KbDocument.parsed_text_path.is_not(None),
        )
    )


def _persist_parsed(db: Session, user_id: int, doc: models.KbDocument, text: str) -> models.KbDocument:
    """解析文本落盘 + 提交（失败回滚，防孤儿行/文件，同 materials err.md A15）。"""
    try:
        doc.parsed_text_path = str(write_kb_parsed_text(user_id, doc.id, text))
        doc.parsed_chars = len(text)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(doc)
    return doc


# --- 创建（静态段先于 {document_id} 声明） ---


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """上传文件到知识库：限量读取 -> 扩展名/magic 校验 -> 去重 -> 配额 -> 解析 -> 落盘。"""
    content = await _read_capped(file, settings.max_upload_bytes)
    ext = _validate_upload(file.filename or "", content)
    filename = (file.filename or "")[:1024]

    # 内容哈希去重（plan/11 §九）：同用户重复上传同一文件 -> 复用已解析结果
    content_hash = _hash_source(content)
    existing = _dedup_lookup(db, user.id, content_hash)
    if existing is not None:
        return _doc_out(existing, deduped=True)
    _check_kb_quota(db, user.id)

    # 先建行拿 id，再用 id 命名保存原件（R20：不使用原始文件名）
    doc = models.KbDocument(
        user_id=user.id,
        title=filename[:256],
        kind=_kind_for_ext(ext),
        ext=ext.lstrip("."),
        source=filename,
        parsed_chars=0,
        meta={},
        content_hash=content_hash,
    )
    db.add(doc)
    db.flush()
    path = save_kb_upload(user.id, doc.id, content, ext)

    out = await parse_material(
        ToolInput(
            tool="parse_material",
            session_id=f"kb-{user.id}",
            args={"kind": "file", "source": str(path)},
        ),
        _ctx(user.id),
    )
    if out.status == "error" and out.errors:
        db.rollback()
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise ApiError(out.errors[0].code, out.errors[0].message)

    text = out.data.get("text", "")
    try:
        doc.original_path = str(path)
        doc.parsed_text_path = str(write_kb_parsed_text(user.id, doc.id, text))
        doc.parsed_chars = len(text)
        doc.meta = out.data.get("meta", {})
        db.commit()
    except Exception:
        # err.md A15：解析文本落盘失败需回滚 DB 并清理原件，防孤儿文件
        db.rollback()
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    db.refresh(doc)
    return _doc_out(doc)


class KbTextIn(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    text: str = Field(max_length=2_000_000)  # 与 materials TextIn 同上限（2MB），防内存 DoS


@router.post("/documents/text")
async def create_text_document(
    body: KbTextIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """纯文本入库：无原件（original_path=None），解析文本即原文（经 500k 截断护栏）。"""
    content = body.text.strip()
    if not content:
        raise ApiError("empty_content", "文本为空")
    content_hash = _hash_source(body.text)
    existing = _dedup_lookup(db, user.id, content_hash)
    if existing is not None:
        return _doc_out(existing, deduped=True)
    _check_kb_quota(db, user.id)

    out = await parse_material(
        ToolInput(tool="parse_material", session_id=f"kb-{user.id}", args={"kind": "text", "source": body.text}),
        _ctx(user.id),
    )
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    text = out.data.get("text", "")
    title = (body.title or "").strip() or content.split("\n", 1)[0][:40]
    doc = models.KbDocument(
        user_id=user.id,
        title=title[:256],
        kind=models.MaterialKind.text,
        ext="txt",
        source=content[:200],  # 可读摘要（同 materials src_field）
        parsed_chars=0,
        meta=out.data.get("meta", {}),
        content_hash=content_hash,
    )
    db.add(doc)
    db.flush()
    return _doc_out(_persist_parsed(db, user.id, doc, text))


class KbUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)  # err.md A5：上限同 materials UrlIn
    title: str | None = Field(default=None, max_length=256)


@router.post("/documents/url")
async def create_url_document(
    body: KbUrlIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """网页链接抓取入库：SSRF 随 parse_material；与 materials/clip 共享 url 限流桶。"""
    if not limiter.check(f"url:{user.id}", _URL_RATE_PER_MIN):
        raise ApiError("rate_limited", f"网页抓取限每分钟 {_URL_RATE_PER_MIN} 次，请稍后再试")
    url = body.url.strip()
    content_hash = _hash_source(url)
    existing = _dedup_lookup(db, user.id, content_hash)
    if existing is not None:
        return _doc_out(existing, deduped=True)
    _check_kb_quota(db, user.id)

    out = await parse_material(
        ToolInput(tool="parse_material", session_id=f"kb-{user.id}", args={"kind": "url", "source": url}),
        _ctx(user.id),
    )
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    text = out.data.get("text", "")
    title = (body.title or "").strip() or url[:80]
    doc = models.KbDocument(
        user_id=user.id,
        title=title[:256],
        kind=models.MaterialKind.url,
        ext="",
        source=url[:1024],
        parsed_chars=0,
        meta=out.data.get("meta", {}),
        content_hash=content_hash,
    )
    db.add(doc)
    db.flush()
    return _doc_out(_persist_parsed(db, user.id, doc, text))


# --- 列表 / 详情 / 重命名 / 删除 ---


@router.get("/documents")
def list_documents(
    q: str | None = Query(default=None, max_length=100),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """文档列表（新→旧）：q=标题子串；kind 枚举过滤（非法 -> 400）；limit ≤1000。"""
    stmt = select(models.KbDocument).where(models.KbDocument.user_id == user.id)
    if q and q.strip():
        stmt = stmt.where(models.KbDocument.title.contains(q.strip()))
    if kind:
        if kind not in {k.value for k in models.MaterialKind}:
            raise ApiError("bad_request", f"非法 kind: {kind}（支持 file/text/url/image/audio/video）", status=400)
        stmt = stmt.where(models.KbDocument.kind == kind)
    rows = db.scalars(stmt.order_by(models.KbDocument.id.desc()).limit(limit)).all()
    return {"documents": [_doc_out(d) for d in rows]}


@router.get("/documents/{document_id}")
def get_document(
    document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    return _doc_out(_owned_doc_or_404(db, document_id, user))


class KbRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)


@router.put("/documents/{document_id}")
def rename_document(
    document_id: int,
    body: KbRenameIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    doc = _owned_doc_or_404(db, document_id, user)
    title = body.title.strip()
    if not title:
        raise ApiError("bad_request", "标题不能为空", status=400)
    doc.title = title
    db.commit()
    db.refresh(doc)
    return _doc_out(doc)


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """删除文档：先删行提交，再尽力清磁盘文件（文件清理失败不阻塞，幂等）。"""
    doc = _owned_doc_or_404(db, document_id, user)
    db.delete(doc)
    db.commit()
    try:
        delete_kb_document_files(user.id, document_id)
    except Exception:  # noqa: BLE001
        log.warning("删除知识库文档文件失败: user=%s doc=%s", user.id, document_id)
    return {"ok": True}
