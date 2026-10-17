"""Agent 工作区单元测试（iter 345）—— 沙箱文件操作 / 原子写 / 路径穿越防护 / commit / promote / 孤儿清理。"""

from __future__ import annotations

import pytest

from app.agent import workspace as ws
from app.db import models
from app.db.base import SessionLocal


def _setup_run():
    db = SessionLocal()
    u = models.User(name="u", username="u", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running)
    db.add(r)
    db.commit()
    db.refresh(r)
    return db, u, t, r


def test_write_read_edit_list_move(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    # 指向临时数据目录（隔离）
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    # write + read
    assert ws.write_file(r.id, "plan.md", "# 标题\n正文") == "plan.md"
    assert ws.read_file(r.id, "plan.md") == "# 标题\n正文"
    # 子目录
    ws.write_file(r.id, "notes/a.md", "嵌套")
    assert ws.read_file(r.id, "notes/a.md") == "嵌套"
    # edit（唯一匹配）
    n = ws.edit_file(r.id, "plan.md", "标题", "Title")
    assert n == 1 and "Title" in ws.read_file(r.id, "plan.md")
    # edit 多处需 replace_all
    ws.write_file(r.id, "dup.md", "x x x")
    with pytest.raises(ValueError):
        ws.edit_file(r.id, "dup.md", "x", "y")
    assert ws.edit_file(r.id, "dup.md", "x", "y", replace_all=True) == 3
    # edit old 不存在
    with pytest.raises(ValueError):
        ws.edit_file(r.id, "plan.md", "不存在片段", "z")
    # list
    names = {f["name"] for f in ws.list_files(r.id)}
    assert {"plan.md", "notes/a.md", "dup.md"} <= names
    # move
    ws.move_file(r.id, "dup.md", "moved/dup.md")
    assert "moved/dup.md" in {f["name"] for f in ws.list_files(r.id)}
    db.close()


def test_path_traversal_blocked(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ws.write_file(r.id, "ok.md", "ok")
    for bad in ["../escape.md", "a/../../b.md", "..", "x/../../../y.md"]:
        with pytest.raises(ValueError):
            ws.write_file(r.id, bad, "x")
    # 注意："/etc/passwd" 经 lstrip("/") -> "etc/passwd" 落在沙箱内（安全，非穿越），故不在此列
    # 非法 run_id
    with pytest.raises(ValueError):
        ws.scratch_dir("abc")
    db.close()


def test_atomic_write_no_tmp_left(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ws.write_file(r.id, "f.md", "v1")
    ws.write_file(r.id, "f.md", "v2")  # 覆盖
    scratch = ws.scratch_dir(r.id)
    tmps = [p for p in scratch.rglob("*.tmp")]
    assert tmps == []  # 无残留 tmp
    assert ws.read_file(r.id, "f.md") == "v2"
    db.close()


def test_commit_moves_to_persistent_and_registers_artifacts(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ws.write_file(r.id, "plan.md", "内容")
    ws.write_file(r.id, "drafts/d.md", "草稿")
    arts = ws.commit_run_workspace(db, r.id, t.id)
    names = {a.name for a in arts}
    assert {"plan.md", "drafts/d.md"} <= names
    run = db.get(models.Run, r.id)
    assert run.workspace_committed is True
    assert run.workspace_dir.startswith("workspaces/")
    # committed 区存在；scratch 内容已 move 走（list_files 会重建空目录，应为空）
    assert ws.committed_dir(t.id, r.id).is_dir()
    assert ws.list_files(r.id) == []
    # DB artifacts
    rows = db.query(models.RunArtifact).filter_by(run_id=r.id).all()
    assert len(rows) == 2 and all(a.kind == "committed" for a in rows)
    db.close()


def test_promote_to_path_copies(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ws.write_file(r.id, "plan.md", "提升")
    ws.commit_run_workspace(db, r.id, t.id)
    art = db.query(models.RunArtifact).filter_by(run_id=r.id, name="plan.md").one()
    src = tmp_path / art.path
    dest_dir = tmp_path / "exports"
    out = ws.promote_to_path(src, dest_dir, "final-plan.md")
    assert out.read_text(encoding="utf-8") == "提升"
    # 仅 basename（防穿越）
    out2 = ws.promote_to_path(src, dest_dir, "../escape.md")
    assert out2.name == "escape.md"
    db.close()


def test_cleanup_orphan_run_dirs(tmp_path, monkeypatch):
    db, u, t, r = _setup_run()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    # r 仍 running -> scratch 保留
    ws.write_file(r.id, "in-progress.md", "x")
    # 造一个孤儿 run 目录（DB 无对应 running run）；iter 368 起孤儿删除带 24h mtime
    # 冷却（备份恢复窗口保护）——回拨到窗外使其成为可删候选
    orphan = tmp_path / "runs" / "999999" / "workspace"
    orphan.mkdir(parents=True)
    (orphan / "stale.md").write_text("旧")
    import os
    import time

    old = time.time() - 25 * 3600
    os.utime(tmp_path / "runs" / "999999", (old, old))
    os.utime(orphan, (old, old))
    os.utime(orphan / "stale.md", (old, old))
    from app.core.maintenance import cleanup_orphan_run_dirs

    removed = cleanup_orphan_run_dirs(str(tmp_path))
    assert removed >= 1
    assert not orphan.exists()
    assert ws.scratch_dir(r.id).exists()  # running run 的 scratch 保留
    db.close()


def test_startup_converges_stale_running_runs(tmp_path, monkeypatch):
    """main.py lifespan 的 runs 收敛逻辑：running -> interrupted。直接验证 SQL 等价。"""
    from sqlalchemy import update

    db, u, t, r = _setup_run()
    # 模拟崩溃：r 仍 running
    assert r.status == models.RunStatus.running
    db.execute(
        update(models.Run)
        .where(models.Run.status == models.RunStatus.running)
        .values(status=models.RunStatus.interrupted)
    )
    db.commit()
    assert db.get(models.Run, r.id).status == models.RunStatus.interrupted
    db.close()
