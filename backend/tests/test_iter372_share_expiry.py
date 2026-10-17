"""iter 372 R15：分享链接默认有效期（待追认缺省 90 天）回归。

此前公开只读链接永久有效——「遗忘的链接长期暴露」是网络服务面已知风险。
R15 起：SHARE_DEFAULT_EXPIRE_DAYS（默认 90，0=恢复永久）——过期判定在读取时
按 created_at+天数推导（零迁移）；公开端点过期与不存在同形 404（不向探测者
泄露存在性）；管理/查询面回传 expires_at（加性字段）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)


def _mk_share(name: str) -> tuple[str, dict]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        sess = models.Session(user_id=u.id, title="分享过期")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"topic": "T"})
        db.add(plan)
        db.commit()
        db.refresh(plan)
        link = models.ShareLink(plan_id=plan.id, token=f"tok_{name}")
        db.add(link)
        db.commit()
        db.refresh(link)
        return link.token, {"id": link.id, "uid": u.id, "pid": plan.id}
    finally:
        db.close()


def test_share_expires_after_default_days():
    tok, refs = _mk_share("share_exp_a")
    # 新建：公开读可访问
    r = client.get(f"/api/shared/{tok}")
    assert r.status_code == 200, r.text
    # 回填 created_at 至 91 天前（naive UTC 同口径）-> 过期
    db = SessionLocal()
    try:
        db.query(models.ShareLink).filter(models.ShareLink.id == refs["id"]).update(
            {"created_at": datetime.utcnow() - timedelta(days=91)}
        )
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/shared/{tok}")
    assert r.status_code == 404, r.status_code
    # 属主查询面：shared=false + expired 标记（区分「从未分享」与「已过期」）
    h = {"Authorization": f"Bearer {create_token(refs['uid'], 'share_exp_a')}"}
    r = client.get(f"/api/plans/{refs['pid']}/share", headers=h)
    assert r.status_code == 200
    assert r.json() == {"shared": False, "expired": True}, r.json()


def test_share_expiry_disabled_and_create_response():
    old = settings.share_default_expire_days
    settings.share_default_expire_days = 0  # 0=永久（旧行为）
    try:
        tok, refs = _mk_share("share_exp_b")
        db = SessionLocal()
        try:
            db.query(models.ShareLink).filter(models.ShareLink.token == tok).update(
                {"created_at": datetime.utcnow() - timedelta(days=3650)}
            )
            db.commit()
        finally:
            db.close()
        # 永久模式：十年前的链接仍可读；expires_at 为 null
        r = client.get(f"/api/shared/{tok}")
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {create_token(refs['uid'], 'share_exp_b')}"}
        r = client.get(f"/api/plans/{refs['pid']}/share", headers=h)
        body = r.json()
        assert body["shared"] is True and body["expires_at"] is None, body
    finally:
        settings.share_default_expire_days = old
    # 恢复默认后：同一链接（3650 天前）立即过期；expires_at 有值
    r = client.get(f"/api/shared/{tok}")
    assert r.status_code == 404


def test_create_share_response_carries_expiry():
    """经真实端点创建：响应带 expires_at（默认 90 天口径）。"""
    db = SessionLocal()
    try:
        u = models.User(name="se", username="share_exp_c", password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        sess = models.Session(user_id=u.id, title="c")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"topic": "T"})
        db.add(plan)
        db.commit()
        db.refresh(plan)
        uid, pid = u.id, plan.id
    finally:
        db.close()
    h = {"Authorization": f"Bearer {create_token(uid, 'share_exp_c')}"}
    r = client.post(f"/api/plans/{pid}/share", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expires_at"] is not None
    delta = datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(body["created_at"])
    assert abs(delta.days - 90) <= 1, delta
