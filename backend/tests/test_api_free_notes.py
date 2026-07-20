"""独立自由笔记 API 测试（iter 227：/notes CRUD + 隔离 + 配额 + 校验）。

区别于 test_api_notes.py（知识点批注 NodeNote，挂知识节点）——本文件测自由笔记本。
"""

from __future__ import annotations

from app.core.security import hash_password
from app.db import models
from app.db.base import SessionLocal

BASE = "/api/notes"


def _mk(username: str, *, is_admin: bool = False) -> None:
    db = SessionLocal()
    try:
        db.add(
            models.User(
                name=username, username=username, password_hash=hash_password("pw1234"), is_admin=is_admin
            )
        )
        db.commit()
    finally:
        db.close()


def _login(client, username: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": "pw1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _other_user_headers(client) -> dict:
    # conftest env token 解析为「首个管理员/首个用户」——先建 admin 再建 other，
    # 使 auth_headers 身份为 admin，对他人笔记的访问验证 404 隔离
    _mk("admin_seed", is_admin=True)
    _mk("bob")
    return _login(client, "bob")


def _create(client, headers, title="第一条笔记", content="正文内容") -> dict:
    r = client.post(BASE, json={"title": title, "content": content}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- CRUD ---


def test_create_and_get(client, auth_headers):
    n = _create(client, auth_headers)
    assert n["title"] == "第一条笔记" and n["content"] == "正文内容"
    assert n["created_at"] and n["updated_at"] and n["truncated"] is False

    r = client.get(f"{BASE}/{n['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["content"] == "正文内容"


def test_create_title_only_or_content_only(client, auth_headers):
    n1 = _create(client, auth_headers, title="只有标题", content="")
    assert n1["title"] == "只有标题"
    n2 = _create(client, auth_headers, title="", content="只有正文")
    assert n2["content"] == "只有正文"


def test_create_empty_400(client, auth_headers):
    r = client.post(BASE, json={"title": "  ", "content": ""}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_list_preview_truncated_and_order(client, auth_headers):
    long_content = "字" * 400
    n1 = _create(client, auth_headers, title="长文", content=long_content)
    n2 = _create(client, auth_headers, title="短文", content="短")
    # SQLite CURRENT_TIMESTAMP 秒级精度——同秒创建无法靠更新时间区分先后，
    # 直接把 n2 的 updated_at 拨早一天，验证「按更新时间倒序」
    db = SessionLocal()
    try:
        row = db.get(models.Note, n2["id"])
        row.updated_at = row.updated_at.replace(day=row.updated_at.day - 1)
        db.commit()
    finally:
        db.close()

    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    items = body["items"]
    assert items[0]["id"] == n1["id"]  # 最近更新在前
    assert items[1]["id"] == n2["id"]
    assert items[0]["truncated"] is True and len(items[0]["content"]) == 300
    assert items[1]["truncated"] is False
    # 完整内容走 GET
    full = client.get(f"{BASE}/{n1['id']}", headers=auth_headers).json()
    assert full["content"] == long_content


def test_update(client, auth_headers):
    n = _create(client, auth_headers)
    r = client.put(f"{BASE}/{n['id']}", json={"title": "改后标题", "content": "改后正文"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "改后标题" and r.json()["content"] == "改后正文"


def test_update_empty_400(client, auth_headers):
    n = _create(client, auth_headers)
    r = client.put(f"{BASE}/{n['id']}", json={"title": "", "content": " "}, headers=auth_headers)
    assert r.status_code == 400


def test_delete(client, auth_headers):
    n = _create(client, auth_headers)
    r = client.delete(f"{BASE}/{n['id']}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get(f"{BASE}/{n['id']}", headers=auth_headers).status_code == 404
    assert client.get(BASE, headers=auth_headers).json()["total"] == 0


# --- 隔离 / 认证 / 配额 ---


def test_isolation_404(client, auth_headers):
    n = _create(client, auth_headers)
    hb = _other_user_headers(client)
    assert client.get(f"{BASE}/{n['id']}", headers=hb).status_code == 404
    assert client.put(f"{BASE}/{n['id']}", json={"title": "x"}, headers=hb).status_code == 404
    assert client.delete(f"{BASE}/{n['id']}", headers=hb).status_code == 404
    assert client.get(BASE, headers=hb).json()["total"] == 0


def test_unauthenticated_401(client):
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"title": "x"}).status_code == 401


def test_quota_429(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.notes._MAX_NOTES_PER_USER", 2)
    _create(client, auth_headers, title="1")
    _create(client, auth_headers, title="2")
    r = client.post(BASE, json={"title": "3"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exceeded"


def test_field_length_422(client, auth_headers):
    r = client.post(BASE, json={"title": "t" * 257}, headers=auth_headers)
    assert r.status_code == 422
    r = client.post(BASE, json={"content": "c" * 20_001}, headers=auth_headers)
    assert r.status_code == 422
