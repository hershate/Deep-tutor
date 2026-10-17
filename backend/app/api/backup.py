"""数据备份端点（iter 93）：整库导出为 JSON 下载 / 从 JSON 追加导入。

- GET  /backup/export   当前用户全部学习资产（凭证除外，见 core/backup.py 边界）
- POST /backup/import   上传备份 JSON 追加还原（全部新行，不覆盖现有数据）
- POST /backup/preview  导入预检（iter 159）：只读计数不落库，导入前看清内容
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core import backup as backup_core
from app.core.ratelimit import limiter
from app.db.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"], dependencies=[Depends(require_auth)])

# 导入文件大小上限（本地单用户应用，256MB 足以覆盖重度使用；防误传巨型文件拖垮内存）
_MAX_BACKUP_BYTES = 256 * 1024 * 1024
# iter 246：备份面限流（此前是限流覆盖遗漏点）——export 全量序列化、import/preview
# 单次读入 ≤256MB 且 json.loads 内存峰值 2-3 倍；并发触发可拖垮小内存部署机
_EXPORT_RATE_PER_MIN = 10
_IMPORT_RATE_PER_MIN = 5
_PREVIEW_RATE_PER_MIN = 10
# iter 369：进程级导入互斥——restore 单事务期间持有 SQLite 写锁，并发导入各读相同
# 配额计数可越过上限、且第二个导入大概率在写锁上等满 busy_timeout 后 500；
# 第二个请求立即 409（同 plans 生成 409 立即拒绝约定）
_RESTORE_LOCK = threading.Lock()


def _check_rate(user: User, bucket: str, limit: int) -> None:
    if not limiter.check(f"backup:{bucket}:{user.id}", limit):
        raise ApiError("rate_limited", "备份操作过于频繁，请稍后再试", status=429)


@router.get("/export")
def export_backup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """导出当前用户全部数据为 JSON 附件下载（自包含，可再导入）。

    iter 369：序列化改落临时文件 + FileResponse 流式回传——此前 payload dict +
    dumps str + encode bytes 三份全量驻留（重度用户单次导出 3 倍内存峰值，恰在
    最需要备份的时刻最可能 OOM）；json.dump 增量写盘后峰值仅剩 dict 一份，
    发送完毕后台删临时文件。
    """
    _check_rate(user, "export", _EXPORT_RATE_PER_MIN)
    payload = backup_core.build_export(db, user)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="dt-backup-")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        size = os.path.getsize(tmp)
    except BaseException:
        os.unlink(tmp)
        raise
    filename = f"deep-tutor-backup-{datetime.now():%Y%m%d-%H%M%S}.json"
    log.info("备份导出：user=%s sessions=%d 大小=%.1fKB", user.id, len(payload["sessions"]), size / 1024)
    return FileResponse(
        tmp,
        media_type="application/json; charset=utf-8",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp),
    )


@router.post("/preview")
async def preview_backup_endpoint(
    file: UploadFile,
    user: User = Depends(get_current_user),
) -> dict:
    """导入预检（iter 159）：解析备份并返回计数，不写库。

    与 /import 同的大小/JSON/结构校验；区别仅在不调用 restore。
    """
    _check_rate(user, "preview", _PREVIEW_RATE_PER_MIN)
    raw = await file.read(_MAX_BACKUP_BYTES + 1)
    if len(raw) > _MAX_BACKUP_BYTES:
        raise ApiError("too_large", "备份文件过大（上限 256MB）", status=413)
    try:
        # iter 367（批次四）：大 JSON 解析移 to_thread——256MB json.loads 在事件循环上
        # 可阻塞数秒，多用户并发时拖垮所有请求（含 SSE 心跳）
        data = await asyncio.to_thread(json.loads, raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError("invalid_backup", "备份文件不是合法 JSON", status=400) from None
    try:
        return await asyncio.to_thread(backup_core.preview_backup, data)
    except backup_core.BackupFormatError as exc:
        raise ApiError("invalid_backup", str(exc), status=400) from None


@router.post("/import")
async def import_backup(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """导入备份 JSON（追加式：全部以新数据重建，不覆盖现有数据）。"""
    _check_rate(user, "import", _IMPORT_RATE_PER_MIN)
    raw = await file.read(_MAX_BACKUP_BYTES + 1)
    if len(raw) > _MAX_BACKUP_BYTES:
        raise ApiError("too_large", "备份文件过大（上限 256MB）", status=413)
    try:
        data = await asyncio.to_thread(json.loads, raw)  # 同 preview：大解析移 to_thread
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError("invalid_backup", "备份文件不是合法 JSON", status=400) from None
    # iter 369：导入互斥——restore 单事务持 SQLite 写锁，并发导入会配额竞态 + 写锁等待 500
    if not _RESTORE_LOCK.acquire(blocking=False):
        raise ApiError("conflict", "已有备份导入正在进行，请稍后再试", status=409)
    try:
        try:
            # iter 367：restore 全程同步 DB/文件写移 to_thread；iter 369：commit/rollback
            # 一并入 to_thread——大导入的 commit 需刷全部脏页 + fsync（慢盘秒级），
            # 留在事件循环上会冻结全部请求（含 SSE 心跳）
            def _restore_and_commit() -> dict:
                summary = backup_core.restore_backup(db, user, data)
                db.commit()
                return summary

            summary = await asyncio.to_thread(_restore_and_commit)
        except backup_core.BackupFormatError as exc:
            await asyncio.to_thread(db.rollback)
            raise ApiError("invalid_backup", str(exc), status=400) from None
        except Exception:  # noqa: BLE001  单事务：任何硬错误整体回滚，不留半截数据
            await asyncio.to_thread(db.rollback)
            log.exception("备份导入失败")
            raise ApiError("import_failed", "导入失败：数据已回滚，未做任何改动", status=500) from None
    finally:
        _RESTORE_LOCK.release()
    log.info("备份导入：user=%s %s", user.id, {k: v for k, v in summary.items() if k != "skipped"})
    return {"ok": True, **summary}
