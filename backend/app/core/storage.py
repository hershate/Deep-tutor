"""文件存储辅助（plan/01 §五 存储策略、R20 路径遍历防护）。

大内容入文件系统，DB 存路径。目录：data/sessions/<sid>/{originals,parsed,exports,materials}/。
路径安全：所有路径 resolve 后须在 data_dir 内，否则拒绝（R20）。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from app.core.settings import settings

log = logging.getLogger(__name__)


def _rmtree_orphan_aware(base: Path) -> None:
    """删目录并在失败时记日志（iter 246）——此前 ignore_errors=True 静默吞错：
    Windows 文件锁（杀软索引/编辑器占用）下 rmtree 失败留下孤儿目录，
    kb/exports 目录无维护清扫，磁盘悄悄膨胀且无从排查。"""
    shutil.rmtree(base, ignore_errors=True)
    if base.exists():
        log.warning("目录删除不完整（可能文件被占用），遗留：%s", base)


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


def materials_dir(session_id: int | str) -> Path:
    d = _session_root(session_id) / "materials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_ext(ext: str) -> str:
    """扩展名清洗：仅保留字母数字，限长。"""
    cleaned = "".join(c for c in ext if c.isalnum())
    return cleaned[:8].lower()


def _replace_with_retry(src: str, dst: str, *, attempts: int = 4) -> None:
    """os.replace 带 Windows 短暂占用重试（iter 368）。

    Windows 下杀软/索引器瞬时锁目标文件会让 replace 抛 PermissionError（Linux 无此
    问题，rename 原子且不要求目标可打开）；指数退避重试后仍失败才向上抛。
    """
    import time

    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """iter 365 D3：原子写——tmp + os.replace（崩溃/IO 错不留半写文件，旧版本完整直至替换成功）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        _replace_with_retry(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(path, text.encode(encoding))


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """公开原子写（iter 368）：plans 溢出 JSON / 备份等模块外调用方使用。"""
    _atomic_write_text(path, text, encoding)


def save_upload(session_id: int | str, material_id: int, content: bytes, ext: str) -> Path:
    """保存上传原件（用 material_id 命名，非原始文件名，R20）。"""
    safe = _safe_ext(ext)
    name = f"{material_id}.{safe}" if safe else str(material_id)
    path = originals_dir(session_id) / name
    _atomic_write_bytes(path, content)
    return path


def write_parsed_text(session_id: int | str, material_id: int, text: str) -> Path:
    path = parsed_dir(session_id) / f"{material_id}.txt"
    _atomic_write_text(path, text)
    return path


def write_unit_text(session_id: int | str, unit_id: int, text: str) -> Path:
    """自学材料单元内容（study_materials.content_md_path，plan/01 §五）。"""
    path = materials_dir(session_id) / f"unit_{unit_id}.md"
    _atomic_write_text(path, text)
    return path


# --- 单元内容拆子文件（plan/02 §13 errors：单文件 >500 行 -> 拆子文件）---

_MAX_UNIT_FILE_LINES = 500


def _part_path(main: Path, n: int) -> Path:
    """拆分子文件路径：unit_<id>.md -> unit_<id>.p2.md（n=2 起）。"""
    return main.with_name(f"{main.stem}.p{n}{main.suffix}")


def _split_at_headings(text: str, max_lines: int = _MAX_UNIT_FILE_LINES) -> list[str]:
    """按 ### 小节边界装箱切分为 ≤max_lines 的部分；单节超限则硬切。"""
    sections: list[str] = []
    buf: list[str] = []
    for ln in text.split("\n"):
        if ln.startswith("### ") and buf:
            sections.append("\n".join(buf))
            buf = []
        buf.append(ln)
    if buf:
        sections.append("\n".join(buf))

    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for sec in sections:
        sec_lines = sec.count("\n") + 1
        if cur and cur_len + sec_lines > max_lines:
            parts.append("\n\n".join(cur))
            cur, cur_len = [], 0
        if sec_lines > max_lines:  # 单节超限 -> 硬切
            hard = sec.split("\n")
            for i in range(0, len(hard), max_lines):
                parts.append("\n".join(hard[i : i + max_lines]))
            continue
        cur.append(sec)
        cur_len += sec_lines
    if cur:
        parts.append("\n\n".join(cur))
    return parts or [text]


def write_unit_content(session_id: int | str, unit_id: int, content_md: str) -> Path:
    """写单元内容（>500 行自动拆子文件，plan/02 §13），返回主文件路径。

    iter 365 D2：先写新版本（原子）后清多余旧 .pN——旧实现先 delete 再 write，写中途遇 IO 错
    则旧内容已删、新内容未落盘 -> 单元内容彻底丢失。新顺序：原子写新 main + .pN（覆盖旧同名），
    全部成功后清编号超出新 parts 数的旧 .pN；任一写失败则旧版本仍完整（可重试）。"""
    main = materials_dir(session_id) / f"unit_{unit_id}.md"
    parts = (
        [content_md]
        if content_md.count("\n") + 1 <= _MAX_UNIT_FILE_LINES
        else _split_at_headings(content_md)
    )
    _atomic_write_text(main, parts[0])  # 原子覆盖 main（先写新，失败旧 main 仍在）
    for i, part in enumerate(parts[1:], 2):
        _atomic_write_text(_part_path(main, i), part)  # 原子覆盖 .pN
    _delete_extra_parts(main, len(parts))  # 清编号 > parts 的旧 .pN（如旧 3 段 -> 新 1 段的 .p2/.p3）
    return main


def _delete_extra_parts(main: Path, keep_count: int) -> None:
    """删编号 > keep_count 的 .pN 拆分子文件（write_unit_content 新版本写完后清旧残留）。"""
    n = keep_count + 1  # parts 编号 1(main)..keep_count；keep_count+1 起为旧残留
    while True:
        part = _part_path(main, n)
        if not part.exists():
            break
        part.unlink(missing_ok=True)
        n += 1


def read_unit_content(path: str | Path) -> str:
    """读单元内容，自动串联 .p2/.p3 拆分子文件（对调用方透明还原全文）。"""
    main = Path(path)
    text = main.read_text(encoding="utf-8")
    n = 2
    while True:
        part = _part_path(main, n)
        if not part.exists():
            break
        text += "\n\n" + part.read_text(encoding="utf-8")
        n += 1
    return text


def delete_unit_content(path: str | Path) -> None:
    """删单元内容文件及其全部拆分子文件。"""
    main = Path(path)
    main.unlink(missing_ok=True)
    n = 2
    while True:
        part = _part_path(main, n)
        if not part.exists():
            break
        part.unlink(missing_ok=True)
        n += 1


def delete_session_files(session_id: int | str) -> None:
    """删除会话目录（级联清理，plan/09 §五）。"""
    base = _session_root(session_id)
    if base.exists():
        _rmtree_orphan_aware(base)


def delete_material_files(session_id: int | str, material_id: int) -> None:
    """删除单个源资料的磁盘文件（iter 104：DELETE /sessions/{sid}/materials/{mid} 配套）。

    原件 originals/<mid>.<ext>（保存时扩展名经 _safe_ext 清洗、删除时不定，按 "<mid>." 前缀 glob；
    material_id 为 int，模式 "5.*" 不会误中 "50.*"）；解析文本 parsed/<mid>.txt 精确路径。
    目录不存在即无文件可删（不创建目录）。缺文件不报错（幂等清理）。
    """
    base = _session_root(session_id)
    originals = base / "originals"
    if originals.is_dir():
        for path in originals.glob(f"{material_id}.*"):
            path.unlink(missing_ok=True)
    (base / "parsed" / f"{material_id}.txt").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 知识库文档存储（iter 212：LLMWiki 式独立文档库 /kb）
#
# 目录：data/kb/<user_id>/{originals/<doc_id>.<ext>, parsed/<doc_id>.txt}。
# 与 sessions 族同构：大内容入文件系统，DB（kb_documents）存路径；
# maintenance.py 只扫 data/sessions/ 数字目录，kb 目录不受影响（不随会话过期清理）。
# ---------------------------------------------------------------------------


def _kb_root(user_id: int | str) -> Path:
    """用户知识库目录，确保在 data_dir 内（R20 防路径遍历，同 _session_root）。"""
    root = _root()
    uid = str(user_id)
    if not uid.replace("_", "").isalnum():
        raise ValueError(f"非法 user_id: {user_id}")
    base = (root / "kb" / uid).resolve()
    try:
        base.relative_to(root)  # 不在 root 内则抛 ValueError
    except ValueError as exc:
        raise ValueError(f"路径越界: {base}") from exc
    return base


def kb_originals_dir(user_id: int | str) -> Path:
    d = _kb_root(user_id) / "originals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def kb_parsed_dir(user_id: int | str) -> Path:
    d = _kb_root(user_id) / "parsed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_kb_upload(user_id: int | str, doc_id: int | str, content: bytes, ext: str) -> Path:
    """保存知识库上传原件（用 doc_id 命名，非原始文件名，R20）。"""
    safe = _safe_ext(ext)
    name = f"{doc_id}.{safe}" if safe else str(doc_id)
    path = kb_originals_dir(user_id) / name
    _atomic_write_bytes(path, content)
    return path


def write_kb_parsed_text(user_id: int | str, doc_id: int | str, text: str) -> Path:
    path = kb_parsed_dir(user_id) / f"{doc_id}.txt"
    _atomic_write_text(path, text)
    return path


def delete_kb_document_files(user_id: int | str, doc_id: int | str) -> None:
    """删除单篇知识库文档的磁盘文件（原件按 "<doc_id>." 前缀 glob，解析文本精确路径）。

    目录不存在即无文件可删（不创建目录）。缺文件不报错（幂等清理）。
    """
    base = _kb_root(user_id)
    originals = base / "originals"
    if originals.is_dir():
        for path in originals.glob(f"{doc_id}.*"):
            path.unlink(missing_ok=True)
    (base / "parsed" / f"{doc_id}.txt").unlink(missing_ok=True)


def delete_kb_files(user_id: int | str) -> None:
    """删除用户整个知识库目录（删用户级联清理，供 users.py delete_user 调用）。"""
    base = _kb_root(user_id)
    if base.exists():
        _rmtree_orphan_aware(base)
