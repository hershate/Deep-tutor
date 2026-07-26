"""Agent 工作区（iter 345 记忆系统，需求③ Claude Code 式 Read/Write/Edit）。

每个 run 一个**临时沙箱工作区**：``data/runs/<run_id>/workspace/``。Agent 文件工具
（workspace_read/write/edit/list/move）只能在此沙箱内操作（路径 resolve+relative_to 防穿越，
同 core/storage R20）。写操作一律**原子写**（tmp + os.replace），崩溃无半写文件。

生命周期（状态机）：
- scratch（``data/runs/<run_id>/workspace/``）：run 执行期读写草拟大文件/学习计划；
- commit：run 正常完成 -> 整体 ``shutil.move`` 到持久区 ``data/workspaces/<thread_id>/<run_id>/``，
  置 ``runs.workspace_committed=1``（崩溃 liveness 判据）+ 登记 RunArtifact(kind=committed)；
- promote（phase F 工具）：用户确认后把 committed 产物复制进既有持久体系（plans/materials/notes）。

崩溃恢复：scratch 在磁盘上，进程崩溃不丢；startup 把 ``runs.status==running`` 收敛为
``interrupted``（main.py，同 orchestrator_state 模式）——弃用 resume，改"新 run"续
（历史在 messages，连续性不丢；半截 tool_use 重放非幂等）。孤儿 ``data/runs/*/`` 由
maintenance.cleanup_orphan_run_dirs 清；committed 区脱离 run 生命周期不清（除非 run 行已删）。
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

from app.core.settings import settings

log = logging.getLogger("deep_tutor.agent.workspace")


def _root() -> Path:
    return Path(settings.data_dir).resolve()


def _check_under_root(p: Path) -> None:
    try:
        p.resolve().relative_to(_root())
    except ValueError as exc:
        raise ValueError(f"工作区路径越界: {p}") from exc


def _digit(seg, label: str) -> str:
    s = str(seg)
    if not s.isdigit():
        raise ValueError(f"非法 {label}: {seg}")
    return s


def scratch_dir(run_id: int) -> Path:
    """run 临时工作区根：data/runs/<run_id>/workspace/（自动创建）。"""
    rid = _digit(run_id, "run_id")
    d = (_root() / "runs" / rid / "workspace").resolve()
    _check_under_root(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def committed_dir(thread_id: int, run_id: int) -> Path:
    """run 持久区根：data/workspaces/<thread_id>/<run_id>/（不自动创建，commit 时建）。"""
    tid = _digit(thread_id, "thread_id")
    rid = _digit(run_id, "run_id")
    d = (_root() / "workspaces" / tid / rid).resolve()
    _check_under_root(d)
    return d


def create_run_workspace(run_id: int) -> Path:
    """run 启动时建 scratch（幂等）。"""
    return scratch_dir(run_id)


def _resolve_in(root: Path, name: str) -> Path:
    """把相对名解析到 root 内（允许多级子目录，拒 .. 与绝对路径，resolve 校验）。"""
    norm = str(name).replace("\\", "/").lstrip("/")
    if norm == "" or ".." in norm.split("/"):
        raise ValueError(f"非法工作区文件名: {name}")
    p = (root / norm).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"工作区路径越界: {name}") from exc
    return p


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# 文件操作（沙箱内，原子写）
# ---------------------------------------------------------------------------


def write_file(run_id: int, name: str, content: str) -> str:
    """原子写：tmp + os.replace（防并发/崩溃半写）。覆盖已存在文件。返回相对名。"""
    root = scratch_dir(run_id)
    target = _resolve_in(root, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return _rel(root, target)


def read_file(run_id: int, name: str) -> str:
    root = scratch_dir(run_id)
    p = _resolve_in(root, name)
    if not p.is_file():
        raise FileNotFoundError(f"工作区无此文件: {name}")
    return p.read_text(encoding="utf-8")


def edit_file(run_id: int, name: str, old: str, new: str, *, replace_all: bool = False) -> int:
    """字符串替换（Claude Code Edit 语义）：old 须存在；多处需 replace_all。返回替换处数。"""
    content = read_file(run_id, name)
    if old == "" or old not in content:
        raise ValueError(f"old 片段未找到: {old[:40]!r}")
    count = content.count(old)
    if count > 1 and not replace_all:
        raise ValueError(f"old 匹配 {count} 处，须 replace_all=true 或收窄匹配")
    new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
    write_file(run_id, name, new_content)
    return count if replace_all else 1


def list_files(run_id: int) -> list[dict]:
    root = scratch_dir(run_id)
    if not root.exists():
        return []
    out: list[dict] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append({"name": _rel(root, p), "size": p.stat().st_size})
    return out


def move_file(run_id: int, old: str, new: str) -> str:
    root = scratch_dir(run_id)
    src = _resolve_in(root, old)
    dst = _resolve_in(root, new)
    if not src.is_file():
        raise FileNotFoundError(f"工作区无此文件: {old}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return _rel(root, dst)


def delete_file(run_id: int, name: str) -> bool:
    root = scratch_dir(run_id)
    p = _resolve_in(root, name)
    if p.is_file():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# commit / promote
# ---------------------------------------------------------------------------


def commit_run_workspace(db, run_id: int, thread_id: int) -> list:
    """run 完成时把 scratch 整体 move 到持久区，置 workspace_committed + 登记 RunArtifact。

    返回 RunArtifact 列表。空 scratch 也会置标志（run 无文件产出）。
    mid-commit 崩溃由 maintenance.cleanup_orphan_committed_dirs 兜（committed 区无有效 run/标志）。
    """
    from app.db.models import Run, RunArtifact

    run = db.get(Run, run_id)
    if run is None:
        raise ValueError(f"run 不存在: {run_id}")
    src = scratch_dir(run_id)
    dst = committed_dir(thread_id, run_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    has_files = src.exists() and any(src.rglob("*"))
    if has_files:
        if dst.exists():
            # Windows：rmtree 可能因文件锁（杀软/索引/已打开句柄）部分失败致 dst 残留，此时
            # shutil.move(src, dst) 会把 src 错位成 dst/<src名> 子目录 -> RunArtifact 路径全错。
            # 改用 copytree 合并（dirs_exist_ok）+ 清 src，不错位（iter 363）。
            shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            shutil.rmtree(src, ignore_errors=True)
        else:
            shutil.move(str(src), str(dst))
    else:
        dst.mkdir(parents=True, exist_ok=True)
    run.workspace_committed = True
    run.workspace_dir = dst.relative_to(_root()).as_posix()
    if has_files:
        for p in sorted(dst.rglob("*")):
            if p.is_file():
                rel = p.relative_to(dst).as_posix()
                # iter 364：跳过已登记的 artifact（promote_artifact 提前登记过同名文件，
                # 避免 awaiting_approval 与 approve 竞态致重复 RunArtifact）
                if db.query(RunArtifact).filter_by(run_id=run_id, name=rel).first() is not None:
                    continue
                db.add(
                    RunArtifact(
                        run_id=run_id,
                        thread_id=thread_id,
                        name=rel,
                        path=p.relative_to(_root()).as_posix(),
                        kind="committed",
                        size=p.stat().st_size,
                    )
                )
    db.commit()
    artifacts = db.query(RunArtifact).filter_by(run_id=run_id).all()
    # 清理已 move 的空 data/runs/<id>/ 外壳
    runs_shell = (_root() / "runs" / str(run_id))
    if runs_shell.exists() and not any(runs_shell.iterdir()):
        shutil.rmtree(runs_shell, ignore_errors=True)
    log.info("commit 工作区 run=%s -> %s（%d 文件）", run_id, dst, len(artifacts))
    return artifacts


def promote_to_path(src_committed_path: Path, dest_dir: Path, dest_name: str) -> Path:
    """把 committed 产物复制到目标目录（promote 底层原语；phase F 工具据 target_kind 拼目标）。

    dest_dir 须在 data_dir 内（复用 _check_under_root）；dest_name 仅 basename。
    """
    name = Path(dest_name).name  # 仅 basename，防穿越
    if not src_committed_path.is_file():
        raise FileNotFoundError(str(src_committed_path))
    _check_under_root(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / name
    shutil.copy2(src_committed_path, dst)
    return dst
