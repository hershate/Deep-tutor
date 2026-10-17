"""iter 372 R13：GET /threads/{id}/memory 列表封顶回归。

此前无界全量返回（2000 条/用户配额 × 20000 字符/条上限最坏 ~40MB 单响应），
Chat 页记忆面板只是 160px 速览小窗。改为最近 200 条 + total 全量计数（加性扩展，
items 消费方不受影响）。锁死：封顶数、total 口径、updated_at 倒序。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)


def test_thread_memory_list_capped_with_total():
    db = SessionLocal()
    try:
        u = models.User(name="memcap", username="memcap_372", password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        t = models.Thread(user_id=u.id, title="cap", kind="chat", model="")
        db.add(t)
        db.commit()
        db.refresh(t)
        t0 = datetime(2026, 1, 1)
        for i in range(250):
            db.add(models.Memory(
                user_id=u.id, thread_id=t.id, scope="thread", kind="note",
                content=f"记忆{i}", updated_at=t0 + timedelta(minutes=i),
            ))
        db.commit()
        tid, uid = t.id, u.id
    finally:
        db.close()

    r = client.get(f"/api/threads/{tid}/memory",
                   headers={"Authorization": f"Bearer {create_token(uid, 'memcap_372')}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 200, len(body["items"])
    assert body["total"] == 250, body["total"]
    # updated_at 倒序：最近在前
    assert body["items"][0]["content"] == "记忆249"
    assert body["items"][-1]["content"] == "记忆50"

    # user 作用域（thread_id=None）同口径封顶：种子全部带 thread_id -> 空
    r2 = client.get("/api/memory",
                    headers={"Authorization": f"Bearer {create_token(uid, 'memcap_372')}"})
    assert r2.status_code == 200
    assert r2.json() == {"items": [], "total": 0}
