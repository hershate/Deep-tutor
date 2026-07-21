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

import asyncio
import logging
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import BudgetTracker, run_tool
from app.agent.context import ExecContext, Timeouts
from app.agent.cost import record_metadata_usage
from app.api.deps import check_daily_budget, get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.api.materials import _URL_RATE_PER_MIN, _hash_source, _read_capped, _validate_upload
from app.api.plans import _safe_download_name
from app.api.sessions import _MAX_SESSIONS_PER_USER
from app.core.ratelimit import limiter
from app.core.locks import drop_lock
from app.core.settings import settings
from app.core.storage import (
    _kb_root,
    delete_kb_document_files,
    delete_session_files,
    kb_originals_dir,
    kb_parsed_dir,
    save_kb_upload,
    write_kb_parsed_text,
    write_parsed_text,
)
from app.db import models
from app.db.models import User
from app.llm import get_provider
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.kb_answer import KbAnswerSpec
from app.tools.parse_material import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, parse_material

log = logging.getLogger("deep_tutor.api.kb")

router = APIRouter(prefix="/kb", tags=["kb"], dependencies=[Depends(require_auth)])

# 配额（沿用 plan/09 §二十 思路）：每用户知识库文档数上限
_MAX_KB_DOCS_PER_USER = 200

# iter 247：上传限流（次/分钟/用户）——单文件上限 50MB 已由 _read_capped 保证，
# 但并发 N 个上传的内存叠加此前无防（多用户部署 OOM 面）
_UPLOAD_RATE_PER_MIN = 20

# per-doc 解析互斥锁（iter 225）：同文档并发解析 -> 409；不同文档可并行（事务外解析 +
# 短写事务，err.md B22）。锁对象只增不减，每文档一个（~百字节），不做淘汰。
_PARSE_LOCKS: dict[int, asyncio.Lock] = {}


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
        "status": d.status,  # iter 225：pending 待确认 / confirmed 已添加未解析 / parsed 已解析
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
    """同用户相同内容哈希的文档 -> 直接复用（plan/11 §九），不重复建行/扣配额。

    iter 225：不再限已解析——pending 暂存/confirmed 未解析行同样复用（重复上传
    同一文件返回该行，前端按其 status 展示，不重复占配额）。
    """
    return db.scalar(
        select(models.KbDocument).where(
            models.KbDocument.user_id == user_id,
            models.KbDocument.content_hash == content_hash,
        )
    )


def _persist_parsed(db: Session, user_id: int, doc: models.KbDocument, text: str) -> models.KbDocument:
    """解析文本落盘 + 提交（失败回滚，防孤儿行/文件，同 materials err.md A15）。"""
    path = None
    try:
        path = write_kb_parsed_text(user_id, doc.id, text)
        doc.parsed_text_path = str(path)
        doc.parsed_chars = len(text)
        db.commit()
    except Exception:
        db.rollback()
        # iter 247：commit 失败（磁盘满/库锁）时清理已落盘的解析文本——此前只回滚行，
        # 文件成孤儿（行已不存在，无任何清理路径引用它），注释与行为不符
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    db.refresh(doc)
    return doc


# --- 创建（静态段先于 {document_id} 声明） ---


def _fresh_doc_id(user_id: int) -> str:
    """生成无冲突的随机文档 id（iter 228 命名冲突防护）。

    uuid4 截断 12 位（48bit）生日界理论可撞——撞名会**静默覆盖同用户既有原件**
    （save_kb_upload 直接 write_bytes）。生成后做存在性检查（原件 glob + 解析文本），
    撞名重摇 ≤8 次，仍撞则退化为全 32 位 hex（实际不可达，纯兜底）。
    """
    originals = kb_originals_dir(user_id)
    parsed = kb_parsed_dir(user_id)
    for _ in range(8):
        cand = uuid.uuid4().hex[:12]
        if not any(originals.glob(f"{cand}.*")) and not (parsed / f"{cand}.txt").exists():
            return cand
    return uuid.uuid4().hex


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """上传文件到知识库（iter 225 上传确认流程）：校验 -> 去重 -> 配额 -> 落原件 +
    建 pending 暂存行，**不做任何解析**——是否转换/怎么转换由用户在确认后调
    /documents/{id}/parse 决定（全异步可控，不再上传即默认解析）。
    """
    # iter 247：上传限流——此前无防，多用户部署下认证用户并行 N 个 50MB 上传
    # 内存峰值 N×50MB 可 OOM 单进程（_read_capped 只限单请求内存）
    if not limiter.check(f"kb_upload:{user.id}", _UPLOAD_RATE_PER_MIN):
        raise ApiError("rate_limited", f"上传限每分钟 {_UPLOAD_RATE_PER_MIN} 次，请稍后再试")
    content = await _read_capped(file, settings.max_upload_bytes)
    ext = _validate_upload(file.filename or "", content)
    filename = (file.filename or "")[:1024]

    # 内容哈希去重（plan/11 §九）：同用户重复上传同一文件 -> 复用已有行（含 pending 暂存）
    content_hash = _hash_source(content)
    existing = _dedup_lookup(db, user.id, content_hash)
    if existing is not None:
        return _doc_out(existing, deduped=True)
    _check_kb_quota(db, user.id)

    # 随机 id 预命名原件（R20 不用原始文件名；err.md B22 不依赖行 id）
    doc_id = _fresh_doc_id(user.id)
    # iter 247：大文件写盘移出事件循环（50MB 同步 write 阻塞数百毫秒，期间全服务无响应）
    path = await asyncio.to_thread(save_kb_upload, user.id, doc_id, content, ext)
    doc = models.KbDocument(
        user_id=user.id,
        title=filename[:256],
        kind=_kind_for_ext(ext),
        ext=ext.lstrip("."),
        source=filename,
        original_path=str(path),
        parsed_chars=0,
        meta={},
        content_hash=content_hash,
        status="pending",  # 暂存待确认：计配额、可确认/删除
    )
    db.add(doc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)  # 防孤儿原件（err.md A15）
        raise
    db.refresh(doc)
    return _doc_out(doc)


@router.post("/documents/{document_id}/confirm")
def confirm_document(
    document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """确认添加到知识库：pending（暂存待确认）-> confirmed（已添加，未解析）。幂等。"""
    doc = _owned_doc_or_404(db, document_id, user)
    if doc.status == "pending":
        doc.status = "confirmed"
        db.commit()
        db.refresh(doc)
    return _doc_out(doc)


class KbParseIn(BaseModel):
    # 解析质量档（iter 225）：仅 PDF 有别——full=marker（高质量/扫描件 OCR，慢）/ fast=pypdf（秒级纯文本）
    mode: Literal["full", "fast"] = "full"


@router.post("/documents/{document_id}/parse")
async def parse_document(
    document_id: int,
    body: KbParseIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """按需解析/转换文档（iter 225：用户确认后触发，是否转换与方式由用户决定）。

    pending/confirmed -> parsed；pending 解析隐含确认。已 parsed 可重解析换档
    （覆盖解析文本）。解析失败**行保留状态不变**（用户可换档重试），不删文档。
    事务外解析（err.md B22）；同文档并发解析 -> 409。
    """
    mode = (body.mode if body else "full")
    doc = _owned_doc_or_404(db, document_id, user)
    if doc.original_path is None:
        raise ApiError("bad_request", "该文档无原件（text/url 类创建时已解析入库）", status=400)
    source_path = doc.original_path
    # iter 247：解析完成后据此校验重取行身份——解析窗口（最长 600s）内文档被删且
    # rowid 复用（SQLite 最大 id 行删除后复用）时，重取到的会是新文档，须拒写
    pre_content_hash = doc.content_hash
    db.rollback()  # 释放 SELECT 事务：解析期间不持有任何 DB 锁（err.md B22）

    lock = _PARSE_LOCKS.setdefault(document_id, asyncio.Lock())
    if lock.locked():
        raise ApiError("conflict", "该文档正在解析中，请稍候", status=409)
    try:
        async with lock:
            try:
                # 直调路径不走 run_tool——Timeouts.parse 外层兜底在此补齐（同 materials.py）
                out = await asyncio.wait_for(
                    parse_material(
                        ToolInput(
                            tool="parse_material",
                            session_id=f"kb-{user.id}",
                            args={"kind": "file", "source": source_path, "parse_mode": mode},
                        ),
                        _ctx(user.id),
                    ),
                    timeout=Timeouts().parse,
                )
            except TimeoutError:
                raise ApiError("upstream_timeout", f"解析超时（{int(Timeouts().parse)}s），请重试或换档") from None
            if out.status == "error" and out.errors:
                raise ApiError(out.errors[0].code, out.errors[0].message)
            text = out.data.get("text", "")
            stem = Path(source_path).stem  # 解析文本与原件同名（.txt），随删除端点按行内路径清理
            parsed_path = write_kb_parsed_text(user.id, stem, text)
            try:
                doc = _owned_doc_or_404(db, document_id, user)  # rollback 后重取实例
            except ApiError:
                # iter 247：解析期间文档被删 -> 刚写出的解析文件无人引用，清理防孤儿
                parsed_path.unlink(missing_ok=True)
                raise
            if doc.content_hash != pre_content_hash or doc.original_path != source_path:
                # iter 247：rowid 复用——重取到的是解析期间新建的另一篇文档，
                # 解析文本不可错绑（原件与解析内容对不上），丢弃并提示重试
                parsed_path.unlink(missing_ok=True)
                raise ApiError("conflict", "文档在解析期间已变更，请重试", status=409)
            doc.parsed_text_path = str(parsed_path)
            doc.parsed_chars = len(text)
            doc.meta = {**(doc.meta or {}), **out.data.get("meta", {})}
            doc.status = "parsed"
            try:
                db.commit()
            except Exception:
                db.rollback()
                parsed_path.unlink(missing_ok=True)  # iter 247：同 _persist_parsed 防孤儿
                raise
            db.refresh(doc)
            result = _doc_out(doc)
    finally:
        drop_lock(_PARSE_LOCKS, document_id, lock)  # 与锁释放间无 await（iter 232 台账有界）
    return result


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
    """文档列表（新→旧）：q=标题**或解析文本内容**子串（iter 255 扩展——内容检索
    有界扫描最近 _PARSED_SCAN_MAX_DOCS 篇已解析文档，标题命中在前、内容命中在后）；
    kind 枚举过滤（非法 -> 400）；limit ≤1000。"""
    stmt = select(models.KbDocument).where(models.KbDocument.user_id == user.id)
    kind_filter: models.MaterialKind | None = None
    if kind:
        if kind not in {k.value for k in models.MaterialKind}:
            raise ApiError("bad_request", f"非法 kind: {kind}（支持 file/text/url/image/audio/video）", status=400)
        kind_filter = models.MaterialKind(kind)
        stmt = stmt.where(models.KbDocument.kind == kind_filter)
    if q and q.strip():
        qs = q.strip()
        title_rows = list(
            db.scalars(
                stmt.where(models.KbDocument.title.contains(qs)).order_by(models.KbDocument.id.desc()).limit(limit)
            ).all()
        )
        hits = content_search_docs(db, user.id, qs, limit - len(title_rows), {d.id for d in title_rows}, kind=kind_filter)
        rows = title_rows + [d for d, _ in hits]
    else:
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
    """删除文档：先删行提交，再尽力清磁盘文件（文件清理失败不阻塞，幂等）。

    err.md B22：文件名不再等于行 id（上传改随机 id 预命名）——按行内记录的路径删，
    再按 id 前缀 glob 兜底清理旧命名/孤儿残留。
    """
    doc = _owned_doc_or_404(db, document_id, user)
    # iter 247：解析进行中禁止删除——删除会让在途解析的结果错绑（rowid 复用）或
    # 写出孤儿解析文件；409 给用户明确信号（等解析完再删）
    lock = _PARSE_LOCKS.get(document_id)
    if lock is not None and lock.locked():
        raise ApiError("conflict", "该文档正在解析中，请解析完成后再删除", status=409)
    paths = [doc.original_path, doc.parsed_text_path]
    db.delete(doc)
    db.commit()
    for p in paths:
        if not p:
            continue
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            log.warning("删除知识库文档文件失败: %s", p)
    try:
        delete_kb_document_files(user.id, document_id)
    except Exception:  # noqa: BLE001
        log.warning("删除知识库文档文件失败: user=%s doc=%s", user.id, document_id)
    return {"ok": True}


# --- 原件 / 解析文本读取（iter 214） ---

# 查看器内联渲染的 media_type 静态映射；docx/pptx 等浏览器不支持内联 -> octet-stream（下载）
_RAW_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
}


@router.get("/documents/{document_id}/raw")
def get_document_raw(
    document_id: int,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """原件读取（单端点双模式）：默认 inline 供前端查看器（PDF/图片/音频/视频浏览器原生
    渲染），?download=1 为 attachment 下载。text/url 类无原件 -> 404。"""
    doc = _owned_doc_or_404(db, document_id, user)
    if not doc.original_path:
        raise ApiError("not_found", f"知识库文档 {document_id} 无原件（text/url 类仅有解析文本）")
    # R20 防线：DB 路径 resolve 后须仍在本用户 kb 目录内（防库被篡改后路径穿越）
    try:
        resolved = Path(doc.original_path).resolve()
        resolved.relative_to(_kb_root(user.id))
    except (ValueError, OSError):
        log.warning("知识库原件路径越界: doc=%s", document_id)
        raise ApiError("not_found", f"知识库文档 {document_id} 的原件缺失") from None
    if not resolved.is_file():
        raise ApiError("not_found", f"知识库文档 {document_id} 的原件缺失")
    media_type = _RAW_MEDIA_TYPES.get(doc.ext.lower(), "application/octet-stream")
    filename = doc.title or f"kb-{doc.id}"
    if download and doc.ext and not filename.lower().endswith(f".{doc.ext.lower()}"):
        filename = f"{filename}.{doc.ext}"  # 重命名后标题可能无扩展名，下载名补齐
    return FileResponse(
        resolved,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/documents/{document_id}/parsed")
def get_document_parsed(
    document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """解析文本（AI 消费面 / 查看器文档类渲染源）：缺失 -> 404，不回显服务器路径（R35）。"""
    doc = _owned_doc_or_404(db, document_id, user)
    if not doc.parsed_text_path:
        raise ApiError("not_found", f"知识库文档 {document_id} 无解析文本")
    try:
        text = Path(doc.parsed_text_path).read_text(encoding="utf-8")
    except OSError:
        log.warning("知识库解析文本缺失: doc=%s", document_id)
        raise ApiError("not_found", f"知识库文档 {document_id} 的解析文本缺失") from None
    return {
        "document_id": doc.id,
        "title": doc.title,
        "text": text,
        "parsed_chars": doc.parsed_chars,
        "truncated": bool((doc.meta or {}).get("truncated")),
    }


# --- ZIP 打包导出（iter 214） ---


def _export_stem(doc: models.KbDocument) -> str:
    """zip 条目名主干：<safe_title>（标题自带扩展名时先去掉，防 a.md.md 双扩展名）。"""
    title = doc.title or f"doc-{doc.id}"
    if doc.ext and title.lower().endswith(f".{doc.ext.lower()}"):
        title = title[: -(len(doc.ext) + 1)]
    return _safe_download_name(title)


def _export_index_md(docs: list[models.KbDocument]) -> str:
    lines = [
        "# 知识库导出清单",
        "",
        f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 文档数：{len(docs)}",
        "",
        "| ID | 标题 | 类型 | 扩展名 | 解析字数 | 原件 | 创建时间 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in docs:
        kind = d.kind.value if hasattr(d.kind, "value") else str(d.kind)
        lines.append(
            f"| {d.id} | {d.title.replace('|', '/')} | {kind} | {d.ext or '-'} "
            f"| {d.parsed_chars} | {'有' if d.original_path else '无'} | {d.created_at} |"
        )
    lines.append("")
    return "\n".join(lines)


@router.get("/export")
def export_kb_zip(
    ids: str | None = Query(default=None, max_length=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """知识库打包导出（ZIP）：index.md 清单 + originals/ + parsed/。

    ids=1,2,3 过滤（缺省全库；他人/不存在 id 静默排除——导出是批量便利操作而非查询）。
    空库或过滤后为空 -> 404。流式：SpooledTemporaryFile(64MB) 防大库 OOM
    （相对 materials export BytesIO 范本的有依据偏离——200 文档×50MB 极端下整个 zip
    压进内存会 OOM），原件经 zf.open+copyfileobj(1MB) 流式写入。
    """
    stmt = select(models.KbDocument).where(models.KbDocument.user_id == user.id)
    if ids:
        wanted = {int(p) for p in (p.strip() for p in ids.split(",")) if p.isdigit()}
        stmt = stmt.where(models.KbDocument.id.in_(wanted)) if wanted else stmt.where(models.KbDocument.id == -1)
    docs = db.scalars(stmt.order_by(models.KbDocument.id)).all()
    if not docs:
        raise ApiError("not_found", "知识库为空，无可导出文档")

    spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.md", _export_index_md(docs))
        for doc in docs:
            stem = f"{doc.id}-{_export_stem(doc)}"
            for arc_dir, path_str, suffix in (
                ("originals", doc.original_path, doc.ext or "bin"),
                ("parsed", doc.parsed_text_path, "txt"),
            ):
                if not path_str:
                    continue
                src = Path(path_str)
                if not src.is_file():
                    log.warning("知识库导出：文档 %s 文件缺失 %s", doc.id, arc_dir)
                    continue
                with src.open("rb") as fin, zf.open(f"{arc_dir}/{stem}.{suffix}", mode="w") as fout:
                    shutil.copyfileobj(fin, fout, 1024 * 1024)
    spool.seek(0)

    def _iter_chunks():
        try:
            while chunk := spool.read(64 * 1024):
                yield chunk
        finally:
            spool.close()

    filename = f"知识库导出-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    log.info("知识库导出：user=%s 文档=%d", user.id, len(docs))
    return StreamingResponse(
        _iter_chunks(),
        media_type="application/zip",
        # RFC 5987：中文文件名经 filename* 百分号编码（UA 不支持时回退 ASCII 名）
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"kb-export-{user.id}.zip\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


# --- 一键生成学习方案（iter 215） ---


class KbGenerateIn(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=256)


@router.post("/generate")
def generate_from_documents(
    body: KbGenerateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """一键用库内文档生成学习方案：建会话 + 逐文档复制解析文本为会话资料（Material），
    返回 /generate/{sid} 路径——前端打开即走既有生成管线（零改动复用
    plans._load_parsed_materials 注入链，资料总量 100k 截断护栏同既有）。

    逐行仿 materials clip 孤儿清理：归属校验（缺失/他人 -> 404）-> 会话配额 ->
    建 Session -> 逐文档读 parsed 全文 -> 建 Material 行（meta 带 kb_document_id 溯源）
    -> write_parsed_text 复制到会话目录 -> 一次 commit；任一异常回滚 +
    delete_session_files 上抛。不复制原件（会话材料仅需解析文本注入）。
    """
    # 请求内 id 去重（保序），防同一文档重复建料
    ordered_ids = list(dict.fromkeys(body.document_ids))
    docs_by_id = {
        d.id: d
        for d in db.scalars(
            select(models.KbDocument).where(
                models.KbDocument.user_id == user.id, models.KbDocument.id.in_(ordered_ids)
            )
        ).all()
    }
    for doc_id in ordered_ids:
        if doc_id not in docs_by_id:
            raise ApiError("not_found", f"知识库文档 {doc_id} 不存在")

    # 会话数配额（与 POST /sessions、materials clip 同口径）
    if db.query(models.Session).filter_by(user_id=user.id).count() >= _MAX_SESSIONS_PER_USER:
        raise ApiError(
            "quota_exceeded",
            f"会话数已达上限 {_MAX_SESSIONS_PER_USER} 个，请先删除不再需要的会话",
        )

    first = docs_by_id[ordered_ids[0]]
    title = (body.title or "").strip() or f"知识库：{first.title}"
    sess = models.Session(user_id=user.id, title=title[:256])
    db.add(sess)
    db.flush()
    material_count = 0
    try:
        seen_hashes: set[str] = set()
        for doc_id in ordered_ids:
            doc = docs_by_id[doc_id]
            # 请求内内容哈希去重（正常库内同哈希文档已被创建期去重拦截，此为双保险）
            if doc.content_hash and doc.content_hash in seen_hashes:
                continue
            if doc.content_hash:
                seen_hashes.add(doc.content_hash)
            if doc.status != "parsed":
                # iter 225：暂存/未解析文档无文本可注入，引导用户先完成确认+解析
                raise ApiError("not_found", f"知识库文档 {doc.id} 尚未解析——请先在文档列表中确认并解析")
            if not doc.parsed_text_path:
                raise ApiError("not_found", f"知识库文档 {doc.id} 的解析文本缺失")
            try:
                text = Path(doc.parsed_text_path).read_text(encoding="utf-8")
            except OSError:
                raise ApiError("not_found", f"知识库文档 {doc.id} 的解析文本缺失") from None
            material = models.Material(
                session_id=sess.id,
                kind=doc.kind,
                source=doc.source,
                parsed_chars=len(text),
                meta={**(doc.meta or {}), "kb_document_id": doc.id},
                content_hash=doc.content_hash,
            )
            db.add(material)
            db.flush()
            material.parsed_text_path = str(write_parsed_text(sess.id, material.id, text))
            material_count += 1
        db.commit()
    except Exception:
        # 任一失败：回滚（会话/资料行随回滚消失）+ 清会话目录，防孤儿
        db.rollback()
        try:
            delete_session_files(sess.id)
        except Exception:  # noqa: BLE001
            pass
        raise
    return {
        "session_id": sess.id,
        "open_path": f"/generate/{sess.id}",
        "material_count": material_count,
    }


# --- 知识库问答（iter 216） ---

# 端点侧显式预截断（对齐 loop.py prompt 守卫：字段级 >20k 静默截断、总量 >200k 硬截断——
# 预截断带标注，让截断行为对用户/模型可见而非静默）
_CHAT_DOC_CHARS = 19_900  # 单文档注入上限（+截断标注后仍 < loop._MAX_PROMPT_FIELD_CHARS=20k）
_CHAT_TOTAL_CHARS = 150_000  # 全部文档注入总量上限（loop._MAX_PROMPT_CHARS=200k 内留余量）
_CHAT_DEFAULT_DOCS = 20  # 缺省上下文：最近 20 篇已解析文档


class KbChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[int] | None = Field(default=None, max_length=20)  # None -> 缺省全库最近 20 篇


def _read_doc_text(doc: models.KbDocument) -> str:
    """读文档解析文本；缺失/读失败 -> ""（调用方归入 omitted）。"""
    if not doc.parsed_text_path:
        return ""
    try:
        return Path(doc.parsed_text_path).read_text(encoding="utf-8")
    except OSError:
        log.warning("知识库问答：文档 %s 解析文本缺失", doc.id)
        return ""


_PARSED_SCAN_MAX_DOCS = 50  # 内容检索扫描上界（最近 N 篇已解析文档）——更老文档仅标题可搜（诚实边界）


def _hit_snippet(text: str, qs: str, ctx: int = 60) -> str:
    """围绕首个命中（大小写不敏感）截取单行上下文片段（±ctx 字符，省略号标截断）。"""
    idx = text.lower().find(qs.lower())
    if idx < 0:
        return ""
    start = max(0, idx - ctx)
    end = min(len(text), idx + len(qs) + ctx)
    body = " ".join(text[start:end].split())  # 解析文本为 markdown，折叠换行/多余空白为单行
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def content_search_docs(
    db: Session,
    user_id: int,
    qs: str,
    limit: int,
    exclude_ids: set[int] | None = None,
    kind: models.MaterialKind | None = None,
) -> list[tuple[models.KbDocument, str]]:
    """有界解析文本内容检索（iter 255）：按新→旧扫描当前用户最近 _PARSED_SCAN_MAX_DOCS 篇
    已解析文档，命中返回 [(doc, 命中片段)]（≤limit，exclude_ids 跳过标题已命中文档，
    kind 非空时与列表端点同口径过滤类型）。

    诚实边界：不建索引——FTS5 需库内存全文副本，与 plan/09 §一「大内容入文件系统」
    存储策略冲突（trigram 索引 2-3 倍体积，见 stage.md iter 253 评估）；仅覆盖最近 N 篇，
    更老文档只有标题可搜。逐篇同步文件读（≤N×500KB，解析截断上限），调用方须为
    同步 def 端点（FastAPI 线程池执行，不阻塞事件循环）。
    """
    if limit <= 0 or not qs:
        return []
    skip = exclude_ids or set()
    stmt = select(models.KbDocument).where(
        models.KbDocument.user_id == user_id, models.KbDocument.parsed_text_path.is_not(None)
    )
    if kind is not None:
        stmt = stmt.where(models.KbDocument.kind == kind)
    stmt = stmt.order_by(models.KbDocument.id.desc()).limit(_PARSED_SCAN_MAX_DOCS)
    hits: list[tuple[models.KbDocument, str]] = []
    for doc in db.scalars(stmt).all():
        if doc.id in skip:
            continue
        text = _read_doc_text(doc)
        if not text:
            continue
        snippet = _hit_snippet(text, qs)
        if snippet:
            hits.append((doc, snippet))
            if len(hits) >= limit:
                break
    return hits


def _assemble_chat_context(docs: list[models.KbDocument]) -> tuple[list[dict], list[str]]:
    """组装注入上下文：单文档 ≤19.9k（带「…[已截断]」标注）、总量 ≤150k。

    返回 (documents payload, omitted_titles)——解析文本缺失或总量预算耗尽的篇目
    进 omitted（响应回传给前端标注，非静默丢弃）。
    """
    payload: list[dict] = []
    omitted: list[str] = []
    total = 0
    for doc in docs:
        text = _read_doc_text(doc)
        if not text.strip():
            omitted.append(doc.title)
            continue
        if len(text) > _CHAT_DOC_CHARS:
            text = text[:_CHAT_DOC_CHARS] + "…[已截断]"
        if total + len(text) > _CHAT_TOTAL_CHARS:
            omitted.append(doc.title)
            continue
        total += len(text)
        payload.append({"doc_id": doc.id, "title": doc.title, "text": text})
    return payload, omitted


@router.post("/chat")
async def chat_with_kb(
    body: KbChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """知识库问答：仅依据库内文档经 LLM 回答（kb_answer 工具），返回 answer + 引用映射。

    上下文：指定 document_ids（归属校验，缺失/他人 -> 404）或缺省最近 20 篇已解析文档；
    端点侧预截断（单篇 ≤19.9k 带标注，总量 ≤150k，耗尽篇目列 omitted_titles）。
    成功问答持久化到 kb_chat_records（iter 220，便于日后参考；失败路径不记账——
    无答案可存）。错误映射：无供应商 -> 503 no_llm_provider；
    upstream_timeout -> 504；其余 LLM 失败 -> 502 bad_gateway 笼统消息（R35 不回显细节）。
    """
    question = body.question.strip()
    if not question:
        raise ApiError("empty_content", "问题为空")

    if body.document_ids is not None:
        ordered_ids = list(dict.fromkeys(body.document_ids))
        docs_by_id = {
            d.id: d
            for d in db.scalars(
                select(models.KbDocument).where(
                    models.KbDocument.user_id == user.id, models.KbDocument.id.in_(ordered_ids)
                )
            ).all()
        }
        for doc_id in ordered_ids:
            if doc_id not in docs_by_id:
                raise ApiError("not_found", f"知识库文档 {doc_id} 不存在")
        docs = [docs_by_id[i] for i in ordered_ids]
    else:
        docs = db.scalars(
            select(models.KbDocument)
            .where(models.KbDocument.user_id == user.id, models.KbDocument.parsed_text_path.is_not(None))
            .order_by(models.KbDocument.id.desc())
            .limit(_CHAT_DEFAULT_DOCS)
        ).all()

    payload, omitted = _assemble_chat_context(list(docs))
    if not payload:
        raise ApiError("empty_content", "知识库暂无可用文档（请先上传文档）")

    check_daily_budget(db, user.id)  # err.md A6：日预算守卫（LLM 计费端点）
    try:
        provider = get_provider(user.id)
    except Exception:  # noqa: BLE001  无 key/无 active 档案
        raise ApiError(
            "no_llm_provider", "未配置 LLM 供应商，请先在设置页配置 API Key 或启用供应商档案"
        ) from None

    ctx = ExecContext(
        session_id=f"kb-{user.id}",
        trace_id=f"kb-chat-{user.id}",
        registry=default_registry(),
        llm_provider=provider,
        data_dir=settings.data_dir,
        budget=BudgetTracker(limit=settings.budget_per_session_usd),
    )
    inp = ToolInput(
        tool="kb_answer",
        session_id=f"kb-{user.id}",
        args={"question": question, "documents": payload},
    )
    out = await run_tool("kb_answer", inp, ctx)
    record_metadata_usage(db, out.metadata, user.id)  # err.md A6：计量（含失败路径的已耗 tokens）

    if out.status != "ok":
        code = out.errors[0].code if out.errors else "tool_exception"
        if code == "upstream_timeout":
            raise ApiError("upstream_timeout", "LLM 响应超时，请稍后重试")
        log.warning("知识库问答失败: user=%s code=%s msg=%s", user.id, code, out.errors[0].message if out.errors else "")
        raise ApiError("bad_gateway", "LLM 调用失败，请稍后重试")

    spec = KbAnswerSpec.model_validate(out.data)
    titles = {d["doc_id"]: d["title"] for d in payload}
    citations = [{"doc_id": i, "title": titles[i]} for i in spec.used_doc_ids if i in titles]
    # iter 220：成功问答持久化（citations/omitted/doc_ids 存 JSON 快照——
    # 被引文档日后可删，记录须自包含可读）
    record = models.KbChatRecord(
        user_id=user.id,
        question=question,
        answer=spec.answer,
        citations=citations,
        omitted_titles=omitted,
        doc_ids=[d["doc_id"] for d in payload] if body.document_ids is not None else [],
        context_docs=len(payload),
    )
    db.add(record)
    db.commit()
    return {
        "record_id": record.id,
        "answer": spec.answer,
        "citations": citations,
        "omitted_titles": omitted,
        "context_docs": len(payload),
    }


# --- 问答记录（iter 220：持久化历史的查询/删除） ---


def _record_out(r: models.KbChatRecord) -> dict:
    return {
        "id": r.id,
        "question": r.question,
        "answer": r.answer,
        "citations": r.citations,
        "omitted_titles": r.omitted_titles,
        "doc_ids": r.doc_ids,
        "context_docs": r.context_docs,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/chats")
def list_chat_records(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """问答记录列表（新→旧）：q=问题子串；分页 limit+offset，返回 total 供前端翻页。"""
    stmt = select(models.KbChatRecord).where(models.KbChatRecord.user_id == user.id)
    if q and q.strip():
        stmt = stmt.where(models.KbChatRecord.question.contains(q.strip()))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(models.KbChatRecord.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [_record_out(r) for r in rows], "total": total}


@router.delete("/chats/{record_id}")
def delete_chat_record(
    record_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """删除单条问答记录；他人记录一律 404（防枚举，同文档删除）。"""
    record = db.scalar(
        select(models.KbChatRecord).where(
            models.KbChatRecord.id == record_id, models.KbChatRecord.user_id == user.id
        )
    )
    if record is None:
        raise ApiError("not_found", f"问答记录 {record_id} 不存在")
    db.delete(record)
    db.commit()
    return {"ok": True}
