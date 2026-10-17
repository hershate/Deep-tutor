"""iter 378 R21：同用户多标签并发写——笔记乐观并发回归（双写丢失更新防护）。

背景：自由笔记 PUT /notes/{id} 与知识节点笔记 PUT /knowledge/notes/{node_id} 均为
全量盲覆盖（last-write-wins）——双标签编辑同笔记，后保存者静默吞掉先保存者的修改，
零提示。iter 378：PUT 加可选 expected_updated_at（装载基线原样回传），不匹配 -> 409；
不传保持旧行为（兼容）。其余并发面已核：progress 乐观锁（iter 367）/quiz 与打卡
append 语义无丢失更新面。

测试用 DB 回拨 updated_at 模拟「他窗口先保存」，避开秒粒度等待。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)


def _mk_user(name: str) -> tuple[int, dict]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, {"Authorization": f"Bearer {create_token(u.id, name)}"}
    finally:
        db.close()


def _backdate(note_id: int, seconds: float) -> None:
    db = SessionLocal()
    try:
        db.query(models.Note).filter(models.Note.id == note_id).update(
            {"updated_at": datetime.utcnow() - timedelta(seconds=seconds)}
        )
        db.commit()
    finally:
        db.close()


# --- 自由笔记 ---


def test_free_note_stale_expected_409():
    _, h = _mk_user("note_cc_a")
    nid = client.post("/api/notes", json={"title": "t", "content": "v1"}, headers=h).json()["id"]
    ts0 = client.get(f"/api/notes/{nid}", headers=h).json()["updated_at"]
    # 他窗口先保存（回拨时钟模拟先前版本）
    r = client.put(f"/api/notes/{nid}", json={"title": "t", "content": "v2"}, headers=h)
    assert r.status_code == 200
    _backdate(nid, 5)
    # 本窗口仍持 ts0 基线 -> 拒绝盲覆盖，服务端内容不被吞
    r = client.put(
        f"/api/notes/{nid}",
        json={"title": "t", "content": "v1-overwrite", "expected_updated_at": ts0},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"
    cur = client.get(f"/api/notes/{nid}", headers=h).json()
    assert cur["content"] == "v2", cur


def test_free_note_fresh_expected_ok_and_compat_none():
    _, h = _mk_user("note_cc_b")
    nid = client.post("/api/notes", json={"title": "t", "content": "v1"}, headers=h).json()["id"]
    ts = client.get(f"/api/notes/{nid}", headers=h).json()["updated_at"]
    # 基线一致 -> 放行
    r = client.put(
        f"/api/notes/{nid}", json={"title": "t", "content": "v2", "expected_updated_at": ts}, headers=h
    )
    assert r.status_code == 200, r.text
    # 不传 = 旧行为（LWW 兼容：插件/外部调用方）
    r = client.put(f"/api/notes/{nid}", json={"title": "t", "content": "v3"}, headers=h)
    assert r.status_code == 200 and r.json()["content"] == "v3"


# --- 知识节点笔记 ---


def _mk_node(uid: int) -> int:
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="cc")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"topic": "T"})
        db.add(plan)
        db.flush()
        node = models.KnowledgeNode(plan_id=plan.id, sid="n1", name="N", difficulty=1)
        db.add(node)
        db.commit()
        db.refresh(node)
        return node.id
    finally:
        db.close()


def _backdate_node_note(user_id: int, node_id: int, seconds: float) -> None:
    db = SessionLocal()
    try:
        db.query(models.NodeNote).filter_by(user_id=user_id, node_id=node_id).update(
            {"updated_at": datetime.utcnow() - timedelta(seconds=seconds)}
        )
        db.commit()
    finally:
        db.close()


def test_node_note_stale_expected_409_and_deleted_conflict():
    uid, h = _mk_user("note_cc_c")
    node_id = _mk_node(uid)
    # 首写（无基线 expected=null 走插入）
    r = client.put(f"/api/knowledge/notes/{node_id}", json={"content": "v1"}, headers=h)
    assert r.status_code == 200, r.text
    ts0 = r.json()["updated_at"]
    # 他窗口保存 v2（回拨模拟）
    r = client.put(f"/api/knowledge/notes/{node_id}", json={"content": "v2"}, headers=h)
    assert r.status_code == 200
    _backdate_node_note(uid, node_id, 5)
    # 本窗口持 ts0 -> 409，内容不被吞
    r = client.put(
        f"/api/knowledge/notes/{node_id}", json={"content": "v1-o", "expected_updated_at": ts0}, headers=h
    )
    assert r.status_code == 409, r.text
    assert client.get(f"/api/knowledge/notes/{node_id}", headers=h).json()["content"] == "v2"
    # 他窗口清空（=删行）后，本窗口仍持旧基线 -> 409（cur=None != ts0）
    _backdate_node_note(uid, node_id, 5)
    ts1 = client.get(f"/api/knowledge/notes/{node_id}", headers=h).json()["updated_at"]
    client.put(f"/api/knowledge/notes/{node_id}", json={"content": ""}, headers=h)  # 清空删行
    r = client.put(
        f"/api/knowledge/notes/{node_id}", json={"content": "again", "expected_updated_at": ts1}, headers=h
    )
    assert r.status_code == 409, r.text


def test_node_note_fresh_ok():
    uid, h = _mk_user("note_cc_d")
    node_id = _mk_node(uid)
    r = client.put(f"/api/knowledge/notes/{node_id}", json={"content": "v1"}, headers=h)
    ts = r.json()["updated_at"]
    r = client.put(
        f"/api/knowledge/notes/{node_id}", json={"content": "v2", "expected_updated_at": ts}, headers=h
    )
    assert r.status_code == 200 and r.json()["content"] == "v2", r.text
