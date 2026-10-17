"""资料 API（plan/03 §一 materials）。

POST /api/materials/upload  上传文件解析（multipart）
POST /api/materials/url     网页链接抓取
POST /api/materials/text    纯文本提交

稳定性：文件大小上限(MAX_UPLOAD_MB)、magic bytes 校验(R20/09 §十八)、
SSRF 防护(/materials/url，复用 verify_url._is_safe_url)、解析失败统一错误码。
存储：原件存 data/sessions/<sid>/originals/，解析文本存 parsed/，DB 存路径（plan/01 §五）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import BudgetTracker
from app.agent.context import ExecContext, Timeouts
from app.api.deps import check_daily_budget, get_current_user, get_db, get_owned_session, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.core.locks import drop_lock
from app.core.settings import settings
from app.core.storage import (
    delete_session_files,
    originals_dir,
    read_unit_content,
    save_upload,
    write_parsed_text,
)
from app.db import models
from app.db.base import SessionLocal
from app.db.models import User
from app.llm import get_provider
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.build_plan_slides import generate_unit_slides_spec, render_slides_html
from app.tools.parse_material import parse_material, SUPPORTED_FILE_EXTS

log = logging.getLogger("deep_tutor.api.materials")

router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[Depends(require_auth)])

# per-material 解析互斥锁（iter 225）：同资料并发解析 -> 409；不同资料可并行
# （事务外解析 + 短写事务，err.md B22）。锁对象只增不减，每资料一个，不做淘汰。
_PARSE_LOCKS: dict[int, asyncio.Lock] = {}

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
    url: str = Field(min_length=1, max_length=2048)  # err.md A5：此前无上限


class TextIn(BaseModel):
    session_id: int
    text: str = Field(max_length=2_000_000)  # err.md A5：与 ClipIn content 同上限（2MB），防内存 DoS


class MaterialOut(BaseModel):
    material_id: int
    kind: str
    meta: dict
    # 处理状态（iter 225）：pending 待确认 / confirmed 已添加未解析 / parsed 已解析。
    # 默认 parsed 兼容 text/url 等「创建即解析」构造点。
    status: str = "parsed"


def _get_session_or_404(db: Session, session_id: int, user: User) -> models.Session:
    """当前用户拥有的会话（P4 iter 58 隔离：不存在/属他人 -> 404）。"""
    return get_owned_session(db, session_id, user)


def _ctx(session_id: int) -> ExecContext:
    return ExecContext(session_id=str(session_id), trace_id=f"mat-{session_id}")


# 配额（plan/09 §二十）：每会话资料数上限；URL 抓取速率（plan/09 §十八）
_MAX_MATERIALS_PER_SESSION = 20
_URL_RATE_PER_MIN = 10
# iter 248：上传限流（次/分钟/用户）——_read_capped 只限单请求内存，并发 N 个
# 50MB 上传的内存叠加此前无防（多用户部署 OOM 面；与 iter 247 kb 上传口径一致）
_UPLOAD_RATE_PER_MIN = 20
# iter 367（批次五）：解析/纯文本入库限流（次/分钟/用户）——解析含 OCR/whisper
# 等重 CPU/长耗时路径；text 入库无成本内容可脚本灌行。此前两者均无限流
_PARSE_RATE_PER_MIN = 20
_TEXT_RATE_PER_MIN = 30


def _hash_source(data: bytes | str) -> str:
    """来源内容哈希（plan/11 §九 content hash 去重）。文本按 utf-8 编码。"""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def _check_material_quota(db: Session, session_id: int) -> None:
    """每会话资料数 ≤20（plan/09 §二十）；超限 -> 429 quota_exceeded。"""
    count = db.scalar(select(func.count(models.Material.id)).where(models.Material.session_id == session_id)) or 0
    if count >= _MAX_MATERIALS_PER_SESSION:
        raise ApiError(
            "quota_exceeded",
            f"每会话资料数已达上限 {_MAX_MATERIALS_PER_SESSION} 条（plan/09 §二十）",
        )


def _material_out(mat: models.Material, *, deduped: bool = False) -> MaterialOut:
    return MaterialOut(
        material_id=mat.id,
        kind=mat.kind.value if hasattr(mat.kind, "value") else str(mat.kind),
        meta={**(mat.meta or {}), **({"deduped": True} if deduped else {})},
        status=mat.status,
    )


def _owned_material_or_404(db: Session, material_id: int, user: User) -> models.Material:
    """当前用户拥有的资料（经 session 归属校验）；不存在/属他人 -> 404。"""
    mat = db.get(models.Material, material_id)
    if mat is None:
        raise ApiError("not_found", f"资料 {material_id} 不存在")
    sess = db.get(models.Session, mat.session_id)
    if sess is None or sess.user_id != user.id:
        raise ApiError("not_found", f"资料 {material_id} 不存在")
    return mat


def _dedup_lookup(db: Session, session_id: int, content_hash: str) -> MaterialOut | None:
    """同会话相同内容哈希的资料 -> 直接复用（plan/11 §九），不重复建行/扣配额。

    iter 225：不再限已解析——pending 暂存/confirmed 未解析行同样复用（重复上传
    同一文件返回该行，前端按其 status 展示，不重复占配额）。
    """
    existing = db.scalar(
        select(models.Material)
        .where(
            models.Material.session_id == session_id,
            models.Material.content_hash == content_hash,
        )
        .order_by(models.Material.id)  # iter 248：并发双建行（check-then-insert 竞态）
        # 下取最旧行复用，结果确定（此前无 ORDER BY，多行命中时行为不定）
    )
    if existing is None:
        return None
    return _material_out(existing, deduped=True)


async def _store(
    db: Session, session_id: int, kind: str, source: str, *, text_override: str | None = None
) -> MaterialOut:
    """解析 -> 持久化解析文本 -> DB 行（先查 content hash 去重 + 配额）。

    iter 368：去重/配额读检查后即 rollback 释放读快照——解析（URL 抓取可达分钟级）
    期间不得持有读事务（SQLite WAL 下升级写会 BUSY_SNAPSHOT，且钉住快照挡 checkpoint，
    同 kb.create_text_document / parse_document 口径）；解析完成后在新鲜状态上重查
    去重/配额（等待期间并发请求可能已插入同哈希行或打满配额）。
    """
    content_hash = _hash_source(text_override if text_override is not None else source.strip())
    deduped = _dedup_lookup(db, session_id, content_hash)
    if deduped is not None:
        return deduped
    _check_material_quota(db, session_id)
    db.rollback()

    args = {"kind": kind, "source": source}
    if text_override is not None:
        args["source"] = text_override
    out = await parse_material(
        ToolInput(tool="parse_material", session_id=str(session_id), args=args), _ctx(session_id)
    )
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    # 解析后重查：并发同哈希插入 -> 复用其行（本请求的解析结果弃用，防重复行）
    deduped = _dedup_lookup(db, session_id, content_hash)
    if deduped is not None:
        return deduped
    _check_material_quota(db, session_id)
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
        content_hash=content_hash,
    )
    db.add(material)
    db.flush()
    parsed_path = None
    try:
        parsed_path = write_parsed_text(session_id, material.id, text)
        material.parsed_text_path = str(parsed_path)
        db.commit()
    except Exception:
        db.rollback()
        # iter 248：commit 失败（磁盘满/库锁）清理已落盘解析文本防孤儿（同 kb _persist_parsed）
        if parsed_path is not None:
            parsed_path.unlink(missing_ok=True)
        raise
    db.refresh(material)
    return MaterialOut(material_id=material.id, kind=kind, meta=meta)


def _validate_upload(filename: str, content: bytes) -> str:
    """扩展名 + magic bytes 校验。返回清洗后扩展名。

    内置格式走 SUPPORTED_FILE_EXTS + _MAGIC；插件注册的格式（后端插件 API v1.3.0
    扩展点）经 parsers.registered_extensions() 接纳、magic 经 parsers.magic_for()。
    被 kb.py 复用——改本函数即同时覆盖知识库上传路径。

    大小校验由 `_read_capped` 在读取阶段完成（防 OOM），此处不再重复。
    """
    from app.plugins import parsers

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    plugin_exts = parsers.registered_extensions()
    if ext not in SUPPORTED_FILE_EXTS and ext not in plugin_exts:
        raise ApiError(
            "unsupported_format",
            f"不支持的扩展名 {ext}（支持 {sorted(SUPPORTED_FILE_EXTS | plugin_exts)}）",
        )
    expected = _MAGIC.get(ext)
    if expected is None:
        expected = parsers.magic_for(ext)  # 插件可注册 magic 签名校验文件真实性
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


@router.get("/formats")
def list_formats() -> dict:
    """可解析文件格式清单（内置 + 插件注册）。

    供替代前端 / CLI / 其他客户端做上传前格式发现（"用 API 完全替换前端"场景必需——
    客户端需知本实例接受哪些格式）。只暴露格式名/扩展名/来源插件，不含机密。
    """
    from app.plugins import parsers

    return {
        "builtin": sorted(SUPPORTED_FILE_EXTS),
        "plugin": [
            {
                "extensions": list(entry.extensions),
                "label": entry.label or f"{entry.plugin_id} 解析器",
                "plugin": entry.plugin_id,
                "override": entry.override,
            }
            for entry in parsers.all_entries()
        ],
    }


@router.post("/upload", response_model=MaterialOut)
async def upload_material(
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    """上传文件到会话资料（iter 225 上传确认流程）：校验 -> 去重 -> 配额 -> 建行
    pending + 落原件，**不做任何解析**——是否转换/怎么转换由用户在确认后调
    /materials/{id}/parse 决定（秒级响应，不再上传即默认解析）。
    """
    _get_session_or_404(db, session_id, user)
    # iter 248：上传限流（与 iter 247 kb 同面补齐——materials 留口则 kb 白堵）
    if not limiter.check(f"mat_upload:{user.id}", _UPLOAD_RATE_PER_MIN):
        raise ApiError("rate_limited", f"上传限每分钟 {_UPLOAD_RATE_PER_MIN} 次，请稍后再试")
    content = await _read_capped(file, settings.max_upload_bytes)
    ext = _validate_upload(file.filename or "", content)
    filename = (file.filename or "")[:1024]

    # 内容哈希去重（plan/11 §九）：同会话重复上传同一文件 -> 复用已有行（含 pending 暂存）
    content_hash = _hash_source(content)
    deduped = _dedup_lookup(db, session_id, content_hash)
    if deduped is not None:
        return deduped
    _check_material_quota(db, session_id)

    # 建行 pending 即提交拿 id（R20 用 id 命名原件；写事务极短，err.md B22）
    material = models.Material(
        session_id=session_id,
        kind="file",
        source=filename,
        parsed_chars=0,
        meta={},
        content_hash=content_hash,
        status="pending",
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    try:
        # iter 248：大文件写盘移出事件循环（50MB 同步 write 阻塞数百毫秒，同 kb）
        await asyncio.to_thread(save_upload, session_id, material.id, content, ext)
    except Exception:
        # 原件落盘失败：删行防孤儿（err.md A15）
        db.delete(material)
        db.commit()
        raise
    return MaterialOut(material_id=material.id, kind="file", meta={}, status="pending")


class ParseIn(BaseModel):
    # 解析质量档（iter 225）：仅 PDF 有别——full=marker（高质量/扫描件 OCR，慢）/ fast=pypdf（秒级纯文本）
    mode: Literal["full", "fast"] = "full"


@router.post("/{material_id}/confirm", response_model=MaterialOut)
def confirm_material(
    material_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MaterialOut:
    """确认添加资料：pending（暂存待确认）-> confirmed（已添加，未解析）。幂等。"""
    mat = _owned_material_or_404(db, material_id, user)
    if mat.status == "pending":
        mat.status = "confirmed"
        db.commit()
        db.refresh(mat)
    return _material_out(mat)


@router.post("/{material_id}/parse", response_model=MaterialOut)
async def parse_material_document(
    material_id: int,
    body: ParseIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    """按需解析/转换资料（iter 225：用户确认后触发，是否转换与方式由用户决定）。

    pending/confirmed -> parsed（pending 隐含确认）；已 parsed 可重解析换档（覆盖
    解析文本）。解析失败**行保留状态不变**（可换档重试）。事务外解析（err.md B22）；
    同资料并发解析 -> 409。无原件（text/url 类）-> 400。
    """
    mode = body.mode if body else "full"
    # iter 367（批次五）：解析限流——重 CPU/长耗时（OCR/whisper 可达分钟级），
    # 脚本循环触发可占满解析并发与内存
    if not limiter.check(f"parse:{user.id}", _PARSE_RATE_PER_MIN):
        raise ApiError("rate_limited", f"解析限每分钟 {_PARSE_RATE_PER_MIN} 次，请稍后再试")
    mat = _owned_material_or_404(db, material_id, user)
    originals = originals_dir(mat.session_id)
    matches = sorted(originals.glob(f"{mat.id}.*"))
    if not matches:
        raise ApiError("bad_request", "该资料无原件（text/url 类创建时已解析入库）", status=400)
    source_path = str(matches[0])
    session_id = mat.session_id
    # iter 248：解析完成后据此校验重取行身份（同 iter 247 kb）——解析窗口（最长 900s）
    # 内资料被删且 rowid 复用时，重取到的会是新资料，须拒写防错绑
    pre_content_hash = mat.content_hash
    db.rollback()  # 释放 SELECT 事务：解析期间不持有任何 DB 锁（err.md B22）

    lock = _PARSE_LOCKS.setdefault(material_id, asyncio.Lock())
    if lock.locked():
        raise ApiError("conflict", "该资料正在解析中，请稍候", status=409)
    try:
        async with lock:
            try:
                # 直调路径不走 run_tool——Timeouts.parse 外层兜底在此补齐
                # （whisper 转写无内部超时，ffprobe 缺失时时长守卫失效，兜无限挂起）
                out = await asyncio.wait_for(
                    parse_material(
                        ToolInput(
                            tool="parse_material",
                            session_id=str(session_id),
                            args={"kind": "file", "source": source_path, "parse_mode": mode},
                        ),
                        _ctx(session_id),
                    ),
                    timeout=Timeouts().parse,
                )
            except TimeoutError:
                raise ApiError("upstream_timeout", f"解析超时（{int(Timeouts().parse)}s），请重试或换档") from None
            if out.status == "error" and out.errors:
                raise ApiError(out.errors[0].code, out.errors[0].message)
            text = out.data.get("text", "")
            parsed_path = write_parsed_text(session_id, material_id, text)
            try:
                mat = _owned_material_or_404(db, material_id, user)  # rollback 后重取实例
            except ApiError:
                # iter 248：解析期间资料被删 -> 刚写出的解析文件无人引用，清理防孤儿
                parsed_path.unlink(missing_ok=True)
                raise
            if mat.content_hash != pre_content_hash:
                # iter 248：rowid 复用——重取到的是解析期间新建的另一份资料，
                # 解析文本不可错绑（原件与解析内容对不上），丢弃并提示重试
                parsed_path.unlink(missing_ok=True)
                raise ApiError("conflict", "资料在解析期间已变更，请重试", status=409)
            mat.parsed_text_path = str(parsed_path)
            mat.parsed_chars = len(text)
            mat.meta = {**(mat.meta or {}), **out.data.get("meta", {})}
            mat.status = "parsed"
            try:
                db.commit()
            except Exception:
                db.rollback()
                parsed_path.unlink(missing_ok=True)  # iter 248：commit 失败防孤儿
                raise
            db.refresh(mat)
            result = _material_out(mat)
    finally:
        drop_lock(_PARSE_LOCKS, material_id, lock)  # 与锁释放间无 await（iter 232 台账有界）
    return result


@router.post("/url", response_model=MaterialOut)
async def url_material(
    body: UrlIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MaterialOut:
    _get_session_or_404(db, body.session_id, user)
    # URL 抓取速率限制（plan/09 §十八 /materials/url 每分钟限 N）
    if not limiter.check(f"url:{user.id}", _URL_RATE_PER_MIN):
        raise ApiError("rate_limited", f"网页抓取限每分钟 {_URL_RATE_PER_MIN} 次，请稍后再试")
    return await _store(db, body.session_id, "url", body.url)


@router.post("/text", response_model=MaterialOut)
async def text_material(
    body: TextIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MaterialOut:
    _get_session_or_404(db, body.session_id, user)
    if not body.text or not body.text.strip():
        raise ApiError("empty_content", "文本为空")
    # iter 367（批次五）：入库限流——无成本内容可脚本灌行（配额只限总量不限速率）
    if not limiter.check(f"text:{user.id}", _TEXT_RATE_PER_MIN):
        raise ApiError("rate_limited", f"文本入库限每分钟 {_TEXT_RATE_PER_MIN} 次，请稍后再试")
    return await _store(db, body.session_id, "text", body.text, text_override=body.text)


def _owned_unit_or_404(db: Session, unit_id: int, user: User) -> models.StudyUnit:
    """当前用户拥有的自学单元（unit -> plan -> session.user_id；不存在/属他人 -> 404）。"""
    unit = db.get(models.StudyUnit, unit_id)
    if unit is None or unit.plan is None or unit.plan.session is None or unit.plan.session.user_id != user.id:
        raise ApiError("not_found", f"自学单元 {unit_id} 不存在")
    return unit


@router.get("/{unit_id}")
def get_study_unit_content(unit_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
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
            content_md = read_unit_content(mat.content_md_path)  # 自动串联拆分子文件（plan/02 §13）
        except OSError:
            # err.md A10：不回显服务器文件系统路径（R35），路径仅进服务端日志
            log.warning("单元内容文件缺失 unit=%s path=%s", unit.id, mat.content_md_path)
            raise ApiError("not_found", f"单元 {unit.id} 的内容文件缺失") from None
    return {"unit_id": unit.id, "title": unit.title, "content_md": content_md, "resources": resources}


def _unit_content_disposition(filename_utf8: str, filename_ascii: str) -> str:
    """RFC 5987：中文文件名经 filename*=UTF-8'' 百分号编码，UA 不支持时回退 ASCII 名。

    与 plans.py _content_disposition 同口径（本地副本，免为单端点跨模块拉入 plans）。
    """
    from urllib.parse import quote

    return f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{quote(filename_utf8)}"


_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_unit_filename(name: str) -> str:
    """文件名清洗：剔除 Windows/zip 非法符与控制字符（LLM 输出的标题可含换行），限长 50
    （与 plans.py _safe_download_name 同口径）。"""
    cleaned = _CTRL_CHARS_RE.sub("", "".join("_" if c in '\\/:*?"<>|' else c for c in name)).strip(". ") or "单元"
    return cleaned[:50]


_UNIT_EXPORT_RATE_PER_MIN = 12  # 单元 PPT 导出限流（与 exports.py / plans.py 导出同口径，共用 export:{uid} 键）


@router.get("/{unit_id}/export")
async def export_unit_slides(
    unit_id: int,
    format: str = "html",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """单个自学单元 -> HTML 幻灯片（iter 359，借助 ppt-slide-builder）。

    复用 build_plan_slides 的 render_slides_html + ppt-slide-builder/templates/base.html：
    LLM 生成 SlideSpec（无 key/失败降级 default_spec_from_unit 确定性按 ### 切分）。
    返回自包含 HTML 附件（前端 Blob 下载，浏览器打开即演示，无需额外文件服务/鉴权）。
    LLM 用量计入 api_usage / 日预算（与 _export_slides 同口径）。
    iter 369：LLM 两阶段包 llm_task_slot（与 _export_slides 同口径——此前本端点绕过
    全局背压且 GET 零限流）；内容读移 to_thread。
    """
    fmt = (format or "html").lower()
    if fmt != "html":
        raise ApiError("invalid_format", f"不支持的导出格式：{format}（仅支持 html）", status=400)
    if not limiter.check(f"export:{user.id}", _UNIT_EXPORT_RATE_PER_MIN):
        raise ApiError("rate_limited", f"导出限每分钟 {_UNIT_EXPORT_RATE_PER_MIN} 次，请稍后再试")

    unit = _owned_unit_or_404(db, unit_id, user)
    session_id = unit.plan.session_id

    # 加载单元内容（read_unit_content 自动串联拆分子文件）
    content_md = ""
    if unit.materials:
        try:
            content_md = await asyncio.to_thread(read_unit_content, unit.materials[0].content_md_path)
        except OSError:
            # err.md A10：不回显服务器文件系统路径（R35），路径仅进服务端日志
            log.warning("单元 PPT 导出：单元 %s 内容文件缺失 path=%s", unit.id, unit.materials[0].content_md_path)
            raise ApiError("not_found", f"单元 {unit.id} 的内容文件缺失") from None

    # 幻灯片模板（vendored ppt-slide-builder，缺失属异常）
    template_path = Path(settings.skills_dir) / "ppt-slide-builder" / "templates" / "base.html"
    try:
        base_template = template_path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        log.error("幻灯片模板缺失: %s", exc)
        raise ApiError("internal_error", "幻灯片模板缺失") from exc

    # 供应商解析（无 key -> 降级确定性 deck，零成本；与 _export_slides 同口径）
    try:
        provider = get_provider(user.id)
    except Exception:  # noqa: BLE001
        provider = None
    if provider is not None:
        check_daily_budget(None, user.id)

    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=f"unit-slides-{unit_id}",
        registry=default_registry(),
        llm_provider=provider,
        data_dir=settings.data_dir,
        budget=BudgetTracker(limit=settings.budget_per_session_usd),
    )
    # iter 359 续：LLM 两阶段——build 生成草稿 + review 审校修正（格式/内容/分页），
    # 任一成功用 LLM 产物，两者皆败或无 key 才确定性兜底（用户反馈硬模板格式不对）。
    # iter 369：包全局 LLM 背压槽；进入前释放读快照（槽等待/LLM 期间不持读事务，
    # 同 kb chat / materials._store 口径）。快照所需属性先取_plain值，rollback 后不再碰 ORM。
    unit_title = unit.title or "自学单元"
    db.rollback()
    from app.core.concurrency import llm_task_slot

    async with llm_task_slot():
        spec, metadatas, degraded = await generate_unit_slides_spec(
            unit_title, content_md, ctx, session_id=str(session_id), unit_id=str(unit_id)
        )

    # LLM 用量计入 api_usage（日预算统计口径完整；确定性兜底零用量不入库）
    if any(m.tokens_in or m.tokens_out for m in metadatas):
        from app.agent.cost import record_metadata_usage

        udb = SessionLocal()

        def _record_all():  # iter 370：整批单线程内逐条记（每次 commit 含 fsync，N 单元 N 次提交不出循环）
            for m in metadatas:
                if m.tokens_in or m.tokens_out:
                    record_metadata_usage(udb, m, user.id)

        try:
            await asyncio.to_thread(_record_all)
        finally:
            udb.close()

    html = render_slides_html(spec, base_template)
    safe_title = _safe_unit_filename(unit_title)
    headers = {"Content-Disposition": _unit_content_disposition(f"{safe_title}.html", f"unit-{unit_id}.html")}
    if degraded and provider is not None:
        # 仅 LLM 两阶段皆败才告警（无 key 走确定性是预期路径，不算降级）
        from urllib.parse import quote

        headers["X-Export-Warning"] = quote("幻灯片 LLM 生成与审校均失败，已降级为默认结构")
    log.info("单元 PPT 导出：unit=%s slides=%d degraded=%s", unit_id, len(spec.slides), degraded)
    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8", headers=headers)


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
async def clip_material(body: ClipIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """剪藏（浏览器插件用）：创建会话（归属当前用户）+ 解析入资料，返回生成页路径。

    - text：剪藏选中文本；url：剪藏页面链接（复用 /materials/url 的 SSRF 防护与解析）。
    - title 缺省取内容前 40 字（text）或 URL 前 80 字。
    - 资料解析失败 -> 删除刚建会话（防孤儿，同 Home 前端模式）后抛原错误。
    - 前端打开 `open_path`（/generate/{session_id}）即开始生成（raw_text 为空，仅用资料）。
    """
    content = body.content.strip()
    title = (body.title or "").strip()
    # err.md A24：clip 同样受会话数配额约束（与 POST /sessions 一致）
    from app.api.sessions import _MAX_SESSIONS_PER_USER

    if db.query(models.Session).filter_by(user_id=user.id).count() >= _MAX_SESSIONS_PER_USER:
        raise ApiError(
            "quota_exceeded",
            f"会话数已达上限 {_MAX_SESSIONS_PER_USER} 个，请先删除不再需要的会话",
        )
    if body.kind == "text":
        if not content:
            raise ApiError("empty_content", "剪藏内容为空")
        title = title or content[:40]
    else:
        if not _HTTPS_RE.match(content):
            raise ApiError("bad_request", "剪藏链接须以 http(s):// 开头", status=400)
        # err.md A13：clip 的 url 分支与 /materials/url 同一抓取入口，同样过限流
        if not limiter.check(f"url:{user.id}", _URL_RATE_PER_MIN):
            raise ApiError("rate_limited", f"网页抓取限每分钟 {_URL_RATE_PER_MIN} 次，请稍后再试")
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
