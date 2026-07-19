"""知识库问答记录持久化测试（iter 220：/kb/chat 落 kb_chat_records + GET/DELETE /kb/chats）。"""

from __future__ import annotations

import json

from app.db import models
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

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


def _records() -> list[models.KbChatRecord]:
    db = _db()
    try:
        return db.query(models.KbChatRecord).order_by(models.KbChatRecord.id).all()
    finally:
        db.close()


class _ChatProvider(LLMProvider):
    """问答罐头 provider：返回固定 JSON（同 test_api_kb_chat 范式）。"""

    def __init__(self, content: str):
        self._content = content

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return LLMResponse(
            content=self._content,
            usage=LLMUsage(input_tokens=10, output_tokens=5, model="claude-sonnet-5"),
        )


def _patch_provider(monkeypatch, provider):
    from app.api import kb as kb_mod

    monkeypatch.setattr(kb_mod, "get_provider", lambda *a, **k: provider)


def _chat_ok(client, headers, monkeypatch, question="Python 是什么？", document_ids=None, answer="答", used=None):
    provider = _ChatProvider(json.dumps({"answer": answer, "used_doc_ids": used or []}))
    _patch_provider(monkeypatch, provider)
    body = {"question": question}
    if document_ids is not None:
        body["document_ids"] = document_ids
    r = client.post(f"{BASE}/chat", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- 持久化 ---


def test_chat_success_persists_record(client, auth_headers, monkeypatch):
    d1 = _mk_text_doc(client, auth_headers, "Python 是一种解释型语言", title="Python 入门")
    body = _chat_ok(
        client, auth_headers, monkeypatch,
        answer="根据《Python 入门》，Python 是解释型语言。", used=[d1["id"]],
    )
    assert body["record_id"] > 0

    rows = _records()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == body["record_id"]
    assert r.question == "Python 是什么？"
    assert "解释型语言" in r.answer
    assert r.citations == [{"doc_id": d1["id"], "title": "Python 入门"}]
    assert r.omitted_titles == []
    assert r.doc_ids == []  # 缺省全库上下文
    assert r.context_docs == 1
    assert r.created_at is not None


def test_chat_records_scoped_doc_ids(client, auth_headers, monkeypatch):
    d1 = _mk_text_doc(client, auth_headers, "第一篇", title="A")
    d2 = _mk_text_doc(client, auth_headers, "第二篇", title="B")
    _chat_ok(client, auth_headers, monkeypatch, document_ids=[d1["id"], d2["id"], d1["id"]])
    rows = _records()
    assert len(rows) == 1
    assert rows[0].doc_ids == [d1["id"], d2["id"]]  # 请求侧保序去重后的实际上下文


def test_chat_failure_not_recorded(client, auth_headers, monkeypatch):
    """LLM 失败（502）不落记录——无答案可存。"""
    _mk_text_doc(client, auth_headers, "有文档")
    _patch_provider(monkeypatch, _ChatProvider("这不是 JSON"))  # schema 重试耗尽
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 502
    assert _records() == []


def test_chat_validation_failure_not_recorded(client, auth_headers, monkeypatch):
    """无可用文档（422）不落记录。"""
    _patch_provider(monkeypatch, _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []})))
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 422
    assert _records() == []


# --- GET /kb/chats ---


def test_list_chats_order_and_fields(client, auth_headers, monkeypatch):
    _mk_text_doc(client, auth_headers, "文档")
    _chat_ok(client, auth_headers, monkeypatch, question="第一问")
    _chat_ok(client, auth_headers, monkeypatch, question="第二问")

    r = client.get(f"{BASE}/chats", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [i["question"] for i in body["items"]] == ["第二问", "第一问"]  # id desc
    item = body["items"][0]
    assert set(item) == {
        "id", "question", "answer", "citations", "omitted_titles", "doc_ids", "context_docs", "created_at",
    }
    assert item["created_at"]


def test_list_chats_q_filter(client, auth_headers, monkeypatch):
    _mk_text_doc(client, auth_headers, "文档")
    _chat_ok(client, auth_headers, monkeypatch, question="Python 入门怎么学")
    _chat_ok(client, auth_headers, monkeypatch, question="番茄钟是什么")

    r = client.get(f"{BASE}/chats?q=Python", headers=auth_headers)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["question"] == "Python 入门怎么学"


def test_list_chats_pagination(client, auth_headers, monkeypatch):
    _mk_text_doc(client, auth_headers, "文档")
    for i in range(3):
        _chat_ok(client, auth_headers, monkeypatch, question=f"问题{i}")

    r = client.get(f"{BASE}/chats?limit=2&offset=2", headers=auth_headers)
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["items"][0]["question"] == "问题0"  # id desc 第 3 条 = 最早一问


def test_list_chats_isolated_per_user(client, auth_headers, monkeypatch):
    from app.core.security import hash_password

    _mk_text_doc(client, auth_headers, "文档")
    _chat_ok(client, auth_headers, monkeypatch, question="我的问题")

    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    hb = {
        "Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'bob', 'password': 'pw1234'}).json()['token']}"
    }
    r = client.get(f"{BASE}/chats", headers=hb)
    assert r.json() == {"items": [], "total": 0}


def test_list_chats_unauthenticated_401(client):
    assert client.get(f"{BASE}/chats").status_code == 401


# --- DELETE /kb/chats/{id} ---


def test_delete_chat_record(client, auth_headers, monkeypatch):
    _mk_text_doc(client, auth_headers, "文档")
    body = _chat_ok(client, auth_headers, monkeypatch)

    r = client.delete(f"{BASE}/chats/{body['record_id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert _records() == []


def test_delete_chat_record_not_found_404(client, auth_headers):
    assert client.delete(f"{BASE}/chats/999999", headers=auth_headers).status_code == 404


def test_delete_chat_record_other_user_404(client, auth_headers, monkeypatch):
    """他人记录一律 404（防枚举，同文档删除）。"""
    from app.core.security import hash_password

    _mk_text_doc(client, auth_headers, "文档")
    body = _chat_ok(client, auth_headers, monkeypatch)

    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    hb = {
        "Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'bob', 'password': 'pw1234'}).json()['token']}"
    }
    assert client.delete(f"{BASE}/chats/{body['record_id']}", headers=hb).status_code == 404
    assert len(_records()) == 1  # 原记录未受影响


def test_delete_chat_record_unauthenticated_401(client):
    assert client.delete(f"{BASE}/chats/1").status_code == 401


# --- 删用户级联 ---


def test_delete_user_cascades_chat_records(client, auth_headers, monkeypatch):
    from app.core.security import hash_password

    db = _db()
    try:
        db.add(models.User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.add(models.User(name="chu", username="chu", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    ha = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'root', 'password': 'pw1234'}).json()['token']}"}
    hu = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'chu', 'password': 'pw1234'}).json()['token']}"}

    _mk_text_doc(client, hu, "chu 的文档")
    _chat_ok(client, hu, monkeypatch, question="chu 的问题")
    rows = _records()
    assert len(rows) == 1
    uid = rows[0].user_id

    r = client.delete(f"/api/users/{uid}", headers=ha)
    assert r.status_code == 200, r.text
    assert _records() == []  # ORM 级联删除（无文件资产）
