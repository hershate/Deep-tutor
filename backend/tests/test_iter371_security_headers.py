"""iter 371 R8：网络暴露面基础防护回归。

- 全站安全响应头（nosniff / X-Frame-Options DENY / Referrer-Policy no-referrer）：
  API 响应、SPA 静态回退响应、以及 body 限流 413 短路响应（中间件须在限流外层）均带头。
- 402 budget_exceeded 消息币种口径为 CNY（￥）——与前端全链展示一致（$ 残留修正）。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def test_security_headers_on_api():
    r = client.get("/api/health")
    assert r.status_code == 200
    for k, v in _EXPECTED.items():
        assert r.headers.get(k) == v, k


def test_security_headers_on_413_short_circuit():
    # Content-Length 预检在限流中间件短路返回 413——安全头中间件注册在其外层，
    # 短路响应也必须带头（否则最大的拒绝响应反而是无防护面）
    r = client.post(
        "/api/sessions",
        content=b"x",
        headers={"Content-Type": "application/json", "Content-Length": str(10**12)},
    )
    assert r.status_code == 413, r.text
    for k, v in _EXPECTED.items():
        assert r.headers.get(k) == v, k


def test_budget_message_currency_cny():
    from app.api.deps import check_daily_budget
    from app.db import models
    from app.db.base import SessionLocal
    from app.api.errors import ApiError

    db = SessionLocal()
    try:
        u = models.User(name="cur", username="cur_371", password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        # 用户级覆盖设 0.0001，制造超限
        db.add(models.UserPref(user_id=u.id, key="daily_budget_usd", value="0.0001"))
        db.add(models.ApiUsage(user_id=u.id, cost=1.0))
        db.commit()
        try:
            check_daily_budget(db, u.id)
            raise AssertionError("应抛 budget_exceeded")
        except ApiError as e:
            assert "￥" in e.message and "$" not in e.message, e.message
    finally:
        db.close()

def test_plugin_admin_rate_limit():
    from app.api.plugins import _gate_plugin_admin
    from app.api.errors import ApiError
    from app.core.ratelimit import reset_rate_limiter
    from app.db.models import User

    reset_rate_limiter()
    u = User(name="adm", username="adm_371", password_hash="x", is_admin=True)
    u.id = 1
    for _ in range(20):
        _gate_plugin_admin(u)
    try:
        _gate_plugin_admin(u)
        raise AssertionError("第 21 次/分钟应 429")
    except ApiError as e:
        assert e.status == 429

def test_backup_import_exempt_from_generic_body_limit():
    """iter 371：v2 备份可超通用 body 上限（默认 ~55MB）——备份端点豁免，否则导出后导不回。

    58MB multipart 到 /api/backup/preview：应穿过通用 413 中间件、由端点自限/解析接手
    （此处发非法 JSON -> 400 invalid_backup 而非 413）；同尺寸裸 POST 到 /api/sessions
    仍被通用中间件 413。
    """
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        u = models.User(name="bk", username="bk_371", password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
    finally:
        db.close()

    big = b"x" * (58 * 1024 * 1024)
    r = client.post(
        "/api/backup/preview",
        files={"file": ("backup.json", big, "application/json")},
        headers={"X-User-Id": str(uid)},
    )
    # 开放本机模式下无需 token；走到端点解析层即证明中间件放行（413 才是失败）
    assert r.status_code != 413, r.text[:200]
    assert r.status_code in (400, 401), r.status_code  # 非法 JSON -> 400；若认证口径变 -> 401

    r2 = client.post(
        "/api/sessions",
        content=b"x",
        headers={"Content-Type": "application/json", "Content-Length": str(len(big))},
    )
    assert r2.status_code == 413
