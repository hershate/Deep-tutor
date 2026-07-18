"""/api/providers 供应商档案 CRUD + 激活测试（iter 51，自定义供应商多档案切换）。"""

from __future__ import annotations

BASE = "/api/providers"


def _create(client, auth_headers, name="官方中转", base_url="https://relay.example.com", api_key="sk-test-1234", model=""):
    r = client.post(
        BASE, json={"name": name, "base_url": base_url, "api_key": api_key, "model": model}, headers=auth_headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_masked(client, auth_headers):
    """创建 -> 列表；响应仅掩码末 4 位，不回显明文。"""
    p = _create(client, auth_headers)
    assert p["is_active"] is False
    assert p["api_key_masked"] == "...1234"
    assert "sk-test-1234" not in str(p)

    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["providers"]
    assert len(rows) == 1
    assert rows[0]["name"] == "官方中转"
    assert rows[0]["base_url"] == "https://relay.example.com"
    assert "sk-test-1234" not in r.text  # 全响应无明文


def test_create_duplicate_name_409(client, auth_headers):
    _create(client, auth_headers)
    r = client.post(BASE, json={"name": "官方中转", "api_key": "sk-x"}, headers=auth_headers)
    assert r.status_code == 409


def test_create_invalid_base_url_422(client, auth_headers):
    r = client.post(BASE, json={"name": "a", "api_key": "sk-x", "base_url": "ftp://x"}, headers=auth_headers)
    assert r.status_code == 422


def test_update_keeps_key_when_absent(client, auth_headers):
    """更新不传 api_key -> 保留原值；传则替换。改名冲突 409。"""
    p = _create(client, auth_headers)
    r = client.put(f"{BASE}/{p['id']}", json={"model": "claude-opus-4-8"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "claude-opus-4-8"
    assert r.json()["api_key_masked"] == "...1234"  # 原 key 保留

    r = client.put(f"{BASE}/{p['id']}", json={"api_key": "sk-new-9999"}, headers=auth_headers)
    assert r.json()["api_key_masked"] == "...9999"

    _create(client, auth_headers, name="另一个")
    r = client.put(f"{BASE}/{p['id']}", json={"name": "另一个"}, headers=auth_headers)
    assert r.status_code == 409

    assert client.put(f"{BASE}/999999", json={"name": "x"}, headers=auth_headers).status_code == 404


def test_activate_single_active_and_deactivate(client, auth_headers):
    """activate：同时至多一个 active；deactivate：全部取消（回退 .env）。"""
    p1 = _create(client, auth_headers, name="A")
    p2 = _create(client, auth_headers, name="B")

    r = client.post(f"{BASE}/{p1['id']}/activate", headers=auth_headers)
    assert r.json()["is_active"] is True
    r = client.post(f"{BASE}/{p2['id']}/activate", headers=auth_headers)
    assert r.json()["is_active"] is True
    rows = client.get(BASE, headers=auth_headers).json()["providers"]
    active = [x for x in rows if x["is_active"]]
    assert len(active) == 1 and active[0]["id"] == p2["id"]  # A 被自动取消

    r = client.post(f"{BASE}/deactivate", headers=auth_headers)
    assert r.json() == {"ok": True}
    rows = client.get(BASE, headers=auth_headers).json()["providers"]
    assert all(not x["is_active"] for x in rows)

    assert client.post(f"{BASE}/999999/activate", headers=auth_headers).status_code == 404


def test_delete(client, auth_headers):
    p = _create(client, auth_headers)
    r = client.delete(f"{BASE}/{p['id']}", headers=auth_headers)
    assert r.json() == {"ok": True}
    assert client.get(BASE, headers=auth_headers).json()["providers"] == []
    assert client.delete(f"{BASE}/{p['id']}", headers=auth_headers).status_code == 404


def test_active_profile_drives_get_provider(client, auth_headers, monkeypatch):
    """激活档案 -> get_provider 用档案 key/base_url（端到端联动 iter 50）。"""
    from app.core import settings as settings_mod
    from app.llm import get_provider

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")  # .env 无 key
    p = _create(client, auth_headers)
    client.post(f"{BASE}/{p['id']}/activate", headers=auth_headers)
    provider = get_provider()  # 不抛错（档案生效）
    assert provider._client.api_key == "sk-test-1234"
    assert "relay.example.com" in str(provider._client.base_url)


def test_requires_auth(client):
    assert client.get(BASE).status_code == 401
