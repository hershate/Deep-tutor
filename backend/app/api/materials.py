"""资料 API（plan/03 §一 materials）。

POST /api/materials/upload  上传文件解析（multipart）
POST /api/materials/url     网页链接抓取
POST /api/materials/text    纯文本提交

稳定性：文件大小上限(MAX_UPLOAD_MB)、magic bytes 校验(R20/09 §十八)、
SSRF 防护(/materials/url，复用 verify_url._is_safe_url)、解析失败统一错误码。
存储：原件存 data/sessions/<sid>/originals/，解析文本存 parsed/，DB 存路径（plan/01 §五）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.context import ExecContext
from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.core.storage import save_upload, write_parsed_text
from app.db import models
from app.tools.base import ToolInput
from app.tools.parse_material import parse_material, SUPPORTED_FILE_EXTS

log = logging.getLogger("deep_tutor.api.materials")

router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[Depends(require_auth)])

_MAGIC = {
    ".pdf": b"%PDF",
    ".docx": b"PK",
    ".pptx": b"PK",
}


class UrlIn(BaseModel):
    session_id: int
    url: str


class TextIn(BaseModel):
    session_id: int
    text: str


class MaterialOut(BaseModel):
    material_id: int
    kind: str
    meta: dict


def _get_session_or_404(db: Session, session_id: int) -> models.Session:
    sess = db.get(models.Session, session_id)
    if sess is None:
        raise ApiError("not_found", f"会话 {session_id} 不存在")
    return sess


def _ctx(session_id: int) -> ExecContext:
    return ExecContext(session_id=str(session_id), trace_id=f"mat-{session_id}")


async def _store(
    db: Session, session_id: int, kind: str, source: str, *, text_override: str | None = None
) -> MaterialOut:
    """解析 -> 持久化解析文本 -> DB 行。"""
    args = {"kind": kind, "source": source}
    if text_override is not None:
        args["source"] = text_override
    out = await parse_material(ToolInput(tool="parse_material", session_id=str(session_id), args=args), _ctx(session_id))
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    text = out.data.get("text", "")
    meta = out.data.get("meta", {})
    # source 字段存可读摘要（文件名/URL/文本前 200 字）
    src_field = source if kind != "text" else text_override[:200] if text_override else ""
    material = models.Material(
        session_id=session_id,
        kind=kind,
        source=src_field[:1024],
        parsed_chars=len(text),
        meta=meta,
    )
    db.add(material)
    db.flush()
    try:
        material.parsed_text_path = str(write_parsed_text(session_id, material.id, text))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(material)
    return MaterialOut(material_id=material.id, kind=kind, meta=meta)


def _validate_upload(filename: str, content: bytes) -> str:
    """大小 + 扩展名 + magic bytes 校验。返回清洗后扩展名。"""
    if len(content) > settings.max_upload_bytes:
        raise ApiError("payload_too_large", f"文件超上限 {settings.max_upload_mb}MB")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_FILE_EXTS:
        raise ApiError("unsupported_format", f"不支持的扩展名 {ext}（支持 {sorted(SUPPORTED_FILE_EXTS)}）")
    expected = _MAGIC.get(ext)
    if expected and not content.startswith(expected):
        raise ApiError("unsupported_format", f"文件内容与扩展名 {ext} 不符（magic bytes 校验失败）")
    return ext


@router.post("/upload", response_model=MaterialOut)
async def upload_material(
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> MaterialOut:
    _get_session_or_404(db, session_id)
    content = await file.read()
    ext = _validate_upload(file.filename or "", content)
    filename = (file.filename or "")[:1024]

    # 先建 material 行拿 id，再用 id 命名保存原件（R20：不使用原始文件名）
    material = models.Material(
        session_id=session_id, kind="file", source=filename, parsed_chars=0, meta={}
    )
    db.add(material)
    db.flush()
    path = save_upload(session_id, material.id, content, ext)

    out = await parse_material(
        ToolInput(
            tool="parse_material",
            session_id=str(session_id),
            args={"kind": "file", "source": str(path)},
        ),
        _ctx(session_id),
    )
    if out.status == "error" and out.errors:
        db.rollback()
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise ApiError(out.errors[0].code, out.errors[0].message)

    text = out.data.get("text", "")
    meta = out.data.get("meta", {})
    material.parsed_text_path = str(write_parsed_text(session_id, material.id, text))
    material.parsed_chars = len(text)
    material.meta = meta
    db.commit()
    db.refresh(material)
    return MaterialOut(material_id=material.id, kind="file", meta=meta)


@router.post("/url", response_model=MaterialOut)
async def url_material(body: UrlIn, db: Session = Depends(get_db)) -> MaterialOut:
    _get_session_or_404(db, body.session_id)
    return await _store(db, body.session_id, "url", body.url)


@router.post("/text", response_model=MaterialOut)
async def text_material(body: TextIn, db: Session = Depends(get_db)) -> MaterialOut:
    _get_session_or_404(db, body.session_id)
    if not body.text or not body.text.strip():
        raise ApiError("empty_content", "文本为空")
    return await _store(db, body.session_id, "text", body.text, text_override=body.text)
