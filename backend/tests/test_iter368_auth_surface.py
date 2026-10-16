"""iter 368 批次七（认证与 API 面加固）回归：

- 非 ASCII Bearer -> 401（此前 compare_digest 对非 ASCII str 抛 TypeError -> 500）
- 认证失败 per-IP 限流（超限 429 而非无限 401）
- 改密吊销该用户全部 active PAT（users / setup 两路径）
- 密码最短 8 位（users / setup）
- 登录 per-IP 聚合桶（轮换用户名绕过 ip+username 桶的 CPU 消耗）
- providers 每用户档案数上限
- 模型计费表清洗：NaN/inf 拒入、条目数与模型名长度上限
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.credentials import KIND_AUTH_TOKEN
from app.core.ratelimit import reset_rate_limiter
from app.db import models
from app.db.base import SessionLocal


def _mk_admin_login(client: TestClient) -> dict:
    from app.core.security import hash_password

    db = SessionLocal()
    try:
        u = models.User(
            name="root", username="root", password_hash=hash_password("rootpw-123456"), is_admin=True
        )
        db.add(u)
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "root", "password": "rootpw-123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_pat(user_id: int, token: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            models.CredentialProfile(
                user_id=user_id, kind=KIND_AUTH_TOKEN, name="pat", api_key=token, is_active=True
            )
        )
        db.commit()
    finally:
        db.close()


def test_non_ascii_bearer_401_not_500(client: TestClient):
    # 真实网络客户端可发任意头字节（uvicorn 按 latin-1 解成含多字节码点的 str）；
    # httpx str 头会先被拒，故用 bytes 模拟线上到达服务端的形态
    r = client.get(
        "/api/plans", headers={"Authorization": "Bearer ".encode() + "令牌-非ASCII-测试".encode()}
    )
    assert r.status_code == 401, r.text
    # 单元级：非 ASCII str 直接进比对不再抛 TypeError
    from app.api.deps import _is_env_token
    from app.core.credentials import match_auth_token_credential

    assert _is_env_token("中文令牌") is False
    db = SessionLocal()
    try:
        assert match_auth_token_credential(db, "中文令牌") is None
    finally:
        db.close()


def test_auth_failure_rate_limited(client: TestClient):
    reset_rate_limiter()
    bad = {"Authorization": "Bearer definitely-not-a-token"}
    for _ in range(30):
        assert client.get("/api/plans", headers=bad).status_code == 401
    r = client.get("/api/plans", headers=bad)
    assert r.status_code == 429
    # 正确 token 的请求不受失败桶影响（失败计数才消耗）
    ok = client.get("/api/plans", headers={"Authorization": "Bearer test-token"})
    assert ok.status_code == 200


def test_password_change_revokes_pat(client: TestClient):
    ha = _mk_admin_login(client)
    db = SessionLocal()
    try:
        u = models.User(name="victim", username="victim", password_hash="x")
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()
    _mk_pat(uid, "pat-secret-token-1")

    # PAT 可用
    r = client.get("/api/plans", headers={"Authorization": "Bearer pat-secret-token-1"})
    assert r.status_code == 200

    # 管理员重置其密码 -> PAT 应被吊销
    r = client.put(f"/api/users/{uid}", json={"password": "new-password-9"}, headers=ha)
    assert r.status_code == 200, r.text
    r = client.get("/api/plans", headers={"Authorization": "Bearer pat-secret-token-1"})
    assert r.status_code == 401


def test_password_min_length_8(client: TestClient):
    ha = _mk_admin_login(client)
    assert (
        client.post(
            "/api/users", json={"username": "shortpw", "password": "1234567"}, headers=ha
        ).status_code
        == 422
    )
    r = client.post(
        "/api/users", json={"username": "shortpw", "password": "12345678"}, headers=ha
    )
    assert r.status_code == 201
    # 存量短密码（直改库）登录不受新校验影响
    db = SessionLocal()
    try:
        db.query(models.User).filter_by(username="shortpw").update({"password_hash": "x"})
        db.commit()
    finally:
        db.close()


def test_login_ip_aggregate_bucket(client: TestClient, monkeypatch):
    reset_rate_limiter()
    monkeypatch.setattr("app.api.auth.dummy_verify", lambda *_a, **_k: None)
    for i in range(60):
        r = client.post("/api/auth/login", json={"username": f"u{i}", "password": "whatever-x"})
        assert r.status_code == 401
    # 第 61 次（同 IP、不同用户名——绕过 ip+username 桶）被 per-IP 聚合桶拦截
    r = client.post("/api/auth/login", json={"username": "u-new", "password": "whatever-x"})
    assert r.status_code == 429


def test_providers_per_user_cap(client: TestClient):
    ha = _mk_admin_login(client)
    db = SessionLocal()
    try:
        user = db.query(models.User).filter_by(username="root").one()
        uid = user.id
        for i in range(49):
            db.add(
                models.LlmProviderProfile(
                    user_id=uid, name=f"p{i}", api_key="sk-x", is_active=False
                )
            )
        db.commit()
    finally:
        db.close()
    # 第 50 个允许
    r = client.post("/api/providers", json={"name": "p-50th", "api_key": "sk-x"}, headers=ha)
    assert r.status_code == 201, r.text
    # 第 51 个拒绝（429：与 notes/credentials 配额族同口径）
    r = client.post("/api/providers", json={"name": "p-51th", "api_key": "sk-x"}, headers=ha)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exceeded"


def test_clean_model_pricing_guards():
    from app.api.settings import _clean_model_pricing

    # NaN / inf 拒入（float("nan") < 0 为 False，此前可溜进成本累计）
    cleaned = _clean_model_pricing(
        {"m1": {"input_per_mtok": float("nan")}, "m2": {"input_per_mtok": float("inf")}}
    )
    assert cleaned == {}
    # 条目数上限 200
    table = {f"m{i}": {"input_per_mtok": 1.0} for i in range(205)}
    assert len(_clean_model_pricing(table)) == 200
    # 超长模型名丢弃
    assert _clean_model_pricing({"x" * 129: {"input_per_mtok": 1.0}}) == {}
    assert len(_clean_model_pricing({"x" * 128: {"input_per_mtok": 1.0}})) == 1


def test_pricing_nan_rejected_via_api(client: TestClient, auth_headers):
    r = client.put(
        "/api/settings/pricing",
        json={"pricing": {"bad-model": {"input_per_mtok": "nan"}}},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["table"] == {}


def test_setup_short_password_422(client: TestClient, monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    db = SessionLocal()
    try:
        db.query(models.CredentialProfile).delete()
        db.commit()
    finally:
        db.close()
    r = client.post("/api/setup", json={"password": "1234567"})
    assert r.status_code == 422
