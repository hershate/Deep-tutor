"""iter 248 加固回归：materials 解析/删除竞态（iter 247 kb 同构补齐）、commit 失败孤儿、
上传限流、dedup 复用确定性、knowledge 导出截断。"""

from __future__ import annotations

import asyncio
import threading

from app.api import materials as mat_mod
from app.core.storage import _session_root
from app.db import models
from app.db.base import SessionLocal


def _seed():
    """建 admin + 会话 + 一份 file 类资料（带原件）；返回 id 集。"""
    db = SessionLocal()
    try:
        admin = models.User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="s")
        db.add(sess)
        db.flush()
        mat = models.Material(
            session_id=sess.id, kind="file", source="讲义.pdf", parsed_chars=0,
            meta={}, content_hash="hash-a", status="confirmed",
        )
        db.add(mat)
        db.commit()
        return {"admin_id": admin.id, "session_id": sess.id, "material_id": mat.id}
    finally:
        db.close()


def _write_original(session_id: int, material_id: int):
    originals = _session_root(session_id) / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    (originals / f"{material_id}.pdf").write_bytes(b"%PDF-fake")


def _stub_parse_blocking(monkeypatch, started, release, side_effect=None, text="正文"):
    """file 类解析桩：置 started -> 执行 side_effect -> 阻塞至 release。"""
    from app.tools.base import ToolOutput

    async def _fake(inp, ctx):
        if inp.args.get("kind") == "file":
            started.set()
            if side_effect is not None:
                side_effect()
            await asyncio.to_thread(release.wait, 10)
        return ToolOutput(tool="parse_material", status="ok", data={"text": text, "meta": {}})

    monkeypatch.setattr(mat_mod, "parse_material", _fake)


def _parsed_dir(session_id: int):
    return _session_root(session_id) / "parsed"


# --- 解析/删除竞态（删除侧 409）---


def test_delete_material_while_parsing_409(client, auth_headers, monkeypatch):
    ids = _seed()
    _write_original(ids["session_id"], ids["material_id"])
    started, release = threading.Event(), threading.Event()
    _stub_parse_blocking(monkeypatch, started, release)

    first: dict = {}

    def _parse_first():
        first["r"] = client.post(
            f"/api/materials/{ids['material_id']}/parse", json={"mode": "full"}, headers=auth_headers
        )

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "解析未开始"
        r = client.delete(
            f"/api/sessions/{ids['session_id']}/materials/{ids['material_id']}", headers=auth_headers
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    # 资料仍在且解析完成
    r = client.get(f"/api/sessions/{ids['session_id']}/materials", headers=auth_headers)
    assert r.json()["total"] == 1


def test_delete_session_while_parsing_409(client, auth_headers, monkeypatch):
    ids = _seed()
    _write_original(ids["session_id"], ids["material_id"])
    started, release = threading.Event(), threading.Event()
    _stub_parse_blocking(monkeypatch, started, release)

    first: dict = {}

    def _parse_first():
        first["r"] = client.post(
            f"/api/materials/{ids['material_id']}/parse", json={"mode": "full"}, headers=auth_headers
        )

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "解析未开始"
        r = client.delete(f"/api/sessions/{ids['session_id']}", headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text


# --- 解析完成侧身份校验与孤儿清理 ---


def test_parse_row_deleted_midflight_cleans_orphan(client, auth_headers, monkeypatch):
    """解析期间行被删（绕过端点直删，模拟竞态残余窗口）-> 404 且解析文件被清理。"""
    ids = _seed()
    _write_original(ids["session_id"], ids["material_id"])

    def _delete_row():
        db = SessionLocal()
        try:
            row = db.get(models.Material, ids["material_id"])
            db.delete(row)
            db.commit()
        finally:
            db.close()

    started, release = threading.Event(), threading.Event()
    release.set()
    _stub_parse_blocking(monkeypatch, started, release, side_effect=_delete_row)

    parsed_dir = _parsed_dir(ids["session_id"])
    before = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    r = client.post(f"/api/materials/{ids['material_id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 404
    after = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    assert after == before  # 无孤儿解析文件残留


def test_parse_rowid_reused_midflight_rejected(client, auth_headers, monkeypatch):
    """rowid 复用：解析期间行被删且新资料拿到同 id -> 409，新资料行不被错绑。"""
    ids = _seed()
    _write_original(ids["session_id"], ids["material_id"])

    def _delete_and_recreate():
        db = SessionLocal()
        try:
            row = db.get(models.Material, ids["material_id"])
            db.delete(row)
            db.flush()
            new = models.Material(
                id=ids["material_id"], session_id=ids["session_id"], kind="text",
                source="新资料", parsed_chars=0, meta={}, content_hash="hash-b",
                status="confirmed",
            )
            db.add(new)
            db.commit()
        finally:
            db.close()

    started, release = threading.Event(), threading.Event()
    release.set()
    _stub_parse_blocking(monkeypatch, started, release, side_effect=_delete_and_recreate)

    r = client.post(f"/api/materials/{ids['material_id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    db = SessionLocal()
    try:
        row = db.get(models.Material, ids["material_id"])
        assert row.content_hash == "hash-b"
        assert row.parsed_text_path is None and row.status == "confirmed"
    finally:
        db.close()


def test_parse_commit_failure_cleans_file(client, auth_headers, monkeypatch):
    """解析路径 commit 失败 -> 已落盘解析文本清理（此前连 try 都没有）。"""
    import pytest
    from sqlalchemy.orm import Session

    ids = _seed()
    _write_original(ids["session_id"], ids["material_id"])
    started, release = threading.Event(), threading.Event()
    release.set()
    _stub_parse_blocking(monkeypatch, started, release)

    def _boom(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Session, "commit", _boom)
    parsed_dir = _parsed_dir(ids["session_id"])
    before = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    with pytest.raises(RuntimeError, match="disk full"):  # TestClient 上抛服务端异常
        client.post(f"/api/materials/{ids['material_id']}/parse", json={"mode": "full"}, headers=auth_headers)
    after = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    assert after == before


# --- 上传限流 ---


def test_materials_upload_rate_limited(client, auth_headers, monkeypatch):
    ids = _seed()
    monkeypatch.setattr(mat_mod, "_UPLOAD_RATE_PER_MIN", 5)  # 缩小限流值避免撞每会话 20 条配额
    for i in range(5):
        r = client.post(
            "/api/materials/upload",
            data={"session_id": str(ids["session_id"])},
            files={"file": (f"f{i}.md", f"# doc{i}\n".encode(), "text/markdown")},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
    r = client.post(
        "/api/materials/upload",
        data={"session_id": str(ids["session_id"])},
        files={"file": ("f6.md", b"# doc6\n", "text/markdown")},
        headers=auth_headers,
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


# --- dedup 复用确定性（并发双建行下取最旧行）---


def test_dedup_lookup_returns_oldest_on_duplicates(client, auth_headers):
    """check-then-insert 竞态产生同 hash 双行时，去重复用取最旧行（结果确定）。"""
    ids = _seed()
    db = SessionLocal()
    try:
        older = models.Material(
            session_id=ids["session_id"], kind="text", source="旧", parsed_chars=1,
            meta={}, content_hash="dup-hash",
        )
        newer = models.Material(
            session_id=ids["session_id"], kind="text", source="新", parsed_chars=1,
            meta={}, content_hash="dup-hash",
        )
        db.add_all([older, newer])
        db.commit()
        db.refresh(older)
        db.refresh(newer)
        out = mat_mod._dedup_lookup(db, ids["session_id"], "dup-hash")
        assert out is not None and out.material_id == older.id
    finally:
        db.close()


# --- knowledge 导出截断 ---


def test_knowledge_export_truncates_over_2000_nodes(client, auth_headers, monkeypatch):
    ids = _seed()
    db = SessionLocal()
    try:
        plan = models.Plan(session_id=ids["session_id"], version=1, content={"intent": {"topic": "T"}})
        db.add(plan)
        db.flush()
        for i in range(25):  # 配合 monkeypatch 的缩小上限
            db.add(models.KnowledgeNode(plan_id=plan.id, sid=f"k{i}", name=f"节点{i}", difficulty=1))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr("app.api.knowledge._EXPORT_MAX_NODES", 10)
    r = client.get("/api/knowledge/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    text = r.content.decode("utf-8")
    assert text.count("### 节点") == 10  # 截断到上限
    assert "仅含最新 10 个" in text  # 截断提示


def test_knowledge_export_under_cap_no_truncation_note(client, auth_headers):
    ids = _seed()
    db = SessionLocal()
    try:
        plan = models.Plan(session_id=ids["session_id"], version=1, content={"intent": {"topic": "T"}})
        db.add(plan)
        db.flush()
        db.add(models.KnowledgeNode(plan_id=plan.id, sid="k1", name="节点甲", difficulty=2))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/knowledge/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    text = r.content.decode("utf-8")
    assert "### 节点甲" in text
    assert "仅含最新" not in text  # 未截断不出提示
