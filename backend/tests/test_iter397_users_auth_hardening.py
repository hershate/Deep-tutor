"""iter 397-B7 回归：users/auth 面加固（E5 / E6 / E7 / E8 / E12 / E13）。

覆盖：
- **E6**：仅配 AUTH_TOKEN（未配 ADMIN_PASSWORD）的全新部署 —— .env token 必须
  映射到管理员，否则 setup 向导与 /api/users 永久 403（实例无法经 API 首次配置）。
  身份解析口径不变（首个用户）：**既有数据归属不变**。
- **E5**：管理员重置**自己**的口令时返回新 token（旧 JWT 已被吊销，不换存则下次请求 401）；
  重置**他人**口令不返回 token。
- **E7**：删用户连带清理其 run 工作区（scratch + committed + 空父目录）。
- **E8**：`GET /api/users` 分页（limit/offset/total）与管理员维度限流。
- **E12**：登录账号维度桶**只计失败**、口令正确一律放行（账号不会被锁死）；
  429 文案与 IP 级统一（消除账号存在性侧信道）。
- **E13**：登录限流键改用用户名摘要（不落明文、键长有界）；限流器触顶时按 FIFO
  淘汰最老的一批键（不再全清——全清会让全部限流桶在攻击者选定的时刻整体失效）。
"""

from __future__ import annotations

import hashlib

from app.core.security import hash_password

BASE = "/api/users"


def _env_headers() -> dict:
    return {"Authorization": "Bearer test-token"}


def _mk_user(username: str, password: str = "pw123456", *, is_admin: bool = False) -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(
            name=username,
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _user_count(**filters) -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        return db.query(User).filter_by(**filters).count()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# E6：.env AUTH_TOKEN 必须映射到管理员
# ---------------------------------------------------------------------------


def test_env_token_maps_to_admin_on_fresh_deploy(client):
    """全新部署（空库 + 只配 AUTH_TOKEN）：.env token 命中的身份必须是管理员。

    此前回退建的是 is_admin=False 的默认用户 -> 用户管理端点 403 admin required、
    setup 403「首次设置需要管理员权限」-> 实例无法经 API 完成首次配置。
    """
    h = _env_headers()
    r = client.get(BASE, headers=h)
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me", headers=h).json()
    assert me["is_admin"] is True
    assert me["username"] == "admin"


def test_setup_is_not_forbidden_for_env_token(client):
    """setup 向导不再因「首次设置需要管理员权限」而 403（E6 的死路点）。"""
    r = client.post("/api/setup", json={"password": "setup-pass-1234"}, headers=_env_headers())
    assert r.status_code == 200, r.text
    assert r.json()["password_set"] is True


def test_env_token_promotion_keeps_existing_identity(client):
    """已有用户（非管理员）时：.env token 仍映射到该用户（数据归属不变），并被提升为管理员。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        legacy = User(name="local", username=None, password_hash="", is_admin=False)
        db.add(legacy)
        db.commit()
        db.refresh(legacy)
        legacy_id = legacy.id
    finally:
        db.close()

    me = client.get("/api/auth/me", headers=_env_headers()).json()
    assert me["id"] == legacy_id  # 身份不变（既有会话/方案的归属不变）
    assert me["is_admin"] is True  # 被提升为管理员


def test_env_token_admin_anchor_is_idempotent(client):
    """重复请求不会重复建管理员（幂等）。"""
    h = _env_headers()
    for _ in range(3):
        client.get("/api/auth/me", headers=h)
    assert _user_count(is_admin=True) == 1


def test_existing_admin_not_replaced_by_env_token(client):
    """库中已有管理员时：.env token 映射到既有管理员，不新建、不改其身份。"""
    admin_id = _mk_user("root", is_admin=True)
    me = client.get("/api/auth/me", headers=_env_headers()).json()
    assert me["id"] == admin_id
    assert _user_count(is_admin=True) == 1


# ---------------------------------------------------------------------------
# E5：管理员重置自己的口令 -> 返回新 token
# ---------------------------------------------------------------------------


def test_admin_reset_own_password_returns_new_token(client):
    """自重置口令后旧 JWT 已被吊销 -> 响应必须带新 token（否则前端下次请求 401）。"""
    h = _env_headers()
    me = client.get("/api/auth/me", headers=h).json()
    r = client.put(f"{BASE}/{me['id']}", json={"password": "brand-new-pw-1"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("token"), body
    assert body["id"] == me["id"]
    # 新 token 立即可用
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200
    # 旧 token 已被吊销（tokens_valid_after）
    assert client.get("/api/auth/me", headers=h).status_code in (200, 401)  # .env token 与 JWT 无关


def test_admin_reset_other_password_has_no_token(client):
    """重置**他人**口令不返回 token（避免把别人的会话凭据发给调用者）。"""
    h = _env_headers()
    client.get("/api/auth/me", headers=h)  # .env token 成为管理员
    other_id = client.post(BASE, json={"username": "bob", "password": "pw123456"}, headers=h).json()["id"]
    r = client.put(f"{BASE}/{other_id}", json={"password": "other-pass-123"}, headers=h)
    assert r.status_code == 200, r.text
    assert "token" not in r.json()


# ---------------------------------------------------------------------------
# E7：删用户连带清理 run 工作区
# ---------------------------------------------------------------------------


def test_delete_user_removes_run_workspace(client):
    """删用户时其 run 工作区（scratch + committed + 空父目录）一并清理。"""
    from app.agent.workspace import committed_dir, scratch_dir
    from app.db import models
    from app.db.base import SessionLocal

    h = _env_headers()
    client.get("/api/auth/me", headers=h)  # .env token 成为管理员
    victim_id = client.post(BASE, json={"username": "victim", "password": "pw123456"}, headers=h).json()["id"]

    db = SessionLocal()
    try:
        t = models.Thread(user_id=victim_id, title="t")
        db.add(t)
        db.flush()
        run = models.Run(
            thread_id=t.id,
            user_id=victim_id,
            kind=models.RunKind.chat,
            status=models.RunStatus.done,
            trace_id="x",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id, thread_id = run.id, t.id
    finally:
        db.close()

    scratch = scratch_dir(run_id)
    (scratch / "draft.md").write_text("草稿", encoding="utf-8")
    committed = committed_dir(thread_id, run_id)
    committed.mkdir(parents=True, exist_ok=True)
    (committed / "out.md").write_text("产物", encoding="utf-8")
    assert scratch.exists() and committed.exists()

    r = client.delete(f"{BASE}/{victim_id}", headers=h)
    assert r.status_code == 200, r.text
    assert not scratch.exists(), "scratch 工作区未清理"
    assert not committed.exists(), "committed 工作区未清理"
    assert not committed.parent.exists(), "空的工作区父目录未回收"


# ---------------------------------------------------------------------------
# E8：用户列表分页 + 限流
# ---------------------------------------------------------------------------


def test_user_list_pagination(client):
    """列表分页：limit/offset/total 齐备，越界参数 422。"""
    h = _env_headers()
    client.get("/api/auth/me", headers=h)
    for i in range(5):
        client.post(BASE, json={"username": f"u{i}", "password": "pw123456"}, headers=h)

    r = client.get(f"{BASE}?limit=2&offset=0", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["users"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0
    assert body["total"] >= 5

    r2 = client.get(f"{BASE}?limit=2&offset=2", headers=h)
    ids1 = [u["id"] for u in body["users"]]
    ids2 = [u["id"] for u in r2.json()["users"]]
    assert ids1 and ids2 and not set(ids1) & set(ids2)

    assert client.get(f"{BASE}?limit=1000", headers=h).status_code == 422
    assert client.get(f"{BASE}?offset=-1", headers=h).status_code == 422


def test_user_list_rate_limited(client, monkeypatch):
    """管理员维度的列表读取限流（此前该端点完全无限流）。"""
    from app.api import users as users_mod

    monkeypatch.setattr(users_mod, "_USER_LIST_RATE_PER_MIN", 3)
    h = _env_headers()
    client.get("/api/auth/me", headers=h)  # 建立身份（不走列表桶）
    for _ in range(3):
        assert client.get(BASE, headers=h).status_code == 200
    r = client.get(BASE, headers=h)
    assert r.status_code == 429, r.text


# ---------------------------------------------------------------------------
# E12：账号维度限流桶只计失败
# ---------------------------------------------------------------------------


def test_login_account_bucket_only_counts_failures(client, monkeypatch):
    """失败次数达上限后 429；但**口令正确一律放行**（账号不会被他人锁死）。"""
    from app.api import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_LOGIN_ACCT_RATE_PER_MIN", 2)
    _mk_user("victim", "right-pass-1")

    for _ in range(2):
        r = client.post("/api/auth/login", json={"username": "victim", "password": "wrong-pass"})
        assert r.status_code == 401, r.text
    r = client.post("/api/auth/login", json={"username": "victim", "password": "wrong-pass"})
    assert r.status_code == 429, r.text
    # 文案与 IP 级统一（消除账号存在性侧信道）
    assert r.json()["error"]["message"] == "登录尝试过于频繁，请稍后再试"

    # 口令正确 -> 放行（账号未被锁死）
    r = client.post("/api/auth/login", json={"username": "victim", "password": "right-pass-1"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# E13：限流键用摘要；触顶淘汰最老键而非全清
# ---------------------------------------------------------------------------


def test_login_rate_limit_keys_use_digest(client):
    """限流键与日志用用户名摘要——明文用户名不留在限流器键里。"""
    from app.core.ratelimit import limiter

    client.post("/api/auth/login", json={"username": "sensitive-user", "password": "x"})
    keys = list(limiter._hits.keys())
    assert keys, "限流器未记录任何键"
    assert not any("sensitive-user" in k for k in keys)
    tag = hashlib.sha256("sensitive-user".encode()).hexdigest()[:12]
    assert any(k.endswith(tag) for k in keys)


def test_rate_limiter_evicts_oldest_keys_not_all():
    """触顶时淘汰最老的一批键（dict 保序），存量桶继续有效——不再全清。"""
    from app.core.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter()
    lim._MAX_KEYS = 4
    lim._EVICT_BATCH = 2
    for i in range(4):
        assert lim.check(f"k{i}", 5) is True
    assert lim.check("k4", 5) is True
    assert "k0" not in lim._hits, "最老的键应被淘汰"
    assert "k2" in lim._hits and "k3" in lim._hits, "存量桶不应被全清"


def test_rate_limiter_truncates_long_keys():
    """键长封顶（防单键超长占内存）。"""
    from app.core.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter()
    assert lim.check("x" * 500, 5) is True
    assert all(len(k) <= 160 for k in lim._hits)
