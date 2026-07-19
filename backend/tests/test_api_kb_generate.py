"""知识库一键生成学习方案测试（iter 215：POST /kb/generate）。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.db import models

BASE = "/api/kb"


def _db():
    from app.db.base import SessionLocal

    return SessionLocal()


def _mk_text_doc(client, headers, text="知识库笔记", title=None) -> dict:
    payload = {"text": text}
    if title is not None:
        payload["title"] = title
    r = client.post(f"{BASE}/documents/text", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _session_count() -> int:
    db = _db()
    try:
        return db.query(models.Session).count()
    finally:
        db.close()


def _materials_of(session_id: int) -> list[tuple[str, str, dict, str | None]]:
    db = _db()
    try:
        rows = db.query(models.Material).filter_by(session_id=session_id).order_by(models.Material.id).all()
        return [(m.kind.value, m.source, m.meta or {}, m.parsed_text_path) for m in rows]
    finally:
        db.close()


# --- 主流程 ---


def test_generate_creates_session_and_materials(client, auth_headers):
    d1 = _mk_text_doc(client, auth_headers, "第一篇文档内容", title="文档一")
    d2 = _mk_text_doc(client, auth_headers, "第二篇文档内容", title="文档二")
    d3 = _mk_text_doc(client, auth_headers, "第三篇文档内容", title="文档三")

    r = client.post(f"{BASE}/generate", json={"document_ids": [d1["id"], d2["id"], d3["id"]]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["material_count"] == 3
    assert body["open_path"] == f"/generate/{body['session_id']}"

    # 会话归属与缺省标题（知识库：{首文档标题}）
    db = _db()
    try:
        sess = db.get(models.Session, body["session_id"])
        assert sess is not None and sess.title == "知识库：文档一"
        uid = sess.user_id
        db.expunge(sess)
    finally:
        db.close()

    mats = _materials_of(body["session_id"])
    assert len(mats) == 3
    for kind, source, meta, parsed_path in mats:
        assert kind == "text"
        assert meta.get("kb_document_id") in {d1["id"], d2["id"], d3["id"]}
        assert parsed_path and Path(parsed_path).exists()
    # 解析文本逐字复制（生成管线注入源 == 知识库解析文本）
    texts = sorted(Path(p).read_text(encoding="utf-8") for _, _, _, p in mats)
    assert texts == sorted(["第一篇文档内容", "第二篇文档内容", "第三篇文档内容"])

    # 零改动复用验证：_load_parsed_materials 直调即得 3 份注入文本
    from app.api.plans import _load_parsed_materials

    db = _db()
    try:
        parsed = _load_parsed_materials(db, body["session_id"])
    finally:
        db.close()
    assert len(parsed) == 3
    assert all(p["text"] for p in parsed)

    # 会话归属当前用户（经会话列表 API 复查）
    r = client.get("/api/sessions", headers=auth_headers)
    assert any(s["session_id"] == body["session_id"] for s in r.json()["items"])
    assert uid > 0


def test_generate_explicit_title(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "x")
    r = client.post(
        f"{BASE}/generate", json={"document_ids": [d["id"]], "title": "我的自定义方案"}, headers=auth_headers
    )
    assert r.status_code == 200
    db = _db()
    try:
        assert db.get(models.Session, r.json()["session_id"]).title == "我的自定义方案"
    finally:
        db.close()


def test_generate_dedup_within_request(client, auth_headers):
    """document_ids 内重复 id -> 仅建一份资料。"""
    d1 = _mk_text_doc(client, auth_headers, "唯一文档")
    d2 = _mk_text_doc(client, auth_headers, "另一文档")
    r = client.post(f"{BASE}/generate", json={"document_ids": [d1["id"], d1["id"], d2["id"]]}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["material_count"] == 2


def test_generate_other_user_doc_404_no_orphan(client, auth_headers):
    """含他人/不存在文档 -> 404，且无孤儿会话（行+目录）。"""
    from app.core.security import hash_password

    d = _mk_text_doc(client, auth_headers, "私有文档")
    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    hb = {
        "Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'bob', 'password': 'pw1234'}).json()['token']}"
    }
    before = _session_count()
    r = client.post(f"{BASE}/generate", json={"document_ids": [d["id"]]}, headers=hb)
    assert r.status_code == 404
    assert _session_count() == before

    # 不存在的 id 同理
    r = client.post(f"{BASE}/generate", json={"document_ids": [999999]}, headers=auth_headers)
    assert r.status_code == 404
    assert _session_count() == before


def test_generate_ids_validation_422(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "x")
    assert client.post(f"{BASE}/generate", json={"document_ids": []}, headers=auth_headers).status_code == 422
    assert (
        client.post(f"{BASE}/generate", json={"document_ids": [d["id"]] * 21}, headers=auth_headers).status_code
        == 422
    )


def test_generate_session_quota_429(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.kb._MAX_SESSIONS_PER_USER", 1)
    d = _mk_text_doc(client, auth_headers, "x")
    r1 = client.post(f"{BASE}/generate", json={"document_ids": [d["id"]]}, headers=auth_headers)
    assert r1.status_code == 200
    r2 = client.post(f"{BASE}/generate", json={"document_ids": [d["id"]]}, headers=auth_headers)
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "quota_exceeded"


def test_generate_unauthenticated_401(client):
    assert client.post(f"{BASE}/generate", json={"document_ids": [1]}).status_code == 401


def test_generate_upload_doc_kind_and_source(client, auth_headers):
    """上传类文档：kind= file、source=原文件名、meta 溯源 kb_document_id。"""
    r = client.post(
        f"{BASE}/documents/upload",
        files={"file": ("指南.md", BytesIO("# 指南\n\n内容".encode("utf-8")), "text/markdown")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    r = client.post(f"{BASE}/generate", json={"document_ids": [doc["id"]]}, headers=auth_headers)
    assert r.status_code == 200
    mats = _materials_of(r.json()["session_id"])
    assert len(mats) == 1
    kind, source, meta, _ = mats[0]
    assert kind == "file" and source == "指南.md"
    assert meta.get("kb_document_id") == doc["id"]
