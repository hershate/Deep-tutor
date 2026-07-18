"""知识库搜索扩展测试（iter 148）：q 同时匹配节点名与本人笔记内容；他人笔记不匹配；隔离。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed() -> None:
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        s1 = models.Session(user_id=admin.id, title="A")
        s2 = models.Session(user_id=other.id, title="B")
        db.add_all([s1, s2])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "信号处理"}})
        p2 = models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "他人方案"}})
        db.add_all([p1, p2])
        db.flush()
        n1 = models.KnowledgeNode(plan_id=p1.id, sid="n1", name="采样定理", difficulty=2)
        n2 = models.KnowledgeNode(plan_id=p1.id, sid="n2", name="频谱分析", difficulty=3)
        n3 = models.KnowledgeNode(plan_id=p2.id, sid="n3", name="傅里叶变换", difficulty=3)  # 他人节点
        db.add_all([n1, n2, n3])
        db.flush()
        db.add_all(
            [
                # n2 名不含「傅里叶」，但本人笔记含 -> 应命中
                models.NodeNote(user_id=admin.id, node_id=n2.id, content="与傅里叶级数的关系"),
                # n1 名不含，但**他人**在我的节点上写的笔记含 -> 不应命中（join 限定 user_id）
                models.NodeNote(user_id=other.id, node_id=n1.id, content="傅里叶他人笔记"),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_knowledge_search_matches_note_content(client, auth_headers):
    _seed()
    r = client.get("/api/knowledge?q=傅里叶", headers=auth_headers)
    assert r.status_code == 200, r.text
    names = {i["name"] for i in r.json()["items"]}
    # n2（本人笔记命中）；n1 仅他人笔记命中 -> 排除；n3 他人节点 -> 隔离
    assert names == {"频谱分析"}


def test_knowledge_search_name_still_matches(client, auth_headers):
    _seed()
    r = client.get("/api/knowledge?q=采样", headers=auth_headers)
    assert {i["name"] for i in r.json()["items"]} == {"采样定理"}
    # 无匹配 -> 空
    assert client.get("/api/knowledge?q=不存在词", headers=auth_headers).json()["total"] == 0
