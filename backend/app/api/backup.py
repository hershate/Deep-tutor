"""数据备份端点（iter 93）：整库导出为 JSON 下载 / 从 JSON 追加导入。

- GET  /backup/export   当前用户全部学习资产（凭证除外，见 core/backup.py 边界）
- POST /backup/import   上传备份 JSON 追加还原（全部新行，不覆盖现有数据）
- POST /backup/preview  导入预检（iter 159）：只读计数不落库，导入前看清内容
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core import backup as backup_core
from app.db.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"], dependencies=[Depends(require_auth)])

# 导入文件大小上限（本地单用户应用，256MB 足以覆盖重度使用；防误传巨型文件拖垮内存）
_MAX_BACKUP_BYTES = 256 * 1024 * 1024


@router.get("/export")
def export_backup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """导出当前用户全部数据为 JSON 附件下载（自包含，可再导入）。"""
    payload = backup_core.build_export(db, user)
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    filename = f"deep-tutor-backup-{datetime.now():%Y%m%d-%H%M%S}.json"
    log.info("备份导出：user=%s sessions=%d 大小=%.1fKB", user.id, len(payload["sessions"]), len(blob) / 1024)
    return Response(
        content=blob,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/preview")
async def preview_backup_endpoint(
    file: UploadFile,
    user: User = Depends(get_current_user),
) -> dict:
    """导入预检（iter 159）：解析备份并返回计数，不写库。

    与 /import 同的大小/JSON/结构校验；区别仅在不调用 restore。
    """
    raw = await file.read(_MAX_BACKUP_BYTES + 1)
    if len(raw) > _MAX_BACKUP_BYTES:
        raise ApiError("too_large", "备份文件过大（上限 256MB）", status=413)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError("invalid_backup", "备份文件不是合法 JSON", status=400) from None
    try:
        return backup_core.preview_backup(data)
    except backup_core.BackupFormatError as exc:
        raise ApiError("invalid_backup", str(exc), status=400) from None


@router.post("/import")
async def import_backup(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """导入备份 JSON（追加式：全部以新数据重建，不覆盖现有数据）。"""
    raw = await file.read(_MAX_BACKUP_BYTES + 1)
    if len(raw) > _MAX_BACKUP_BYTES:
        raise ApiError("too_large", "备份文件过大（上限 256MB）", status=413)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError("invalid_backup", "备份文件不是合法 JSON", status=400) from None
    try:
        summary = backup_core.restore_backup(db, user, data)
        db.commit()
    except backup_core.BackupFormatError as exc:
        db.rollback()
        raise ApiError("invalid_backup", str(exc), status=400) from None
    except Exception:  # noqa: BLE001  单事务：任何硬错误整体回滚，不留半截数据
        db.rollback()
        log.exception("备份导入失败")
        raise ApiError("import_failed", "导入失败：数据已回滚，未做任何改动", status=500) from None
    log.info("备份导入：user=%s %s", user.id, {k: v for k, v in summary.items() if k != "skipped"})
    return {"ok": True, **summary}
