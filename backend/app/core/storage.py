"""文件存储辅助（plan/01 §五 存储策略、R20 路径遍历防护）。

大内容入文件系统，DB 存路径。目录：data/sessions/<sid>/{originals,parsed,exports}/。
路径安全：所有路径 resolve 后须在 data_dir 内，否则拒绝（R20）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.settings import settings


def _root() -> Path:
    return Path(settings.data_dir).resolve()


def _session_root(session_id: int | str) -> Path:
    """会话目录，确保在 data_dir 内（防路径遍历）。"""
    root = _root()
    # sid 限数字/字母，杜绝 ../ 注入
    sid = str(session_id)
    if not sid.replace("_", "").isalnum():
        raise ValueError(f"非法 session_id: {session_id}")
    base = (root / "sessions" / sid).resolve()
    try:
        base.relative_to(root)  # 不在 root 内则抛 ValueError
    except ValueError as exc:
        raise ValueError(f"路径越界: {base}") from exc
    return base


def originals_dir(session_id: int | str) -> Path:
    d = _session_root(session_id) / "originals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parsed_dir(session_id: int | str) -> Path:
    d = _session_root(session_id) / "parsed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir(session_id: int | str) -> Path:
    d = _session_root(session_id) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_ext(ext: str) -> str:
    """扩展名清洗：仅保留字母数字，限长。"""
    cleaned = "".join(c for c in ext if c.isalnum())
    return cleaned[:8].lower()


def save_upload(session_id: int | str, material_id: int, content: bytes, ext: str) -> Path:
    """保存上传原件（用 material_id 命名，非原始文件名，R20）。"""
    safe = _safe_ext(ext)
    name = f"{material_id}.{safe}" if safe else str(material_id)
    path = originals_dir(session_id) / name
    path.write_bytes(content)
    return path


def write_parsed_text(session_id: int | str, material_id: int, text: str) -> Path:
    path = parsed_dir(session_id) / f"{material_id}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def delete_session_files(session_id: int | str) -> None:
    """删除会话目录（级联清理，plan/09 §五）。"""
    base = _session_root(session_id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
