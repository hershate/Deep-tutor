"""按单元重生成自学材料测试（iter 98）：unit_ids 选中重生成 / 互斥与 404/422 校验。

无 ANTHROPIC_API_KEY 的测试环境走确定性降级路径（brief_unit_from_stage），
无需 mock LLM 即可验证「删哪些、生成哪些、order 重排、他单元不受影响」。
"""

from __future__ import annotations

from app.db.base import SessionLocal


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(u)
        db.commit()
    finally:
        db.close()


def _seed_plan_with_units() -> dict:
    """plan 含两阶段；两个单元（stage_index 0/1）+ 内容文件。返回关键 id。"""
    from app.core.storage import write_unit_content
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=1, title="单元重生成会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id,
            version=1,
            content={
                "intent": {"topic": "测试主题"},
                "stages": [
                    {"index": 0, "name": "阶段一", "topics": [{"name": "知识点A"}]},
                    {"index": 1, "name": "阶段二", "topics": [{"name": "知识点B"}]},
                ],
            },
        )
        db.add(plan)
        db.flush()
        ids = []
        for i, (stage_idx, title) in enumerate([(0, "单元一"), (1, "单元二")]):
            unit = models.StudyUnit(plan_id=plan.id, stage_index=stage_idx, title=title, order=i)
            db.add(unit)
            db.flush()
            path = write_unit_content(sess.id, unit.id, f"# {title}\n\n原始内容")
            db.add(models.StudyMaterial(unit_id=unit.id, content_md_path=str(path), size=10))
            ids.append(unit.id)
        # 无 stage 归属单元（伪阶段路径）
        orphan = models.StudyUnit(plan_id=plan.id, stage_index=None, title="游离单元", order=2)
        db.add(orphan)
        db.flush()
        path = write_unit_content(sess.id, orphan.id, "# 游离单元\n\n原始内容")
        db.add(models.StudyMaterial(unit_id=orphan.id, content_md_path=str(path), size=10))
        ids.append(orphan.id)
        db.commit()
        return {"plan_id": plan.id, "unit_ids": ids}
    finally:
        db.close()


def _units():
    from app.db import models

    db = SessionLocal()
    try:
        return (
            db.query(models.StudyUnit)
            .order_by(models.StudyUnit.order)
            .all()
        )
    finally:
        db.close()


def _post_materials(client, headers, plan_id, body, sse=True):
    h = dict(headers)
    if sse:
        h["Accept"] = "text/event-stream"
    return client.post(f"/api/plans/{plan_id}/materials", json=body, headers=h)


def test_unit_ids_regenerates_selected_only(client, auth_headers):
    _mk_admin()
    seeded = _seed_plan_with_units()
    target = seeded["unit_ids"][1]  # 单元二
    r = _post_materials(client, auth_headers, seeded["plan_id"], {"unit_ids": [target]})
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers["content-type"]
    assert "unit_done" in r.text and "done" in r.text

    units = _units()
    assert len(units) == 3  # 总数不变（选中行替换，不是追加）
    by_title = {u.title: u for u in units}
    assert by_title["单元一"].id == seeded["unit_ids"][0]  # 未选中单元原样保留（同一行）
    # 选中单元替换为新行（标题取所属 stage 名，与整档生成同一规则）
    assert by_title["阶段二"].id != target
    assert by_title["阶段二"].stage_index == 1  # stage 归属保持
    # order 重排稳定（stage 0/1/None 顺序）
    assert [u.title for u in units] == ["单元一", "阶段二", "游离单元"]


def test_unit_ids_orphan_unit_uses_pseudo_stage(client, auth_headers):
    """stage_index=None 的游离单元经伪阶段（单元标题）重生成，不报错。"""
    _mk_admin()
    seeded = _seed_plan_with_units()
    orphan = seeded["unit_ids"][2]
    r = _post_materials(client, auth_headers, seeded["plan_id"], {"unit_ids": [orphan]})
    assert r.status_code == 200, r.text
    units = _units()
    assert len(units) == 3
    assert {u.title for u in units} == {"单元一", "单元二", "游离单元"}


def test_unit_ids_multiple_selected(client, auth_headers):
    _mk_admin()
    seeded = _seed_plan_with_units()
    two = seeded["unit_ids"][:2]
    r = _post_materials(client, auth_headers, seeded["plan_id"], {"unit_ids": two})
    assert r.status_code == 200, r.text
    units = _units()
    assert len(units) == 3
    assert {u.id for u in units if u.title != "游离单元"}.isdisjoint(two)  # 两个都被替换


def test_unit_ids_not_found(client, auth_headers):
    _mk_admin()
    seeded = _seed_plan_with_units()
    r = _post_materials(client, auth_headers, seeded["plan_id"], {"unit_ids": [99999]})
    assert r.status_code == 404
    assert len(_units()) == 3  # 未做任何删除


def test_unit_ids_conflicts_with_scope(client, auth_headers):
    _mk_admin()
    seeded = _seed_plan_with_units()
    r = _post_materials(
        client, auth_headers, seeded["plan_id"],
        {"unit_ids": [seeded["unit_ids"][0]], "scope": {"stage_indexes": [0]}},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_scope"
    assert len(_units()) == 3


def test_unit_ids_rejected_on_sync_json_path(client, auth_headers):
    """非 SSE 请求（同步 JSON 路径）不支持按单元重生成 -> 422 且不删任何单元。"""
    _mk_admin()
    seeded = _seed_plan_with_units()
    r = _post_materials(client, auth_headers, seeded["plan_id"], {"unit_ids": [seeded["unit_ids"][0]]}, sse=False)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_scope"
    units = _units()
    assert len(units) == 3 and units[0].id == seeded["unit_ids"][0]
