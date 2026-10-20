"""iter 397 E1：开放模式（零配置）下 /api/users 缺回环限制 -> 远程未认证即可接管管理员。

iter 394 已为 POST /setup 加回环限制，认定它是开放模式下「唯一的认领管理员入口」。
但 POST /api/users 同样是「造出带口令管理员账号」的入口：require_admin 经
get_current_user -> resolve_user -> get_or_create_open_admin 在开放模式下放行
**任何**客户端，于是远程未认证请求可：

    POST /api/users {"username":"pwn","password":"Passw0rd!","is_admin":true} -> 201
    POST /api/auth/login {"username":"pwn","password":"Passw0rd!"}          -> 管理员 JWT

即「零配置 + 端口可达」= 远程管理员接管，比弱 AUTH_TOKEN 更严重。
PUT /api/users/{id}（重置他人口令）与 DELETE /api/users/{id}（删号）同理暴露。

修复：users.py 三个写端点复用 setup.py 的回环判定（判定函数上提到 app/api/deps.py 共用）。
"""

from __future__ import annotations

from app.core import settings as settings_mod

BASE = "/api/users"


def _zero_config(monkeypatch):
    """模拟零配置部署：无 AUTH_TOKEN（开放模式前提）。"""
    monkeypatch.setattr(settings_mod.settings, "auth_token", "")


def _mk_user_with_password(username="root", password="Passw0rd!"):
    """建一个设了口令的用户——开放模式随即关闭（口令存在即非开放模式）。"""
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password), is_admin=True)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _mk_user_no_password(username="anchor", is_admin=False):
    """建一个**无口令**用户——开放模式仍成立（口令存在才关闭开放模式）。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash="", is_admin=is_admin)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


# --- 开放模式：非回环客户端不得触碰用户管理写端点 ---


def test_open_mode_remote_cannot_create_user(remote_client, monkeypatch):
    """远程未认证 POST /api/users -> 403（此前 201 = 远程管理员接管）。"""
    _zero_config(monkeypatch)
    r = remote_client.post(BASE, json={"username": "pwn", "password": "Passw0rd!", "is_admin": True})
    assert r.status_code == 403, r.text
    assert "回环" in r.json()["error"]["message"] or "本机" in r.json()["error"]["message"]


def test_open_mode_remote_cannot_update_or_delete_user(remote_client, monkeypatch):
    """远程未认证 PUT /api/users/{id}（重置口令）与 DELETE /api/users/{id}（删号）-> 403。

    目标用户**无口令**（开放模式仍成立）；若有口令则开放模式已关闭，远程请求
    本就该 401——那属另一条防线，不在本用例范围。
    """
    _zero_config(monkeypatch)
    uid = _mk_user_no_password("victim")
    r = remote_client.put(f"{BASE}/{uid}", json={"password": "NewPassw0rd!"})
    assert r.status_code == 403, r.text
    r = remote_client.delete(f"{BASE}/{uid}")
    assert r.status_code == 403, r.text


def test_open_mode_remote_cannot_reset_password_of_admin(remote_client, monkeypatch):
    """开放模式下远程重置**管理员**口令 -> 403（管理员是懒创建的无口令锚点）。"""
    _zero_config(monkeypatch)
    remote_client.get("/api/auth/me")  # 触发懒创建 admin 锚点
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        admin_id = db.query(User).filter_by(is_admin=True).first().id
    finally:
        db.close()
    r = remote_client.put(f"{BASE}/{admin_id}", json={"password": "Pwned12345!"})
    assert r.status_code == 403, r.text


def test_closed_open_mode_remote_unauthenticated_is_rejected(remote_client, monkeypatch):
    """开放模式关闭后（库中有口令用户），远程未认证写端点 -> 401（认证防线）。"""
    _zero_config(monkeypatch)
    _mk_user_with_password()
    r = remote_client.post(BASE, json={"username": "pwn3", "password": "Passw0rd!"})
    assert r.status_code == 401, r.text


def test_open_mode_remote_cannot_take_over_admin_even_with_existing_admin(remote_client, monkeypatch):
    """库中已有管理员（开放模式仍成立：管理员无口令）时，远程仍不得建号。"""
    _zero_config(monkeypatch)
    r = remote_client.post(BASE, json={"username": "pwn2", "password": "Passw0rd!"})
    assert r.status_code == 403, r.text


# --- 回环客户端：开放模式的本机用法不受影响 ---


def test_open_mode_loopback_can_still_create_user(client, monkeypatch):
    """本机（回环）调用仍放行——开放模式本就是「本机零配置」场景。"""
    _zero_config(monkeypatch)
    r = client.post(BASE, json={"username": "localuser", "password": "Passw0rd!"})
    assert r.status_code == 201, r.text
    assert r.json()["username"] == "localuser"


def test_open_mode_loopback_created_admin_can_login(client, monkeypatch):
    """回环建的管理员可登录取得 JWT（开放模式本机链路的完整性）。"""
    _zero_config(monkeypatch)
    r = client.post(BASE, json={"username": "localadmin", "password": "Passw0rd!", "is_admin": True})
    assert r.status_code == 201, r.text
    lr = client.post("/api/auth/login", json={"username": "localadmin", "password": "Passw0rd!"})
    assert lr.status_code == 200, lr.text
    assert lr.json()["token"]


# --- 非开放模式：认证路径不受影响 ---


def test_authenticated_remote_can_manage_users(remote_client, monkeypatch):
    """开放模式关闭（有口令用户）后，远程持管理员 JWT 仍可正常管理用户。"""
    _zero_config(monkeypatch)
    _mk_user_with_password("root", "RootPassw0rd!")
    lr = remote_client.post("/api/auth/login", json={"username": "root", "password": "RootPassw0rd!"})
    assert lr.status_code == 200, lr.text
    hdr = {"Authorization": f"Bearer {lr.json()['token']}"}
    r = remote_client.post(BASE, json={"username": "u2", "password": "Passw0rd!"}, headers=hdr)
    assert r.status_code == 201, r.text
    r = remote_client.get(BASE, headers=hdr)
    assert r.status_code == 200, r.text
    assert any(u["username"] == "u2" for u in r.json()["users"])


# --- PAT（个人访问令牌）：开放模式下的持久后门 ---


def test_open_mode_remote_cannot_create_pat(remote_client, monkeypatch):
    """远程未认证建 PAT -> 403（PAT 在开放模式关闭后依然有效 = 持久后门）。"""
    _zero_config(monkeypatch)
    r = remote_client.post(
        "/api/credentials",
        json={"kind": "auth_token", "name": "backdoor", "api_key": "a" * 32, "activate": True},
    )
    assert r.status_code == 403, r.text


def test_open_mode_remote_cannot_activate_pat(remote_client, client, monkeypatch):
    """本机建好（未启用）的 PAT，远程不得启用——启用即把令牌变成可用凭据。"""
    _zero_config(monkeypatch)
    r = client.post(
        "/api/credentials",
        json={"kind": "auth_token", "name": "pat1", "api_key": "b" * 32, "activate": False},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = remote_client.post(f"/api/credentials/{cid}/activate")
    assert r.status_code == 403, r.text


def test_open_mode_remote_cannot_rewrite_pat_value(remote_client, client, monkeypatch):
    """远程不得改写 PAT 值（换一把自己已知的钥匙）。"""
    _zero_config(monkeypatch)
    r = client.post(
        "/api/credentials", json={"kind": "auth_token", "name": "pat2", "api_key": "c" * 32}
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = remote_client.put(f"/api/credentials/{cid}", json={"api_key": "d" * 32})
    assert r.status_code == 403, r.text


def test_open_mode_remote_can_still_create_supplier_key(remote_client, monkeypatch):
    """非 PAT 的供应商 Key 不建立对本实例的认证能力 -> 不受限（避免过度收紧）。"""
    _zero_config(monkeypatch)
    r = remote_client.post(
        "/api/credentials",
        json={"kind": "anthropic", "name": "k1", "api_key": "sk-ant-xxx"},
    )
    assert r.status_code == 201, r.text


def test_open_mode_loopback_can_create_pat(client, monkeypatch):
    """回环建 PAT 不受影响。"""
    _zero_config(monkeypatch)
    r = client.post(
        "/api/credentials",
        json={"kind": "auth_token", "name": "pat-local", "api_key": "e" * 32, "activate": True},
    )
    assert r.status_code == 201, r.text


# --- 供应商档案 base_url：持久数据外泄通道 ---


def test_open_mode_remote_cannot_create_provider(remote_client, monkeypatch):
    """远程不得建供应商档案（base_url = 服务端出站目标 = 持久外泄通道）。"""
    _zero_config(monkeypatch)
    r = remote_client.post(
        "/api/providers",
        json={"name": "evil", "base_url": "https://evil.example.com", "api_key": "k", "model": "m"},
    )
    assert r.status_code == 403, r.text


def test_open_mode_remote_cannot_update_or_activate_provider(remote_client, client, monkeypatch):
    """远程不得改 base_url 或启用档案。"""
    _zero_config(monkeypatch)
    r = client.post(
        "/api/providers",
        json={"name": "p1", "base_url": "https://api.anthropic.com", "api_key": "k", "model": "m"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = remote_client.put(f"/api/providers/{pid}", json={"base_url": "https://evil.example.com"})
    assert r.status_code == 403, r.text
    r = remote_client.post(f"/api/providers/{pid}/activate")
    assert r.status_code == 403, r.text


def test_open_mode_loopback_can_create_provider(client, monkeypatch):
    """回环配置供应商档案不受影响（开放模式本机链路完整）。"""
    _zero_config(monkeypatch)
    r = client.post(
        "/api/providers",
        json={"name": "p-local", "base_url": "https://api.anthropic.com", "api_key": "k", "model": "m"},
    )
    assert r.status_code == 201, r.text
