"""会话源资料管理端点测试（iter 104）：清单 / 单个删除 / 用户隔离 / 运行态 409 / 文件清理。"""

from __future__ import annotations

from app.core.storage import _session_root
from app.db.base import SessionLocal


def _seed(with_other: bool = False):
    """建 admin（+可选 other）用户、会话、两条资料；返回 id 集。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        other = None
        if with_other:
            other = User(name="other", username="other", password_hash="x", is_admin=False)
            db.add(other)
        db.flush()
        sess = models.Session(user_id=admin.id, title="资料会话")
        db.add(sess)
        db.flush()
        m1 = models.Material(
            session_id=sess.id, kind="text", source="第一段资料", parsed_chars=100, meta={}
        )
        m2 = models.Material(
            session_id=sess.id,
            kind="file",
            source="讲义.pdf",
            parsed_chars=200,
            meta={"filename": "讲义.pdf"},
        )
        db.add_all([m1, m2])
        db.commit()
        return {
            "admin_id": admin.id,
            "other_id": other.id if other else None,
            "session_id": sess.id,
            "m1": m1.id,
            "m2": m2.id,
        }
    finally:
        db.close()


def _write_material_files(session_id: int, material_id: int):
    """模拟上传/解析落盘：原件 + 解析文本。"""
    root = _session_root(session_id)
    originals = root / "originals"
    parsed = root / "parsed"
    originals.mkdir(parents=True, exist_ok=True)
    parsed.mkdir(parents=True, exist_ok=True)
    (originals / f"{material_id}.pdf").write_bytes(b"%PDF-fake")
    (parsed / f"{material_id}.txt").write_text("解析文本", encoding="utf-8")
    return originals / f"{material_id}.pdf", parsed / f"{material_id}.txt"


def test_list_session_materials(client, auth_headers):
    ids = _seed()
    r = client.get(f"/api/sessions/{ids['session_id']}/materials", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [i["material_id"] for i in body["items"]] == [ids["m1"], ids["m2"]]  # id 升序
    by_id = {i["material_id"]: i for i in body["items"]}
    assert by_id[ids["m1"]]["kind"] == "text" and by_id[ids["m1"]]["parsed_chars"] == 100
    assert by_id[ids["m2"]]["meta"]["filename"] == "讲义.pdf"


def test_delete_material_removes_row_and_files(client, auth_headers):
    ids = _seed()
    sid = ids["session_id"]
    original, parsed = _write_material_files(sid, ids["m2"])
    # 干扰项：id 前缀相邻的文件不得被误删（"2.*" 不得删到 "20.x"）
    decoy = _session_root(sid) / "originals" / f"{ids['m2']}0.txt"
    decoy.write_text("decoy", encoding="utf-8")

    r = client.delete(f"/api/sessions/{sid}/materials/{ids['m2']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert not original.exists() and not parsed.exists()
    assert decoy.exists()  # 前缀相邻文件保留

    # DB 行已删，清单只剩 m1；重复删除 -> 404
    r = client.get(f"/api/sessions/{sid}/materials", headers=auth_headers)
    assert [i["material_id"] for i in r.json()["items"]] == [ids["m1"]]
    r = client.delete(f"/api/sessions/{sid}/materials/{ids['m2']}", headers=auth_headers)
    assert r.status_code == 404


def test_delete_material_missing_files_ok(client, auth_headers):
    """磁盘文件本就不存在时删除照常成功（幂等清理）。"""
    ids = _seed()
    r = client.delete(
        f"/api/sessions/{ids['session_id']}/materials/{ids['m1']}", headers=auth_headers
    )
    assert r.status_code == 200, r.text


def test_materials_user_isolation(client, auth_headers):
    """他人会话的资料：列表/删除均 404（不泄露存在性）。"""
    from app.db import models

    ids = _seed(with_other=True)
    db = SessionLocal()
    try:
        other_sess = models.Session(user_id=ids["other_id"], title="他人会话")
        db.add(other_sess)
        db.flush()
        other_mat = models.Material(
            session_id=other_sess.id, kind="text", source="他人资料", parsed_chars=1
        )
        db.add(other_mat)
        db.commit()
        other_sid, other_mid = other_sess.id, other_mat.id
    finally:
        db.close()

    r = client.get(f"/api/sessions/{other_sid}/materials", headers=auth_headers)
    assert r.status_code == 404
    r = client.delete(f"/api/sessions/{other_sid}/materials/{other_mid}", headers=auth_headers)
    assert r.status_code == 404
    # 自己的会话 + 他人资料 id（跨会话引用）-> 404
    r = client.delete(
        f"/api/sessions/{ids['session_id']}/materials/{other_mid}", headers=auth_headers
    )
    assert r.status_code == 404


def test_delete_material_conflict_while_running(client, auth_headers):
    """会话生成中删除资料 -> 409（与 plans.py R33 运行态冲突同模式）。"""
    from app.db import models

    ids = _seed()
    db = SessionLocal()
    try:
        db.add(
            models.OrchestratorState(
                session_id=ids["session_id"], status=models.OrchestratorStatus.running
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.delete(
        f"/api/sessions/{ids['session_id']}/materials/{ids['m1']}", headers=auth_headers
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    # 资料仍在
    r = client.get(f"/api/sessions/{ids['session_id']}/materials", headers=auth_headers)
    assert r.json()["total"] == 2


def test_materials_require_auth(client):
    assert client.get("/api/sessions/1/materials").status_code == 401
    assert client.delete("/api/sessions/1/materials/1").status_code == 401
