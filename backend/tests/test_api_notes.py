"""知识点笔记端点测试（iter 105）：增改删 / has_note 标记 / 用户隔离 / 级联清理 / 备份 roundtrip。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed(with_other: bool = False):
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        other = None
        if with_other:
            other = User(name="other", username="other", password_hash="x", is_admin=False)
            db.add(other)
        db.flush()
        sess = models.Session(user_id=admin.id, title="笔记会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "笔记主题"}})
        db.add(plan)
        db.flush()
        n1 = models.KnowledgeNode(plan_id=plan.id, sid="k1", name="二叉树", difficulty=3)
        n2 = models.KnowledgeNode(plan_id=plan.id, sid="k2", name="哈希表", difficulty=2)
        db.add_all([n1, n2])
        db.commit()
        return {
            "admin_id": admin.id,
            "other_id": other.id if other else None,
            "session_id": sess.id,
            "n1": n1.id,
            "n2": n2.id,
        }
    finally:
        db.close()


def test_note_create_update_clear(client, auth_headers):
    ids = _seed()
    n1 = ids["n1"]
    # 无笔记：GET 返回空内容（不 404）
    r = client.get(f"/api/knowledge/notes/{n1}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["content"] == ""

    # 创建
    r = client.put(f"/api/knowledge/notes/{n1}", json={"content": "  先序遍历口诀  "}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "先序遍历口诀"  # 去空白
    assert r.json()["updated_at"]

    # 更新
    r = client.put(f"/api/knowledge/notes/{n1}", json={"content": "改：中序=左根右"}, headers=auth_headers)
    assert r.json()["content"] == "改：中序=左根右"

    # 清空 -> 行删除（库中不留空笔记）
    r = client.put(f"/api/knowledge/notes/{n1}", json={"content": "   "}, headers=auth_headers)
    assert r.json()["content"] == ""
    from app.db import models

    db = SessionLocal()
    try:
        assert db.query(models.NodeNote).count() == 0
    finally:
        db.close()


def test_knowledge_has_note_flag(client, auth_headers):
    ids = _seed()
    client.put(f"/api/knowledge/notes/{ids['n1']}", json={"content": "有笔记"}, headers=auth_headers)
    r = client.get("/api/knowledge", headers=auth_headers)
    by_name = {i["name"]: i for i in r.json()["items"]}
    assert by_name["二叉树"]["has_note"] is True
    assert by_name["哈希表"]["has_note"] is False


def test_note_user_isolation(client, auth_headers):
    from app.db import models

    ids = _seed(with_other=True)
    db = SessionLocal()
    try:
        other_sess = models.Session(user_id=ids["other_id"], title="他人会话")
        db.add(other_sess)
        db.flush()
        other_plan = models.Plan(session_id=other_sess.id, version=1, content={})
        db.add(other_plan)
        db.flush()
        other_node = models.KnowledgeNode(plan_id=other_plan.id, sid="k1", name="他人节点")
        db.add(other_node)
        db.commit()
        other_node_id = other_node.id
    finally:
        db.close()

    # 他人节点：GET/PUT 均 404（不泄露存在性）
    assert client.get(f"/api/knowledge/notes/{other_node_id}", headers=auth_headers).status_code == 404
    assert (
        client.put(
            f"/api/knowledge/notes/{other_node_id}", json={"content": "x"}, headers=auth_headers
        ).status_code
        == 404
    )

    # 自己节点的笔记对他人不可见（用 other 的 token 需换用户——此处验证库级隔离：
    # admin 的笔记挂在 admin 名下，/knowledge 只查当前用户笔记）
    client.put(f"/api/knowledge/notes/{ids['n1']}", json={"content": "admin 私有"}, headers=auth_headers)
    db = SessionLocal()
    try:
        note = db.query(models.NodeNote).filter_by(node_id=ids["n1"]).one()
        assert note.user_id == ids["admin_id"]
    finally:
        db.close()


def test_note_cascade_on_session_delete(client, auth_headers):
    """删会话 -> 节点级联删 -> 笔记随之清理（不留孤儿行）。"""
    from app.db import models

    ids = _seed()
    client.put(f"/api/knowledge/notes/{ids['n1']}", json={"content": "待清理"}, headers=auth_headers)
    r = client.delete(f"/api/sessions/{ids['session_id']}", headers=auth_headers)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(models.NodeNote).count() == 0
    finally:
        db.close()


def test_note_backup_roundtrip(client, auth_headers):
    """笔记随备份导出（嵌于节点 note 字段），导入后按 sid 重关联还原。"""
    ids = _seed()
    client.put(f"/api/knowledge/notes/{ids['n1']}", json={"content": "备份笔记内容"}, headers=auth_headers)

    r = client.get("/api/backup/export", headers=auth_headers)
    assert r.status_code == 200
    blob = r.json()
    exported_node = blob["sessions"][0]["plans"][0]["knowledge_nodes"][0]
    assert exported_node["note"] == "备份笔记内容"

    r = client.post(
        "/api/backup/import",
        files={"file": ("backup.json", r.content, "application/json")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["node_notes"] == 1

    # 还原后同名节点两份（原 + 导入），均带笔记
    r = client.get("/api/knowledge", headers=auth_headers)
    noted = [i for i in r.json()["items"] if i["name"] == "二叉树" and i["has_note"]]
    assert len(noted) == 2
    for item in noted:
        rr = client.get(f"/api/knowledge/notes/{item['node_id']}", headers=auth_headers)
        assert rr.json()["content"] == "备份笔记内容"


def test_notes_require_auth(client):
    assert client.get("/api/knowledge/notes/1").status_code == 401
    assert client.put("/api/knowledge/notes/1", json={"content": "x"}).status_code == 401
