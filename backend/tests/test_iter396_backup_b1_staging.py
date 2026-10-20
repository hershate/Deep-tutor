"""iter 396 B1 回归：备份还原「文件落盘移出写事务」（暂存 + 事务内改名）。

问题：还原全程持 SQLite 写锁，而单文件「建+写」成本（Windows ~2.7ms）是对应
DB 插入（~0.2ms）的十余倍。文件 IO 落在事务内会把写锁持有时长放大一个数量级
（还原上限 10 万行 -> 可产生十万级文件），期间全服务写请求等满
busy_timeout(10s) 后 500。

修法（两段式）：
1. 预暂存（事务外，写锁未持有）：内容写到 ``data/.restore-staging/<uuid>/``；
2. 改名（事务内）：拿到新行 id 后 ``os.replace`` 到最终路径（元数据操作）。

本文件验证：
- 内容落盘全部发生在**第一条 INSERT 之前**（即写锁尚未持有）；
- 改名后最终文件内容正确（含 >500 行的 .pN 拆分、kb 原件与解析文本）；
- 暂存缺失时回退为事务内直写，数据不丢；
- 事务失败时已改名的文件被补偿删除（kb 原件孤儿不被 maintenance 回收）；
- 暂存目录不残留。
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from sqlalchemy import event

from app.core import backup as backup_mod
from app.core import storage as storage_mod
from app.db import models
from app.db.base import SessionLocal

_PAYLOAD = "deep-tutor-backup"


def _mk_user(name: str) -> tuple[int, str]:
    from app.core.security import create_token

    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, create_token(u.id, name)
    finally:
        db.close()


def _import(client, backup: dict, headers: dict):
    return client.post(
        "/api/backup/import",
        files={"file": ("b.json", json.dumps(backup), "application/json")},
        headers=headers,
    )


def _base(**kw) -> dict:
    b = {
        "format": _PAYLOAD,
        "version": 2,
        "sessions": [],
        "threads": [],
        "memories": [],
        "kb_documents": [],
        "provider_profiles": [],
    }
    b.update(kw)
    return b


def _rich_backup(*, unit_md: str = "### 小节\n\n正文") -> dict:
    """一份覆盖全部落盘路径的备份：解析文本 / 单元内容 / kb 解析文本 / kb 原件。"""
    return _base(
        sessions=[
            {
                "title": "s1",
                "materials": [
                    {
                        "kind": "file",
                        "source": "a.txt",
                        "status": "parsed",
                        "parsed_text": "资料解析正文",
                        "parsed_chars": 6,
                    }
                ],
                "plans": [
                    {
                        "content": {"intent": {"topic": "T"}},
                        "version": 1,
                        "knowledge_nodes": [],
                        "resources": [],
                        "progress": [],
                        "learning_logs": [],
                        "quizzes": [],
                        "study_units": [
                            {
                                "node_sid": None,
                                "title": "单元一",
                                "content_md": unit_md,
                                "material_resources": [],
                            }
                        ],
                    }
                ],
            }
        ],
        kb_documents=[
            {
                "title": "kb1",
                "kind": "file",
                "ext": "pdf",
                "source": "k.pdf",
                "status": "parsed",
                "parsed_text": "kb 解析正文",
                "parsed_chars": 6,
                "original_b64": base64.b64encode(b"PDF-BYTES").decode("ascii"),
            }
        ],
    )


# ---------------------------------------------------------------------------
# 1. 内容落盘在事务外（核心断言）
# ---------------------------------------------------------------------------


def test_file_content_written_before_first_insert(client, monkeypatch):
    """所有文件「建+写」必须发生在第一条 INSERT 之前（写锁尚未持有）。"""
    uid, tok = _mk_user("bk_b1_order")
    h = {"Authorization": f"Bearer {tok}"}

    writes: list[tuple[float, str]] = []
    orig_write = storage_mod._atomic_write_bytes

    def spy_write(path, content):
        writes.append((time.perf_counter(), str(path)))
        return orig_write(path, content)

    monkeypatch.setattr(storage_mod, "_atomic_write_bytes", spy_write)

    inserts: list[float] = []

    def _on_exec(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(time.perf_counter())

    from app.db.base import engine

    event.listen(engine, "before_cursor_execute", _on_exec)
    try:
        r = _import(client, _rich_backup(), h)
    finally:
        event.remove(engine, "before_cursor_execute", _on_exec)

    assert r.status_code == 200, r.text[:400]
    assert writes, "未捕获到任何文件写入（暂存路径未生效？）"
    assert inserts, "未捕获到 INSERT（测试前提失效）"
    last_write = max(t for t, _ in writes)
    first_insert = min(inserts)
    assert last_write < first_insert, (
        f"有 {sum(1 for t, _ in writes if t > first_insert)} 次文件写入落在事务内"
        f"（首个 INSERT @ {first_insert:.6f}，写入最晚 @ {last_write:.6f}）"
    )


def test_staging_files_live_under_data_dir(client, monkeypatch):
    """暂存文件必须落在 data_dir 下（与最终路径同卷，os.replace 才可用）。"""
    uid, tok = _mk_user("bk_b1_vol")
    h = {"Authorization": f"Bearer {tok}"}
    staged: list[str] = []
    orig = backup_mod.atomic_write_bytes

    def spy(path, content):
        staged.append(str(path))
        return orig(path, content)

    monkeypatch.setattr(backup_mod, "atomic_write_bytes", spy)
    r = _import(client, _rich_backup(), h)
    assert r.status_code == 200, r.text[:400]
    assert staged, "未走暂存路径"
    root = Path(storage_mod.data_root())
    for p in staged:
        assert root in Path(p).parents or Path(p).parent == root, p


# ---------------------------------------------------------------------------
# 2. 改名后最终文件内容正确
# ---------------------------------------------------------------------------


def test_restored_files_have_correct_content(client):
    """暂存->改名后，解析文本 / 单元内容 / kb 文本 / kb 原件内容均正确。"""
    uid, tok = _mk_user("bk_b1_content")
    h = {"Authorization": f"Bearer {tok}"}
    r = _import(client, _rich_backup(), h)
    assert r.status_code == 200, r.text[:400]

    db = SessionLocal()
    try:
        sess = db.query(models.Session).filter_by(user_id=uid).one()
        mat = db.query(models.Material).filter_by(session_id=sess.id).one()
        assert mat.parsed_text_path
        assert Path(mat.parsed_text_path).read_text(encoding="utf-8") == "资料解析正文"

        plan = db.query(models.Plan).filter_by(session_id=sess.id).one()
        unit = db.query(models.StudyUnit).filter_by(plan_id=plan.id).one()
        sm = db.query(models.StudyMaterial).filter_by(unit_id=unit.id).one()
        assert Path(sm.content_md_path).read_text(encoding="utf-8") == "### 小节\n\n正文"

        doc = db.query(models.KbDocument).filter_by(user_id=uid).one()
        assert Path(doc.parsed_text_path).read_text(encoding="utf-8") == "kb 解析正文"
        assert Path(doc.original_path).read_bytes() == b"PDF-BYTES"
    finally:
        db.close()


def test_long_unit_content_split_parts_restored(client):
    """>500 行的单元内容在改名路径下同样拆出 .pN 子文件（读回内容完整）。"""
    uid, tok = _mk_user("bk_b1_parts")
    h = {"Authorization": f"Bearer {tok}"}
    long_md = "\n".join(f"### 第{i}节\n\n第{i}行内容" for i in range(300))
    assert long_md.count("\n") + 1 > 500
    r = _import(client, _rich_backup(unit_md=long_md), h)
    assert r.status_code == 200, r.text[:400]

    db = SessionLocal()
    try:
        sess = db.query(models.Session).filter_by(user_id=uid).one()
        plan = db.query(models.Plan).filter_by(session_id=sess.id).one()
        unit = db.query(models.StudyUnit).filter_by(plan_id=plan.id).one()
        sm = db.query(models.StudyMaterial).filter_by(unit_id=unit.id).one()
        main = Path(sm.content_md_path)
        assert main.exists()
        assert main.with_name(f"{main.stem}.p2.md").exists(), "缺少 .p2 拆分子文件"
        # 拆分规则由 _split_at_headings 决定（按小节边界装箱、以空行重组），
        # 故期望值是「同样规则的分段结果」，而非原始字节
        expected = "\n\n".join(storage_mod.unit_content_parts(long_md))
        assert storage_mod.read_unit_content(main) == expected
        assert "第299节" in expected  # 全部小节都在（未截断）
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. 回退路径：暂存缺失 -> 事务内直写（数据不丢）
# ---------------------------------------------------------------------------


def test_falls_back_to_direct_write_when_staging_missing(client, monkeypatch):
    """改名失败（暂存缺失）时回退为事务内直写，内容不得丢失。"""
    uid, tok = _mk_user("bk_b1_fallback")
    h = {"Authorization": f"Bearer {tok}"}
    monkeypatch.setattr(backup_mod._StagedFiles, "promote", lambda *a, **kw: False)
    r = _import(client, _rich_backup(), h)
    assert r.status_code == 200, r.text[:400]

    db = SessionLocal()
    try:
        sess = db.query(models.Session).filter_by(user_id=uid).one()
        mat = db.query(models.Material).filter_by(session_id=sess.id).one()
        assert Path(mat.parsed_text_path).read_text(encoding="utf-8") == "资料解析正文"
        doc = db.query(models.KbDocument).filter_by(user_id=uid).one()
        assert Path(doc.original_path).read_bytes() == b"PDF-BYTES"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. 事务失败 -> 已改名文件被补偿删除；暂存目录不残留
# ---------------------------------------------------------------------------


def test_failed_import_compensates_promoted_files(client, monkeypatch):
    """导入中途失败：已改名的文件（含 kb 原件）必须被补偿删除，不留孤儿。"""
    uid, tok = _mk_user("bk_b1_rollback")
    h = {"Authorization": f"Bearer {tok}"}
    orig = backup_mod.restore_backup

    def boom(*args, **kwargs):
        orig(*args, **kwargs)
        raise RuntimeError("注入失败")

    monkeypatch.setattr(backup_mod, "restore_backup", boom)
    r = _import(client, _rich_backup(), h)
    assert r.status_code == 500, r.text[:200]

    db = SessionLocal()
    try:
        assert db.query(models.Session).filter_by(user_id=uid).count() == 0
        assert db.query(models.KbDocument).filter_by(user_id=uid).count() == 0
    finally:
        db.close()

    root = Path(storage_mod.data_root())
    kb_dir = root / "kb" / str(uid)
    if kb_dir.exists():
        originals = list((kb_dir / "originals").glob("*")) if (kb_dir / "originals").exists() else []
        parsed = list((kb_dir / "parsed").glob("*")) if (kb_dir / "parsed").exists() else []
        assert originals == [], f"kb 原件孤儿未补偿：{originals}"
        assert parsed == [], f"kb 解析文本孤儿未补偿：{parsed}"


def test_staging_dir_removed_after_restore(client):
    """还原结束后暂存目录不得残留（含空父目录）。"""
    uid, tok = _mk_user("bk_b1_cleanup")
    h = {"Authorization": f"Bearer {tok}"}
    r = _import(client, _rich_backup(), h)
    assert r.status_code == 200, r.text[:400]
    root = Path(storage_mod.data_root())
    staging = root / ".restore-staging"
    assert not staging.exists() or not any(staging.iterdir()), "暂存目录残留"


@pytest.mark.parametrize("payload", [{}, {"sessions": [{"title": "x", "materials": [], "plans": []}]}])
def test_restore_without_files_leaves_no_staging(client, payload):
    """无文件载荷的导入同样不得留下暂存目录。"""
    uid, tok = _mk_user("bk_b1_empty")
    h = {"Authorization": f"Bearer {tok}"}
    r = _import(client, _base(**payload), h)
    assert r.status_code == 200, r.text[:400]
    root = Path(storage_mod.data_root())
    staging = root / ".restore-staging"
    assert not staging.exists() or not any(staging.iterdir()), "暂存目录残留"
