"""首次设置向导 API 测试（iter 89）：status / setup / dismiss + setup_state 存储。"""

from __future__ import annotations

from app.core import settings as settings_mod
from app.core.security import hash_password
from app.core.setup_state import is_dismissed, load_state, mark_dismissed, save_state

BASE = "/api/setup"


def _zero_config(monkeypatch):
    """模拟零配置部署：无 AUTH_TOKEN、无 ANTHROPIC_API_KEY。"""
    monkeypatch.setattr(settings_mod.settings, "auth_token", "")
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")


def _mk_admin(username="admin", password="pw1234"):
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


def _login(client, username="admin", password="pw1234"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- setup_state 存储 ---


def test_state_default_and_roundtrip():
    assert load_state()["dismissed"] is False
    assert is_dismissed() is False
    mark_dismissed()
    assert is_dismissed() is True
    save_state({"future_step": True})  # 扩展字段保留（version 结构前瞻）
    state = load_state()
    assert state["dismissed"] is True and state["future_step"] is True
    assert state["version"] == 1


def test_state_corrupted_file_returns_default():
    from app.core.setup_state import _path

    _path().write_text("{not json", encoding="utf-8")
    assert load_state()["dismissed"] is False
    assert is_dismissed() is False


# --- GET /status ---


def test_status_zero_config(client, monkeypatch):
    _zero_config(monkeypatch)
    r = client.get(f"{BASE}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["open_mode"] is True
    assert body["llm_configured"] is False
    assert body["needs_setup"] is True


def test_status_after_dismiss(client, monkeypatch):
    _zero_config(monkeypatch)
    assert client.post(f"{BASE}/dismiss").status_code == 200
    body = client.get(f"{BASE}/status").json()
    assert body["dismissed"] is True
    assert body["needs_setup"] is False  # 跳过后不再强引导（功能仍开放模式）
    assert body["open_mode"] is True
    # dismiss 幂等
    assert client.post(f"{BASE}/dismiss").status_code == 200


def test_status_llm_configured_via_env_key(client, monkeypatch):
    _zero_config(monkeypatch)
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "sk-test")
    assert client.get(f"{BASE}/status").json()["llm_configured"] is True


def test_bing_key_not_required(monkeypatch):
    """iter 222：BING_API_KEY 转可选（不配时 auto 模式用 Bing 网页抓取兜底），
    不再计入 validate_feature_keys 缺失项——锁定「可选」语义防回归。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    assert "BING_API_KEY" not in settings_mod.settings.validate_feature_keys()
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    assert "BING_API_KEY" not in settings_mod.settings.validate_feature_keys()


def test_status_llm_placeholder_env_key_counts_as_unconfigured(client, monkeypatch):
    """iter 90：模板占位 ANTHROPIC_API_KEY=sk-ant-xxxxxxxx 视为未配置——
    否则旧脚本生成的 .env 会标记 LLM 已配而不进设置向导（叠加占位 AUTH_TOKEN
    锁死登录页，用户实机反馈）。"""
    _zero_config(monkeypatch)
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "sk-ant-xxxxxxxx")
    assert settings_mod.settings.anthropic_key_configured is False
    assert "ANTHROPIC_API_KEY" in settings_mod.settings.validate_feature_keys()
    body = client.get(f"{BASE}/status").json()
    assert body["llm_configured"] is False
    assert body["needs_setup"] is True


def test_status_corrupted_state_file_still_ok(client, monkeypatch):
    _zero_config(monkeypatch)
    from app.core.setup_state import _path

    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text("garbage", encoding="utf-8")
    r = client.get(f"{BASE}/status")
    assert r.status_code == 200
    assert r.json()["dismissed"] is False


# --- POST /setup ---


def test_setup_password_returns_jwt_and_closes_open_mode(client, monkeypatch):
    _zero_config(monkeypatch)
    r = client.post(f"{BASE}", json={"password": "my-secret"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["password_set"] is True and body["provider_configured"] is False
    # 返回 JWT 立即可用
    h = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    # 开放模式已关闭：无 token -> 401；status 同步反映
    assert client.get("/api/plans").status_code == 401
    assert client.get(f"{BASE}/status").json()["open_mode"] is False
    # 向导标记完成
    assert client.get(f"{BASE}/status").json()["dismissed"] is True
    # 旧密码登录可用
    assert client.post("/api/auth/login", json={"username": "admin", "password": "my-secret"}).status_code == 200


def test_setup_password_too_short_422(client, monkeypatch):
    _zero_config(monkeypatch)
    assert client.post(f"{BASE}", json={"password": "abc"}).status_code == 422


def test_setup_api_key_creates_active_profile(client, monkeypatch):
    _zero_config(monkeypatch)
    r = client.post(f"{BASE}", json={"api_key": "sk-ant-test-1234", "model": "claude-haiku-4-5-20251001"})
    assert r.status_code == 200, r.text
    assert r.json()["provider_configured"] is True

    from app.db.base import SessionLocal
    from app.db.models import LlmProviderProfile

    db = SessionLocal()
    try:
        profiles = db.query(LlmProviderProfile).all()
        assert len(profiles) == 1
        p = profiles[0]
        assert p.is_active is True and p.api_key == "sk-ant-test-1234"
        assert p.model == "claude-haiku-4-5-20251001"
    finally:
        db.close()
    # status 反映 llm_configured
    assert client.get(f"{BASE}/status").json()["llm_configured"] is True


def test_setup_api_key_upsert_keeps_single_profile(client, monkeypatch):
    _zero_config(monkeypatch)
    assert client.post(f"{BASE}", json={"api_key": "sk-one"}).status_code == 200
    assert client.post(f"{BASE}", json={"api_key": "sk-two"}).status_code == 200

    from app.db.base import SessionLocal
    from app.db.models import LlmProviderProfile

    db = SessionLocal()
    try:
        profiles = db.query(LlmProviderProfile).all()
        assert len(profiles) == 1  # upsert 不产生重复档案
        assert profiles[0].api_key == "sk-two"
    finally:
        db.close()


def test_setup_invalid_base_url_422(client, monkeypatch):
    _zero_config(monkeypatch)
    assert client.post(f"{BASE}", json={"api_key": "sk-x", "base_url": "ftp://evil"}).status_code == 422


def test_setup_requires_auth_when_closed(client, monkeypatch):
    """非开放模式（AUTH_TOKEN 已配）且无凭证 -> 401；普通用户 JWT -> 403。"""
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    # conftest AUTH_TOKEN=test-token 仍在 -> 非开放模式；无 token -> 401
    assert client.post(f"{BASE}", json={"api_key": "sk-x"}).status_code == 401
    # 普通用户（非 admin）JWT -> 403
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="bob", username="bob", password_hash=hash_password("pw1234"), is_admin=False))
        db.commit()
    finally:
        db.close()
    hb = _login(client, "bob")
    assert client.post(f"{BASE}", json={"api_key": "sk-x"}, headers=hb).status_code == 403


def test_setup_admin_jwt_allowed_when_closed(client, monkeypatch):
    """密码模式下 admin JWT 可调 /setup（设置页「访问密码」卡复用此路径）。"""
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    _mk_admin()
    ha = _login(client)
    # 吊销判定基于整秒（JWT iat 为 int 秒，见 setup.py 注释）：登录与改密若碰巧
    # 落在同一秒，旧 JWT 本就不在吊销范围，测试随秒边界抖动（连跑实证）。把时钟
    # 拨快 2s 使「改密」确定性地晚于登录 iat。PyJWT 的 exp 校验走 datetime 而非
    # time.time，不受此补丁影响。
    import time as time_mod

    real_now = time_mod.time()
    monkeypatch.setattr(time_mod, "time", lambda: real_now + 2)
    r = client.post(f"{BASE}", json={"password": "new-pw-5678"}, headers=ha)
    assert r.status_code == 200, r.text
    # 新密码生效、旧 JWT 被吊销（tokens_valid_after）
    assert client.post("/api/auth/login", json={"username": "admin", "password": "new-pw-5678"}).status_code == 200
    assert client.get("/api/auth/me", headers=ha).status_code == 401


def test_setup_empty_body_marks_dismissed_only(client, monkeypatch):
    _zero_config(monkeypatch)
    r = client.post(f"{BASE}", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "password_set": False, "provider_configured": False}
    assert is_dismissed() is True
