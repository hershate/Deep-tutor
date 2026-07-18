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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.context import ExecContext
from app.api.deps import get_current_user, get_db, get_owned_session, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.core.storage import delete_session_files, save_upload, write_parsed_text
from app.db import models
from app.db.models import User
from app.tools.base import ToolInput
from app.tools.parse_material import parse_material, SUPPORTED_FILE_EXTS

log = logging.getLogger("deep_tutor.api.materials")

router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[Depends(require_auth)])

_MAGIC = {
    ".pdf": b"%PDF",
    ".docx": b"PK",
    ".pptx": b"PK",
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
    ".bmp": b"BM",
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


def _get_session_or_404(db: Session, session_id: int, user: User) -> models.Session:
    """当前用户拥有的会话（P4 iter 58 隔离：不存在/属他人 -> 404）。"""
    return get_owned_session(db, session_id, user)


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
    """扩展名 + magic bytes 校验。返回清洗后扩展名。

    大小校验由 `_read_capped` 在读取阶段完成（防 OOM），此处不再重复。
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_FILE_EXTS:
        raise ApiError("unsupported_format", f"不支持的扩展名 {ext}（支持 {sorted(SUPPORTED_FILE_EXTS)}）")
    expected = _MAGIC.get(ext)
    if expected and not content.startswith(expected):
        raise ApiError("unsupported_format", f"文件内容与扩展名 {ext} 不符（magic bytes 校验失败）")
    return ext


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """读取上传内容，限量 max_bytes+1 防 OOM（R20 / DoS）。

    - `file.size` 预检：Content-Length 可得时，超大文件直接拒，不读字节。
    - 限量读取：至多读 `max_bytes+1`；超 `max_bytes` 抛 `payload_too_large`
      （Content-Length 缺失/伪造时的兜底，内存上界 = `max_bytes+1`）。
    """
    size = getattr(file, "size", None)
    if size is not None and size > max_bytes:
        raise ApiError("payload_too_large", f"文件超上限 {settings.max_upload_mb}MB")
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ApiError("payload_too_large", f"文件超上限 {settings.max_upload_mb}MB")
    return content


@router.post("/upload", response_model=MaterialOut)
async def upload_material(
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    _get_session_or_404(db, session_id, user)
    content = await _read_capped(file, settings.max_upload_bytes)
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
async def url_material(
    body: UrlIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MaterialOut:
    _get_session_or_404(db, body.session_id, user)
    return await _store(db, body.session_id, "url", body.url)


@router.post("/text", response_model=MaterialOut)
async def text_material(
    body: TextIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MaterialOut:
    _get_session_or_404(db, body.session_id, user)
    if not body.text or not body.text.strip():
        raise ApiError("empty_content", "文本为空")
    return await _store(db, body.session_id, "text", body.text, text_override=body.text)


def _owned_unit_or_404(db: Session, unit_id: int, user: User) -> models.StudyUnit:
    """当前用户拥有的自学单元（unit -> plan -> session.user_id；不存在/属他人 -> 404）。"""
    unit = db.get(models.StudyUnit, unit_id)
    if unit is None or unit.plan is None or unit.plan.session is None or unit.plan.session.user_id != user.id:
        raise ApiError("not_found", f"自学单元 {unit_id} 不存在")
    return unit


@router.get("/{unit_id}")
def get_study_unit_content(
    unit_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """自学材料单元内容（plan/03 §一 GET /materials/{unit_id}，P2 iter 43）。

    偏离记录：返回 content_md（前端 react-markdown 渲染）替代 plan 的 content_html；
    resources 为 WebSearch 补充资料（iter 48，可能为空 list）。
    """
    unit = _owned_unit_or_404(db, unit_id, user)
    content_md = ""
    resources: list = []
    if unit.materials:
        mat = unit.materials[0]
        resources = mat.resources or []
        try:
            content_md = Path(mat.content_md_path).read_text(encoding="utf-8")
        except OSError:
            raise ApiError("not_found", f"单元内容文件缺失: {mat.content_md_path}") from None
    return {"unit_id": unit.id, "title": unit.title, "content_md": content_md, "resources": resources}


class UnitDoneIn(BaseModel):
    done: bool
@router.post("/{unit_id}/done")
def mark_unit_done(
    unit_id: int,
    body: UnitDoneIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """标记/取消已学（plan/07 §六 StudyMaterialReader 标记已学，P2 iter 44）。

    done=True -> done_at=当前 UTC（与 func.now() 同口径的 naive UTC）；False -> 置 None。幂等。
    """
    unit = _owned_unit_or_404(db, unit_id, user)
    unit.done_at = datetime.now(timezone.utc).replace(tzinfo=None) if body.done else None
    db.commit()
    return {"unit_id": unit.id, "done": unit.done_at is not None}


# --- 浏览器剪藏（P4 iter 64，plan/README 输入形式10）：建会话 + 加资料一步到位 ---

_HTTPS_RE = re.compile(r"^https?://", re.IGNORECASE)


class ClipIn(BaseModel):
    kind: Literal["text", "url"]
    content: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=256)


@router.post("/clip")
async def clip_material(
    body: ClipIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """剪藏（浏览器插件用）：创建会话（归属当前用户）+ 解析入资料，返回生成页路径。

    - text：剪藏选中文本；url：剪藏页面链接（复用 /materials/url 的 SSRF 防护与解析）。
    - title 缺省取内容前 40 字（text）或 URL 前 80 字。
    - 资料解析失败 -> 删除刚建会话（防孤儿，同 Home 前端模式）后抛原错误。
    - 前端打开 `open_path`（/generate/{session_id}）即开始生成（raw_text 为空，仅用资料）。
    """
    content = body.content.strip()
    title = (body.title or "").strip()
    if body.kind == "text":
        if not content:
            raise ApiError("empty_content", "剪藏内容为空")
        title = title or content[:40]
    else:
        if not _HTTPS_RE.match(content):
            raise ApiError("bad_request", "剪藏链接须以 http(s):// 开头", status=400)
        title = title or content[:80]

    sess = models.Session(user_id=user.id, title=title[:256])
    db.add(sess)
    db.commit()
    db.refresh(sess)
    try:
        if body.kind == "text":
            mat = await _store(db, sess.id, "text", content, text_override=content)
        else:
            mat = await _store(db, sess.id, "url", content)
    except Exception:
        # 资料失败 -> 清理孤儿会话（行 + 文件目录），再抛原错误
        try:
            doomed = db.get(models.Session, sess.id)
            if doomed is not None:
                db.delete(doomed)
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        try:
            delete_session_files(sess.id)
        except Exception:  # noqa: BLE001
            pass
        raise
    return {
        "session_id": sess.id,
        "material_id": mat.material_id,
        "kind": mat.kind,
        "title": sess.title,
        "open_path": f"/generate/{sess.id}",
    }
