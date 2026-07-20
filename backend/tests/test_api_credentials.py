"""/api/credentials API Key 凭证档案测试（iter 223：设置页可视化管理全部 Key，多档案卡片切换）。

覆盖：CRUD（掩码/重名/配额/强度）、P4 隔离、单活（anthropic/bing）与多活（auth_token）
激活语义、deactivate-all、三大消费点（get_provider 三级回退 / Bing 引擎决策与检索 key /
个人访问令牌认证与开放模式让位）、settings 快照来源、删用户级联。
"""

from __future__ import annotations

from app.core.security import hash_password

BASE = "/api/credentials"


def _create(client, auth_headers, kind="anthropic", name="主 Key", api_key="sk-cred-1234", activate=False):
    r = client.post(
        BASE, json={"kind": kind, "name": name, "api_key": api_key, "activate": activate}, headers=auth_headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def _me_id(client, auth_headers) -> int:
    r = client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_user(username: str, password: str = "pw123", is_admin: bool = False) -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password), is_admin=is_admin)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw123") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- CRUD 基础 ---


def test_create_and_list_masked(client, auth_headers):
    """创建 -> 列表；响应仅掩码末 4 位，不回显明文；kinds 清单随响应返回。"""
    p = _create(client, auth_headers)
    assert p["is_active"] is False
    assert p["kind"] == "anthropic"
    assert p["api_key_masked"] == "...1234"
    assert "sk-cred-1234" not in str(p)

    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["kinds"] == ["anthropic", "bing", "auth_token"]
    assert len(body["credentials"]) == 1
    assert "sk-cred-1234" not in r.text  # 全响应无明文

    # 按 kind 过滤
    _create(client, auth_headers, kind="bing", name="搜索", api_key="bing-key-5678")
    rows = client.get(f"{BASE}?kind=bing", headers=auth_headers).json()["credentials"]
    assert len(rows) == 1 and rows[0]["kind"] == "bing" and rows[0]["name"] == "搜索"
    assert client.get(f"{BASE}?kind=other", headers=auth_headers).status_code == 422


def test_create_invalid_kind_422(client, auth_headers):
    r = client.post(BASE, json={"kind": "other", "name": "x", "api_key": "k"}, headers=auth_headers)
    assert r.status_code == 422


def test_auth_token_min_length(client, auth_headers):
    """auth_token 类为认证凭证：key <8 位 -> 400；>=8 -> 201。其余类不限。"""
    r = client.post(BASE, json={"kind": "auth_token", "name": "弱", "api_key": "short"}, headers=auth_headers)
    assert r.status_code == 400
    p = _create(client, auth_headers, kind="auth_token", name="强", api_key="token-abcdefgh")
    assert p["kind"] == "auth_token"
    # anthropic 类短 key 不限制（同供应商档案 min_length=1）
    p2 = _create(client, auth_headers, kind="anthropic", name="短", api_key="x")
    assert p2["api_key_masked"] == "****"


def test_create_duplicate_name_same_kind_only(client, auth_headers):
    _create(client, auth_headers, kind="anthropic", name="重复")
    r = client.post(BASE, json={"kind": "anthropic", "name": "重复", "api_key": "k2"}, headers=auth_headers)
    assert r.status_code == 409
    # 同名不同 kind 不冲突
    r = client.post(BASE, json={"kind": "bing", "name": "重复", "api_key": "k2"}, headers=auth_headers)
    assert r.status_code == 201


def test_update_keeps_key_when_absent(client, auth_headers):
    """更新不传 api_key -> 保留原值；传则替换（auth_token 类替换须过强度校验）。改名冲突 409。"""
    p = _create(client, auth_headers, kind="auth_token", name="令牌", api_key="token-abcdefgh")
    r = client.put(f"{BASE}/{p['id']}", json={"name": "令牌2"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["api_key_masked"] == "...efgh"  # 原 key 保留
    r = client.put(f"{BASE}/{p['id']}", json={"api_key": "short"}, headers=auth_headers)
    assert r.status_code == 400  # auth_token 强度校验
    r = client.put(f"{BASE}/{p['id']}", json={"api_key": "token-99999999"}, headers=auth_headers)
    assert r.json()["api_key_masked"] == "...9999"

    _create(client, auth_headers, kind="auth_token", name="另一个", api_key="token-bbbbbbbb")
    r = client.put(f"{BASE}/{p['id']}", json={"name": "另一个"}, headers=auth_headers)
    assert r.status_code == 409
    assert client.put(f"{BASE}/999999", json={"name": "x"}, headers=auth_headers).status_code == 404


def test_delete(client, auth_headers):
    p = _create(client, auth_headers)
    r = client.delete(f"{BASE}/{p['id']}", headers=auth_headers)
    assert r.json() == {"ok": True}
    assert client.get(BASE, headers=auth_headers).json()["credentials"] == []
    assert client.delete(f"{BASE}/{p['id']}", headers=auth_headers).status_code == 404


def test_quota_per_kind_429(client, auth_headers):
    """每用户每类 ≤20：第 21 个 -> 429；不同类独立计数。"""
    for i in range(20):
        _create(client, auth_headers, kind="bing", name=f"k{i}", api_key=f"bing-{i:04d}")
    r = client.post(BASE, json={"kind": "bing", "name": "溢出", "api_key": "bing-x"}, headers=auth_headers)
    assert r.status_code == 429
    r = client.post(BASE, json={"kind": "anthropic", "name": "另类", "api_key": "sk-x"}, headers=auth_headers)
    assert r.status_code == 201


def test_isolation_other_user_404(client, auth_headers):
    """P4 隔离：他人凭证读/改/删/激活一律 404（不泄露存在性）；列表互不可见。"""
    p = _create(client, auth_headers, kind="bing", name="我的", api_key="bing-mine-1")
    _mk_user("bob")
    bob = _login(client, "bob")
    assert client.get(BASE, headers=bob).json()["credentials"] == []
    assert client.put(f"{BASE}/{p['id']}", json={"name": "x"}, headers=bob).status_code == 404
    assert client.delete(f"{BASE}/{p['id']}", headers=bob).status_code == 404
    assert client.post(f"{BASE}/{p['id']}/activate", headers=bob).status_code == 404
    # 他人同名不冲突（唯一约束按 (user,kind,name)）
    r = client.post(BASE, json={"kind": "bing", "name": "我的", "api_key": "bing-bob-1"}, headers=bob)
    assert r.status_code == 201


def test_requires_auth(client):
    assert client.get(BASE).status_code == 401


# --- 激活语义 ---


def test_activate_single_active_kinds(client, auth_headers):
    """anthropic/bing 单活：激活 B 自动停用 A；auth_token 多活并存。"""
    a = _create(client, auth_headers, kind="anthropic", name="A", api_key="sk-a")
    b = _create(client, auth_headers, kind="anthropic", name="B", api_key="sk-b")
    client.post(f"{BASE}/{a['id']}/activate", headers=auth_headers)
    client.post(f"{BASE}/{b['id']}/activate", headers=auth_headers)
    rows = client.get(f"{BASE}?kind=anthropic", headers=auth_headers).json()["credentials"]
    active = [x for x in rows if x["is_active"]]
    assert len(active) == 1 and active[0]["id"] == b["id"]

    t1 = _create(client, auth_headers, kind="auth_token", name="T1", api_key="token-11111111")
    t2 = _create(client, auth_headers, kind="auth_token", name="T2", api_key="token-22222222")
    client.post(f"{BASE}/{t1['id']}/activate", headers=auth_headers)
    client.post(f"{BASE}/{t2['id']}/activate", headers=auth_headers)
    rows = client.get(f"{BASE}?kind=auth_token", headers=auth_headers).json()["credentials"]
    assert sum(1 for x in rows if x["is_active"]) == 2  # 多活：互不影响

    # 单条停用
    r = client.post(f"{BASE}/{t1['id']}/deactivate", headers=auth_headers)
    assert r.json()["is_active"] is False
    rows = client.get(f"{BASE}?kind=auth_token", headers=auth_headers).json()["credentials"]
    assert [x["id"] for x in rows if x["is_active"]] == [t2["id"]]
    assert client.post(f"{BASE}/999999/activate", headers=auth_headers).status_code == 404


def test_deactivate_all_by_kind(client, auth_headers):
    """deactivate-all?kind= 仅停用该类（anthropic 回退 .env；bing 激活不受影响）。"""
    a = _create(client, auth_headers, kind="anthropic", name="A", api_key="sk-a", activate=True)
    b = _create(client, auth_headers, kind="bing", name="B", api_key="bing-b", activate=True)
    r = client.post(f"{BASE}/deactivate-all?kind=anthropic", headers=auth_headers)
    assert r.json() == {"ok": True}
    rows = client.get(BASE, headers=auth_headers).json()["credentials"]
    by_id = {x["id"]: x for x in rows}
    assert by_id[a["id"]]["is_active"] is False
    assert by_id[b["id"]]["is_active"] is True  # 其他类不受影响
    assert client.post(f"{BASE}/deactivate-all?kind=other", headers=auth_headers).status_code == 422


def test_create_with_activate(client, auth_headers):
    """activate=true 创建即启用，且互斥停用同类既有 active。"""
    a = _create(client, auth_headers, kind="bing", name="旧", api_key="bing-old", activate=True)
    b = _create(client, auth_headers, kind="bing", name="新", api_key="bing-new", activate=True)
    rows = client.get(f"{BASE}?kind=bing", headers=auth_headers).json()["credentials"]
    by_id = {x["id"]: x for x in rows}
    assert by_id[a["id"]]["is_active"] is False
    assert by_id[b["id"]]["is_active"] is True


# --- 消费点 1：get_provider 三级回退 ---


def test_anthropic_credential_drives_get_provider(client, auth_headers, monkeypatch):
    """env 无 key：active Anthropic 凭证档案 -> get_provider 用其 key（官方端点默认模型）；
    LLM 供应商档案优先于凭证档案；凭证档案停用后回退报错。"""
    from app.core import settings as settings_mod
    from app.llm import get_provider

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")  # .env 无 key
    uid = _me_id(client, auth_headers)

    try:
        get_provider(uid)
        raise AssertionError("无 key 无档案应抛 RuntimeError")
    except RuntimeError:
        pass

    p = _create(client, auth_headers, kind="anthropic", name="回退", api_key="sk-fallback-42", activate=True)
    provider = get_provider(uid)
    assert provider._client.api_key == "sk-fallback-42"

    # LLM 供应商档案激活 -> 优先于凭证档案
    r = client.post(
        "/api/providers",
        json={"name": "中转", "api_key": "sk-relay-9999", "base_url": "https://relay.example.com"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    client.post(f"/api/providers/{r.json()['id']}/activate", headers=auth_headers)
    provider = get_provider(uid)
    assert provider._client.api_key == "sk-relay-9999"
    client.post("/api/providers/deactivate", headers=auth_headers)
    assert get_provider(uid)._client.api_key == "sk-fallback-42"  # 回到凭证档案

    # 凭证档案停用 -> env 亦无 key -> 报错
    client.post(f"{BASE}/{p['id']}/deactivate", headers=auth_headers)
    try:
        get_provider(uid)
        raise AssertionError("停用后应抛 RuntimeError")
    except RuntimeError:
        pass


# --- 消费点 2：Bing 引擎决策与检索 key ---


def test_bing_credential_drives_engine_and_settings(client, auth_headers, monkeypatch):
    """env 无 Bing key：active bing 凭证档案 -> resolve_bing_api_key 取档案 key、
    auto 决策为 bing_api；settings 快照同步反映。"""
    from app.core import settings as settings_mod
    from app.core.credentials import resolve_bing_api_key
    from app.core.settings import effective_search_engine
    from app.db.base import SessionLocal

    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    uid = _me_id(client, auth_headers)

    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["bing_api_key_set"] is False
    assert snap["bing_key_source"] == "none"
    assert snap["search_engine_effective"] == "bing_web"  # auto 无 key -> 网页版兜底

    _create(client, auth_headers, kind="bing", name="搜索", api_key="bing-profile-key", activate=True)
    db = SessionLocal()
    try:
        key = resolve_bing_api_key(uid, db)
    finally:
        db.close()
    assert key == "bing-profile-key"
    assert effective_search_engine(key) == "bing_api"

    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["bing_api_key_set"] is True
    assert snap["bing_key_source"] == "credential"
    assert snap["search_engine_effective"] == "bing_api"


def test_search_resources_uses_credential_key(client, auth_headers, monkeypatch):
    """检索调度 key 流：ctx.bing_api_key（凭证档案解析值）直达引擎函数。"""
    import asyncio

    from app.agent.context import ExecContext
    from app.tools import search_resources as sr_mod
    from app.tools.base import ToolInput, ToolOutput

    captured: dict = {}

    async def fake_search(query, engine, count=5, key=""):
        captured["engine"] = engine
        captured["key"] = key
        return [{"title": "t", "url": "https://example.com/x"}]

    async def fake_verify(inp, ctx):
        return ToolOutput(
            tool="verify_url", status="ok",
            data={"ok": True, "status": 200, "suitable": True, "final_url": inp.args["url"], "label": "ok"},
        )

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)
    ctx = ExecContext(session_id="s", trace_id="t", bing_api_key="bing-profile-key")
    inp = ToolInput(tool="search_resources", session_id="s", args={"stage_topics": [["React"]]})
    out = asyncio.run(sr_mod.search_resources(inp, ctx))
    assert out.status == "ok"
    assert captured["engine"] == "bing_api"
    assert captured["key"] == "bing-profile-key"
    assert out.data["resources"][0]["url"] == "https://example.com/x"


# --- 消费点 3：个人访问令牌认证 ---


def test_auth_token_authenticates_owner(client, auth_headers):
    """active PAT 以归属用户身份通过认证；停用即失效；.env token 始终有效。"""
    _mk_user("carol")
    carol = _login(client, "carol")
    p = _create(client, carol, kind="auth_token", name="扩展用", api_key="carol-secret-token", activate=True)

    r = client.get("/api/auth/me", headers={"Authorization": "Bearer carol-secret-token"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "carol"

    # 停用 -> 401
    client.post(f"{BASE}/{p['id']}/deactivate", headers=carol)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer carol-secret-token"}).status_code == 401
    # .env token（test-token）不受影响
    assert client.get("/api/auth/me", headers=auth_headers).status_code == 200


def test_auth_token_closes_open_mode(client, auth_headers, monkeypatch):
    """开放模式（无 env token 无密码）：创建并激活 PAT 后开放模式关闭——
    无 token 请求 401，持 PAT 请求以 admin 身份通过；停用 PAT 恢复开放。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "auth_token", "")  # 无 env token -> 开放模式
    # 开放模式中无 token 也可访问（懒创建空密码 admin）
    assert client.get("/api/auth/me").status_code == 200

    r = client.post(
        BASE, json={"kind": "auth_token", "name": "看门", "api_key": "open-guard-token", "activate": True}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # 开放模式已关闭：无 token -> 401
    assert client.get("/api/auth/me").status_code == 401
    # 持 PAT -> 200（归属 admin）
    pat = {"Authorization": "Bearer open-guard-token"}
    r = client.get("/api/auth/me", headers=pat)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"

    # 停用 PAT -> 恢复开放
    client.post(f"{BASE}/{pid}/deactivate", headers=pat)
    assert client.get("/api/auth/me").status_code == 200


# --- settings 快照与 setup 向导 ---


def test_settings_snapshot_key_sources(client, auth_headers, monkeypatch):
    """anthropic_key_source：none -> credential -> provider_profile -> credential；
    auth_token_source：env -> credential。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["anthropic_api_key_set"] is False
    assert snap["anthropic_key_source"] == "none"
    assert snap["auth_token_source"] == "env"  # test-token 已配置

    _create(client, auth_headers, kind="anthropic", name="回退", api_key="sk-fb-1", activate=True)
    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["anthropic_key_source"] == "credential"
    assert snap["anthropic_api_key_set"] is True

    r = client.post("/api/providers", json={"name": "中转", "api_key": "sk-relay-1"}, headers=auth_headers)
    client.post(f"/api/providers/{r.json()['id']}/activate", headers=auth_headers)
    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["anthropic_key_source"] == "provider_profile"
    client.post("/api/providers/deactivate", headers=auth_headers)
    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["anthropic_key_source"] == "credential"

    _create(client, auth_headers, kind="auth_token", name="PAT", api_key="token-snapshot", activate=True)
    snap = client.get("/api/settings", headers=auth_headers).json()
    assert snap["auth_token_source"] == "credential"


def test_setup_status_llm_configured_with_credential(client, auth_headers, monkeypatch):
    """首次设置向导：active Anthropic 凭证档案 -> llm_configured=True（同供应商档案语义）。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    assert client.get("/api/setup/status").json()["llm_configured"] is False
    _create(client, auth_headers, kind="anthropic", name="回退", api_key="sk-setup-1", activate=True)
    assert client.get("/api/setup/status").json()["llm_configured"] is True


# --- 级联 ---


def test_delete_user_cascades_credentials(client, auth_headers):
    """删用户 -> 其凭证档案级联删除（ORM delete-orphan）。"""
    from app.db.base import SessionLocal
    from app.db import models

    uid = _mk_user("dave")
    dave = _login(client, "dave")
    _create(client, dave, kind="bing", name="搜索", api_key="bing-dave-1")

    db = SessionLocal()
    try:
        assert db.query(models.CredentialProfile).filter_by(user_id=uid).count() == 1
        u = db.get(models.User, uid)
        db.delete(u)
        db.commit()
        assert db.query(models.CredentialProfile).filter_by(user_id=uid).count() == 0
    finally:
        db.close()
