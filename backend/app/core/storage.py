"""文件存储辅助（plan/01 §五 存储策略、R20 路径遍历防护）。

大内容入文件系统，DB 存路径。目录：data/sessions/<sid>/{originals,parsed,exports,materials}/。
路径安全：所有路径 resolve 后须在 data_dir 内，否则拒绝（R20）。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from functools import lru_cache
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


@lru_cache(maxsize=64)
def _root_cached(data_dir: str) -> Path:
    """data_dir 的解析结果（缓存）——见 _root 的说明。"""
    return Path(data_dir).resolve()


def _root() -> Path:
    """data_dir 根目录（已 resolve）。

    iter 396 B1：解析结果带缓存——``Path.resolve()`` 在 Windows 上约 0.46ms/次
    （走 ntpath.realpath），备份还原逐文件解析会话目录时会被放大到秒级
    （实测 1250 个文件 -> 2505 次 resolve -> 1.57s，且全部落在写事务内）。
    缓存键含 data_dir 原文，设置变更即换键；``resolve_under_root`` 的越界
    校验在首次计算时已完成，路径由 data_dir + id 唯一确定。
    """
    return _root_cached(str(settings.data_dir))


def data_root() -> Path:
    """data_dir 根目录（已 resolve）——备份暂存区等需与最终路径同卷的场景用（iter 396 B1）。"""
    return _root()


def strip_ext_prefix(p: Path | str) -> str:
    """去掉 Windows 扩展长度前缀（``\\\\?\\``）——ntpath.realpath 在部分路径形态下
    （并发建目录、junction、超长路径等）会返回带前缀形式，与未带前缀的根路径做
    ``relative_to`` 比较会**误判越界**（合法路径被拒 -> 500 / 工具报错）。
    UNC 形态 ``\\\\?\\UNC\\host\\share`` 还原为 ``\\\\host\\share``。
    """
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
        if s.startswith("UNC\\"):
            s = "\\\\" + s[4:]
    return s


def is_within_root(root: Path, target: Path) -> bool:
    """target 是否位于 root 之内（路径穿越守卫，Windows 前缀形态无关）。

    两侧都先剥扩展长度前缀再比较；``PureWindowsPath`` 的比较本身大小写不敏感。
    """
    try:
        Path(strip_ext_prefix(target)).relative_to(Path(strip_ext_prefix(root)))
        return True
    except ValueError:
        return False


def relative_to_root(root: Path, target: Path) -> str:
    """target 相对 root 的 posix 路径（Windows 扩展长度前缀形态无关）。

    产物登记等场景需要「相对 data_dir 的路径」；resolve() 偶发返回 \\?\\ 前缀形态时，
    逐处 relative_to 会抛 ValueError。
    """
    return Path(strip_ext_prefix(target)).relative_to(Path(strip_ext_prefix(root))).as_posix()


def resolve_under_root(root: Path, *parts: str) -> Path:
    """解析 ``root/parts`` 并校验其仍在 root 之内；越界抛 ValueError。

    与逐处手写 ``resolve().relative_to(root)`` 等价，但统一处理 Windows 扩展长度
    前缀形态（否则合法路径会被误判越界——iter 392 实测：备份导入并发写文件时
    目录刚被创建，resolve() 返回 ``\\\\?\\`` 前缀形态，越界守卫抛错致整次导入失败）。
    """
    base = root.joinpath(*parts).resolve()
    if not is_within_root(root, base):
        raise ValueError(f"路径越界: {base}")
    return base


@lru_cache(maxsize=4096)
def _session_root_cached(root_str: str, sid: str) -> Path:
    """会话目录解析结果（缓存）——见 _session_root 的说明。"""
    return resolve_under_root(Path(root_str), "sessions", sid)


def _session_root(session_id: int | str) -> Path:
    """会话目录，确保在 data_dir 内（防路径遍历）。"""
    root = _root()
    # sid 限数字/字母，杜绝 ../ 注入
    sid = str(session_id)
    if not sid.replace("_", "").isalnum():
        raise ValueError(f"非法 session_id: {session_id}")
    # iter 392：越界判定统一走 resolve_under_root（Windows 扩展长度前缀形态无关）
    # iter 396 B1：解析结果带缓存（还原时同一会话的目录被逐文件反复解析）
    return _session_root_cached(str(root), sid)


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


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """公开原子写（字节）——备份还原的「暂存区落盘」用（iter 396 B1）。"""
    _atomic_write_bytes(path, content)


def replace_with_retry(src: str | Path, dst: str | Path) -> None:
    """公开的 os.replace（带 Windows 瞬时占用重试）——备份还原的改名用（iter 396 B1）。

    杀软/索引器瞬时锁住目标文件会让 os.replace 抛 PermissionError（Linux 无此
    问题，rename 原子且不要求目标可打开）；沿用 _replace_with_retry 的指数退避。
    """
    _replace_with_retry(str(src), str(dst))


def save_upload(session_id: int | str, material_id: int, content: bytes, ext: str) -> Path:
    """保存上传原件（用 material_id 命名，非原始文件名，R20）。"""
    safe = _safe_ext(ext)
    name = f"{material_id}.{safe}" if safe else str(material_id)
    path = originals_dir(session_id) / name
    _atomic_write_bytes(path, content)
    return path


# --- 上传原件的「先暂存、后改名」（iter 397 K3） ---
#
# 原件最终名必须含行 id（material_id / doc_id），而行 id 要等 INSERT 之后才有。
# 于是只能「先建行、再落盘」——留下「DB 有行但文件缺失」的窗口：行提交后、
# 文件写完前进程被杀/落盘失败 -> 一条永远没有原件的 pending 行（用户点解析
# 只得到 400，且无任何后台路径能修）。
#
# 改为与备份还原（iter 396 B1）同款两段式：
#   ① 预暂存（事务外、无写锁）：内容落到会话暂存目录（随机名，不依赖行 id）；
#   ② 改名（事务内、行 id 已知）：暂存文件 rename 成正式名（纯元数据操作，~ms 级）。
# 由此「行可见」必然晚于「文件就位」——不存在「有行无文件」的持久状态；
# 反向的短暂孤儿（文件已落、行未建）由失败路径清理，且对用户不可见。

_STAGING_DIRNAME = ".staging"


def staging_dir(session_id: int | str) -> Path:
    """会话级暂存目录（上传原件/解析文本的落地中转）。"""
    d = _session_root(session_id) / _STAGING_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_upload(session_id: int | str, content: bytes, ext: str) -> Path:
    """上传原件 -> 会话暂存目录（事务外落盘；行 id 未知时的中转名）。"""
    safe = _safe_ext(ext)
    stem = uuid.uuid4().hex
    path = staging_dir(session_id) / (f"{stem}.{safe}" if safe else stem)
    _atomic_write_bytes(path, content)
    return path


def stage_text(session_id: int | str, text: str, suffix: str = ".txt") -> Path:
    """文本（解析文本）-> 会话暂存目录（事务外落盘）。"""
    path = staging_dir(session_id) / f"{uuid.uuid4().hex}{suffix}"
    _atomic_write_text(path, text)
    return path


def promote_staged(staged: Path, final: Path) -> Path:
    """暂存文件 -> 正式路径（rename；纯元数据操作，~ms 级，可在事务内调用）。"""
    final.parent.mkdir(parents=True, exist_ok=True)
    _replace_with_retry(str(staged), str(final))
    return final


def promote_upload(session_id: int | str, material_id: int, staged: Path) -> Path:
    """暂存原件 -> 正式名 originals/<material_id>.<ext>（事务内 rename）。"""
    final = originals_dir(session_id) / f"{material_id}{staged.suffix}"
    return promote_staged(staged, final)


def parsed_text_path(session_id: int | str, material_id: int) -> Path:
    """资料解析文本的目标路径（只算路径，不写文件）——备份还原「先暂存后改名」用（iter 396 B1）。"""
    return parsed_dir(session_id) / f"{material_id}.txt"


def write_parsed_text(session_id: int | str, material_id: int, text: str) -> Path:
    path = parsed_text_path(session_id, material_id)
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


def unit_content_parts(content_md: str) -> list[str]:
    """单元内容的分段（main + .pN）——备份还原的暂存/改名与正常写入共用同一分段规则（iter 396 B1）。"""
    if content_md.count("\n") + 1 <= _MAX_UNIT_FILE_LINES:
        return [content_md]
    return _split_at_headings(content_md)


def unit_content_paths(session_id: int | str, unit_id: int, content_md: str) -> list[Path]:
    """单元内容的目标路径列表（main + .pN，只算路径不写文件）——备份还原的暂存+改名用（iter 396 B1）。"""
    main = materials_dir(session_id) / f"unit_{unit_id}.md"
    parts = unit_content_parts(content_md)
    return [main] + [_part_path(main, i) for i in range(2, len(parts) + 1)]


def write_unit_content(session_id: int | str, unit_id: int, content_md: str) -> Path:
    """写单元内容（>500 行自动拆子文件，plan/02 §13），返回主文件路径。

    iter 365 D2：先写新版本（原子）后清多余旧 .pN——旧实现先 delete 再 write，写中途遇 IO 错
    则旧内容已删、新内容未落盘 -> 单元内容彻底丢失。新顺序：原子写新 main + .pN（覆盖旧同名），
    全部成功后清编号超出新 parts 数的旧 .pN；任一写失败则旧版本仍完整（可重试）。"""
    main = materials_dir(session_id) / f"unit_{unit_id}.md"
    parts = unit_content_parts(content_md)
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


def prune_unit_parts(main: Path, keep_count: int) -> None:
    """删编号 > keep_count 的 .pN 拆分子文件（公开入口，iter 396 B1）。

    备份还原走「暂存 + 改名」路径时不经 write_unit_content，需显式清理旧残留
    （还原失败重试会复用同一 unit_id，残留 .pN 会让读回内容多出旧段落）。
    """
    _delete_extra_parts(main, keep_count)


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


@lru_cache(maxsize=4096)
def _kb_root_cached(root_str: str, uid: str) -> Path:
    """知识库目录解析结果（缓存）——见 _kb_root 的说明。"""
    return resolve_under_root(Path(root_str), "kb", uid)


def _kb_root(user_id: int | str) -> Path:
    """用户知识库目录，确保在 data_dir 内（R20 防路径遍历，同 _session_root）。"""
    root = _root()
    uid = str(user_id)
    if not uid.replace("_", "").isalnum():
        raise ValueError(f"非法 user_id: {user_id}")
    # iter 396 B1：解析结果带缓存（同 _session_root）
    return _kb_root_cached(str(root), uid)


def kb_originals_dir(user_id: int | str) -> Path:
    d = _kb_root(user_id) / "originals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def kb_parsed_dir(user_id: int | str) -> Path:
    d = _kb_root(user_id) / "parsed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def kb_original_path(user_id: int | str, doc_id: int | str, ext: str) -> Path:
    """知识库原件的目标路径（只算路径，不写文件）——备份还原的暂存+改名用（iter 396 B1）。"""
    safe = _safe_ext(ext)
    name = f"{doc_id}.{safe}" if safe else str(doc_id)
    return kb_originals_dir(user_id) / name


def save_kb_upload(user_id: int | str, doc_id: int | str, content: bytes, ext: str) -> Path:
    """保存知识库上传原件（用 doc_id 命名，非原始文件名，R20）。"""
    path = kb_original_path(user_id, doc_id, ext)
    _atomic_write_bytes(path, content)
    return path


def kb_parsed_text_path(user_id: int | str, doc_id: int | str) -> Path:
    """知识库解析文本的目标路径（只算路径，不写文件）——备份还原的暂存+改名用（iter 396 B1）。"""
    return kb_parsed_dir(user_id) / f"{doc_id}.txt"


def write_kb_parsed_text(user_id: int | str, doc_id: int | str, text: str) -> Path:
    path = kb_parsed_text_path(user_id, doc_id)
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
