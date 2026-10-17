"""iter 373 R16：分享过期后续行为闭合（R15 引入的呈现/语义缺口）。

- 过期后再创建：不得沿用死链——须轮换 token 并重置 90 天计时（旧 token
  继续失效，新 token 公开可读）；此前原样返回过期 token，前端「分享」按钮
  会拿到一条公开 404 的链接。
- /shares 管理列表：逐条带 expired 标记（过期链接不再可公开访问，但行仍在，
  前端据此标注并允许撤销清理）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)


def _mk_share(name: str, backdate_days: int | None = None) -> dict:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        sess = models.Session(user_id=u.id, title="轮换")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"topic": "T"})
        db.add(plan)
        db.commit()
        db.refresh(plan)
        uid, pid = u.id, plan.id
        refs = {"uid": uid, "pid": pid}
    finally:
        db.close()
    h = {"Authorization": f"Bearer {create_token(uid, name)}"}
    r = client.post(f"/api/plans/{pid}/share", headers=h)
    assert r.status_code == 201, r.text
    refs["token1"] = r.json()["token"]
    if backdate_days is not None:
        db = SessionLocal()
        try:
            db.query(models.ShareLink).filter(models.ShareLink.token == refs["token1"]).update(
                {"created_at": datetime.utcnow() - timedelta(days=backdate_days)}
            )
            db.commit()
        finally:
            db.close()
    refs["auth"] = h
    return refs


def test_create_share_rotates_expired_link():
    refs = _mk_share("share_rot_a", backdate_days=91)
    # 前置确认：旧 token 公开 404
    assert client.get(f"/api/shared/{refs['token1']}").status_code == 404
    # 属主再点「分享」：必须拿到可用新链接，而非沿用死链
    r = client.post(f"/api/plans/{refs['pid']}/share", headers=refs["auth"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"] != refs["token1"], "过期链接再分享必须轮换 token"
    assert client.get(f"/api/shared/{body['token']}").status_code == 200, body
    assert client.get(f"/api/shared/{refs['token1']}").status_code == 404, "旧 token 须保持失效"
    # 新链接重新计时（expires_at 距今约 90 天，而非沿用过期基准）
    delta = datetime.fromisoformat(body["expires_at"]) - datetime.utcnow()
    assert timedelta(days=88) < delta < timedelta(days=91), delta


def test_create_share_active_link_not_rotated():
    # 未过期链接：幂等语义不变——同 token 原样返回（避免每次点「复制分享链接」
    # 都换 token，导致此前复制出去的链接意外失效）
    refs = _mk_share("share_rot_b")
    r = client.post(f"/api/plans/{refs['pid']}/share", headers=refs["auth"])
    assert r.status_code == 201, r.text
    assert r.json()["token"] == refs["token1"]


def test_list_shares_marks_expired():
    a = _mk_share("share_rot_c1", backdate_days=91)
    b = _mk_share("share_rot_c2")
    r = client.get("/api/shares", headers=a["auth"])
    assert r.status_code == 200, r.text
    mine = {s["token"]: s for s in r.json()["shares"]}
    assert set(mine[a["token1"]].keys()) >= {"expires_at", "expired"}
    assert mine[a["token1"]]["expired"] is True, "过期链接须标记（前端标注+可撤销清理）"
    # 同用户另一条新链接不在他人列表里，且自己列表里标记为未过期
    r = client.get("/api/shares", headers=b["auth"])
    assert r.status_code == 200, r.text
    other = {s["token"]: s for s in r.json()["shares"]}
    assert a["token1"] not in other, "他人链接不可见"
    assert other[b["token1"]]["expired"] is False
    assert other[b["token1"]]["expires_at"] is not None
