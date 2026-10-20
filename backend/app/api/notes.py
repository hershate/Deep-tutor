"""独立笔记 API（iter 227 自由笔记本）：标题+正文，不依附方案/知识点，随建随记。

与 /knowledge/notes（知识点批注，挂知识节点）并存互补：那边是「这个知识点的笔记」，
这边是「我的笔记本」。安全约定同族：
- P4 按用户隔离：一切操作仅作用于当前用户的笔记；跨用户访问一律 404（不泄露存在性）。
- 每用户 ≤500 条（与 credentials/profiles 配额同族），超限 429。
- 标题与正文不可同时为空（防空壳笔记）；正文上限 20000 字（对齐 NodeNote _MAX_NOTE_CHARS）。
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.dbwrite import run_db_write
from app.core.quota import quota_slot
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/notes", tags=["notes"], dependencies=[Depends(require_auth)])

_MAX_NOTES_PER_USER = 500  # 每用户笔记条数上限（与其他资源配额同族）
_MAX_TITLE_CHARS = 256
_MAX_CONTENT_CHARS = 20_000
_LIST_PREVIEW_CHARS = 300  # 列表预览截断（完整内容走 GET /notes/{id}）
# iter 292：导入安全覆盖
_IMPORT_MAX_ENTRY_BYTES = 100_000  # 单条目原始字节上限（≈ 覆盖 _MAX_CONTENT_CHARS 的 UTF-8 字节）
_IMPORT_TOTAL_BYTES_CAP = 5 * 1024 * 1024  # 全部条目解压总量上限（防 zip bomb）
# iter 396：zip 条目分块读的单块上限。**同时约束 zlib 单次解压输出量**——
# zipfile 的 zf.read() 走 ZipExtFile._read1(MAX_N=1<<31)，zlib 会把整个 deflate
# 成员一次性展开（实测：97KB 请求体 -> 峰值分配 204.8MiB），且与中央目录声明的
# file_size 无关（攻击者可谎报声明值绕过单条大小判定）。分块读把每次解压输出
# 限制在块大小量级（实测峰值 0.3MiB）。
_ZIP_CHUNK_BYTES = 64 * 1024
# iter 368：写/导出限流——写路径逐条含 20k 字文本落库，导出为流式 zip 压缩（CPU 密集）
_NOTES_WRITE_PER_MIN = 60
_NOTES_EXPORT_PER_MIN = 10


def _check_notes_write(user: User) -> None:
    if not limiter.check(f"notes_write:{user.id}", _NOTES_WRITE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)


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
    # iter 378 R21：乐观并发期望值——客户端把装载时的 updated_at 原样回传，
    # 与库中不一致（他窗口已改写）-> 409，防双标签盲覆盖丢更新；不传保持旧 LWW 行为
    expected_updated_at: str | None = Field(default=None, max_length=64)


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
    _check_notes_write(user)
    title, content = _normalize(body)
    # iter 394：配额判定是「先查后写」——并发创建可同时通过「未达上限」检查，
    # 实际写入数按并发数突破（app/core/quota.py：单进程部署下进程内互斥）。
    with quota_slot(f"note:{user.id}"):
        if db.query(models.Note).filter_by(user_id=user.id).count() >= _MAX_NOTES_PER_USER:
            raise ApiError("quota_exceeded", f"笔记数已达上限 {_MAX_NOTES_PER_USER} 条，请先删除不再使用的笔记")
        note = models.Note(user_id=user.id, title=title, content=content)
        db.add(note)
        db.commit()
        db.refresh(note)
    return _out(note)


def _split_md_title(text: str, fallback_title: str) -> tuple[str, str]:
    """从 markdown 文本拆 (title, content)：首个 '# ' 行为标题（与 GET /export zip 格式对称），
    否则标题取 fallback（文件名 stem）；content 为全文（去首个标题行）。"""
    lines = text.split("\n", 1)
    first = lines[0].strip() if lines else ""
    if first.startswith("# "):
        return first[2:].strip()[:_MAX_TITLE_CHARS], (lines[1] if len(lines) > 1 else "").strip()
    return (fallback_title or "")[:_MAX_TITLE_CHARS], text.strip()


def _read_zip_entry(fp, cap: int) -> tuple[bytes, bool]:
    """从 ZipExtFile 读至多 ``cap + 1`` 字节，返回 (数据, 是否超出单条上限)。

    iter 396：分块读（``fp.read(_ZIP_CHUNK_BYTES)``）而非 ``zf.read(name)``——后者
    走 ``ZipExtFile._read1(MAX_N=1<<31)``，zlib 单次可分配至多 2GB（实测 97KB
    请求体 -> 峰值 204.8MiB），且**不受中央目录声明的 file_size 约束**（声明值
    可谎报）。分块后每次解压输出受块大小约束（实测峰值 0.3MiB）。
    """
    buf = bytearray()
    while len(buf) <= cap:
        chunk = fp.read(_ZIP_CHUNK_BYTES)
        if not chunk:
            return bytes(buf), False
        buf += chunk
    return bytes(buf), True


def _parse_import_payloads(payloads: list[tuple[str, bytes]]) -> tuple[list[tuple[str, str]], int]:
    """解析导入内容 -> (待入库的 (title, content) 列表, 跳过条数)。

    纯 CPU/内存工作，**无 DB 访问**——调用方在事件循环外（``to_thread``）执行。

    iter 396 修正：
    - zip 条目按实际解压字节流式读取，声明的 file_size 仅作参考不作依据；
    - 累计解压字节超 ``_IMPORT_TOTAL_BYTES_CAP`` 即**停止**（此前继续遍历并逐条
      解压后续条目，放大同一风险）；
    - 候选条数上限 = ``_MAX_NOTES_PER_USER``（再多也装不下，白做解压）。
    """
    rows: list[tuple[str, str]] = []
    skipped = 0
    total_bytes = 0
    candidates: list[tuple[str, bytes]] = []

    for idx, (name, data) in enumerate(payloads):
        if name.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                skipped += 1
                continue
            with zf:
                md_infos = [
                    i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".md")
                ]
                for idx, info in enumerate(md_infos):
                    try:
                        with zf.open(info) as fp:
                            raw, oversized = _read_zip_entry(fp, _IMPORT_MAX_ENTRY_BYTES)
                    except Exception:  # noqa: BLE001  损坏条目跳过，不阻塞其余
                        skipped += 1
                        continue
                    if oversized:
                        skipped += 1  # 单条过大（按**实际**解压字节判定，不信声明值）
                        continue
                    candidates.append((info.filename, raw))
                    total_bytes += len(raw)
                    if total_bytes > _IMPORT_TOTAL_BYTES_CAP:
                        # 超总预算即停——不再解压后续条目（余下条目计入跳过，
                        # 与旧实现的 skipped 口径一致）
                        skipped += len(md_infos) - idx - 1
                        break
        else:
            candidates.append((name, data))
            total_bytes += len(data)
            if total_bytes > _IMPORT_TOTAL_BYTES_CAP:
                skipped += len(payloads) - idx  # 余下上传件一并计入跳过
                break

    for name, raw in candidates:
        if len(rows) >= _MAX_NOTES_PER_USER:
            skipped += 1
            continue
        text = raw[:_IMPORT_MAX_ENTRY_BYTES].decode("utf-8", errors="replace")[:_MAX_CONTENT_CHARS]
        title, content = _split_md_title(text, Path(name).stem)
        if not title and not content:
            skipped += 1
            continue
        rows.append((title, content))
    return rows, skipped


@router.post("/import", status_code=201)
async def import_notes(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """批量导入自由笔记（iter 292，与 GET /export zip 对称）：接受 .md 文件与/或含 .md 的 .zip。
    每篇：首个 '# ' 行为标题（否则文件名 stem）、正文为全文；与导出格式可往返。

    安全覆盖：每条目内容 ≤_MAX_CONTENT_CHARS、单条**实际解压**字节 ≤_IMPORT_MAX_ENTRY_BYTES、
    全部解压总量 ≤_IMPORT_TOTAL_BYTES_CAP（防 zip bomb）、总数受 _MAX_NOTES_PER_USER 配额约束、
    zip 仅内存 read 不落盘（无 zip slip）、UTF-8 解码失败跳过、空标题+空正文跳过（同 _normalize）。
    返回 {imported, skipped, quota_full}。

    iter 396 三处修正：
    1. **配额竞态闭合**——此前在函数开头数一次 `existing` 就用 `existing + imported`
       判定上限，全程不持 quota_slot、不在提交前复查：两个并发 import（或 import 与
       并发 create_note）各自看到同一 `existing` -> 实际落库可达 ~2 倍上限。现把
       「计数 + 批量插入 + 提交」放进同一把 `quota_slot("note:{user.id}")`（与
       create_note 同键），且计数建立在**已提交**的可见状态上（iter 394 口径）。
    2. **解压移出事件循环**——zip 遍历与读取此前在 async 端点内同步执行，命中
       大条目时事件循环停摆（全站请求与 SSE 心跳冻结）。现整段走 `to_thread`。
    3. **内存放大封死**——见 `_read_zip_entry` 注释（声明值谎报 -> 205MiB 峰值）。
    """
    _check_notes_write(user)
    # 阶段一（无 DB）：读上传体 -> 解析条目。解析在事件循环外执行。
    payloads: list[tuple[str, bytes]] = []
    for up in files:
        name = up.filename or "note.md"
        data = await up.read()
        if not data:
            continue
        payloads.append((name, data))
    rows, parse_skipped = await asyncio.to_thread(_parse_import_payloads, payloads)

    # 阶段二（写库）：配额判定 + 批量插入 + 提交，同一把 quota_slot 内完成。
    def _write() -> tuple[int, bool]:
        with quota_slot(f"note:{user.id}"):
            existing = db.query(models.Note).filter_by(user_id=user.id).count()
            room = max(0, _MAX_NOTES_PER_USER - existing)
            take = rows[:room]
            for title, content in take:
                db.add(models.Note(user_id=user.id, title=title, content=content))
            if take:
                db.commit()  # 提交须在锁内——否则并发请求看不到未提交的行（iter 394）
            return len(take), len(rows) > room

    imported, quota_full = await run_db_write(_write)
    skipped = parse_skipped + max(0, len(rows) - imported)
    return {"imported": imported, "skipped": skipped, "quota_full": quota_full}


@router.get("/export")
def export_notes_zip(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> StreamingResponse:
    """全部自由笔记打包导出（iter 286，ZIP）：每篇一个 .md（# 标题 + 正文）+ index.md 清单。

    便携导出（迁入 Obsidian/Notion 等）——区别于设置页全库 JSON 备份（内部恢复格式）：
    本端点产出人类可读的独立 .md 文件。无笔记 -> 404（导出是批量便利操作，空态前端按钮禁用）。
    SpooledTemporaryFile 防大笔记集 OOM（500 篇 × 2 万字上限约 10MB，仍走 spool 统一口径）。
    路由须在 /{note_id} 之前注册，否则 "export" 被当 note_id 解析报 422。
    """
    from app.api.plans import _safe_download_name

    if not limiter.check(f"notes_export:{user.id}", _NOTES_EXPORT_PER_MIN):
        raise ApiError("rate_limited", "导出过于频繁，请稍后再试", status=429)
    notes = (
        db.query(models.Note)
        .filter_by(user_id=user.id)
        .order_by(models.Note.updated_at.desc(), models.Note.id.desc())
        .all()
    )
    if not notes:
        raise ApiError("not_found", "还没有笔记，无可导出内容")

    def _note_md(n: models.Note) -> str:
        title = (n.title or "").strip()
        content = n.content or ""
        return f"# {title or '无题'}\n\n{content}".rstrip() + "\n"

    index_lines = [
        "# 笔记导出清单",
        "",
        f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 笔记数：{len(notes)}",
        "",
        "| 文件 | 标题 | 更新时间 |",
        "| --- | --- | --- |",
    ]

    spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for n in notes:
            stem = _safe_download_name(n.title or f"note-{n.id}") or f"note-{n.id}"
            name = f"{stem}.md"
            if name in used:  # 同名笔记去重：追加 id
                # iter 396：追加 id 后仍可能撞名（另一篇笔记的标题恰为
                # "<id>-<stem>"）-> 继续编号直到不重名。否则导出件解压时后者
                # 覆盖前者，那篇笔记静默丢失。
                base = f"{n.id}-{stem}"
                name = f"{base}.md"
                k = 1
                while name in used:
                    k += 1
                    name = f"{base}-{k}.md"
            used.add(name)
            zf.writestr(name, _note_md(n))
            index_lines.append(f"| {name} | {(n.title or '无题').replace('|', '/')} | {n.updated_at} |")
        index_lines.append("")
        zf.writestr("index.md", "\n".join(index_lines))
    spool.seek(0)

    def _iter_chunks():
        try:
            while chunk := spool.read(64 * 1024):
                yield chunk
        finally:
            spool.close()

    filename = f"笔记导出-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        _iter_chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"notes-export-{user.id}.zip\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return _out(_owned_or_404(db, note_id, user))


@router.put("/{note_id}")
def update_note(
    note_id: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    _check_notes_write(user)
    note = _owned_or_404(db, note_id, user)
    # iter 378 R21：期望时间戳不匹配 = 其他窗口已保存更新版本 -> 409 拒绝盲覆盖
    # （不传 = 旧行为；等秒精度下同秒双写仍 LWW——SQLite CURRENT_TIMESTAMP 秒粒度所限）
    if body.expected_updated_at is not None:
        cur = note.updated_at.isoformat() if note.updated_at else None
        if cur != body.expected_updated_at:
            raise ApiError("conflict", "笔记已在其他窗口被修改，请刷新后重试", status=409)
    note.title, note.content = _normalize(body)
    db.commit()
    db.refresh(note)
    return _out(note)


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_notes_write(user)
    db.delete(_owned_or_404(db, note_id, user))
    db.commit()
    return {"ok": True}
