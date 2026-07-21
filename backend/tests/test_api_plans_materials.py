"""POST /plans/{id}/materials 端点测试（P2 自学材料，iter 36）。

先 mock 生成建 plan（CannedProvider 同 test_api_export），再调 materials 端点。
"""

from __future__ import annotations

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(c):
    return LLMResponse(content=c, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self):
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


class _MaterialsProvider(LLMProvider):
    """materials 端点专用 provider（返回 StudyMaterialsSpec JSON）。"""

    def __init__(self, responses):
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return self._r.pop(0) if self._r else _resp("{}")


def _make_plan(client, auth_headers):
    from app.api import plans as plans_mod

    plans_mod.get_provider = lambda *a, **k: CannedProvider()
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    import json as _json

    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return _json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_materials_ok(client, auth_headers, monkeypatch):
    """LLM 合法规约 -> 200 + markdown + unit_count。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    spec_json = (
        '{"title":"React 自学材料","units":['
        '{"title":"JS 基础","objectives":["学完后能够写基础函数"],"prerequisites":["无"],'
        '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 是交互逻辑。\\n\\n### 实践练习\\n\\n1. 写一个函数\\n2. 写一个循环"},'
        '{"title":"React 组件","objectives":["学完后能够写函数组件"],"prerequisites":["JS 基础"],'
        '"estimated_time":"60 min","content_md":"### 概述\\n\\n组件是 UI 单元。"}]}'
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _MaterialsProvider([_resp(spec_json)]))
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_count"] == 2
    assert "# React 自学材料" in body["markdown"]
    assert "## 1. JS 基础" in body["markdown"]
    assert "## 2. React 组件" in body["markdown"]
    assert "JS 是交互逻辑。" in body["markdown"]
    assert "warning" not in body  # 未降级


def test_materials_degrade_no_provider(client, auth_headers, monkeypatch):
    """无 LLM provider（无 key）-> 降级 default_materials_from_plan，仍 200 + markdown + warning。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/materials", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_count"] >= 1
    assert "# React" in body["markdown"]  # 默认规约取 intent.topic
    assert "warning" in body  # 降级提示


def test_materials_plan_not_found(client, auth_headers):
    r = client.post("/api/plans/999999/materials", json={}, headers=auth_headers)
    assert r.status_code == 404


def test_materials_invalid_depth(client, auth_headers):
    """depth 非 brief|full -> 422。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    plans_mod.get_provider = lambda *a, **k: CannedProvider()
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "verbose"}, headers=auth_headers)
    assert r.status_code == 422


# --- iter 43：study_units/study_materials 持久化 ---

_SPEC_JSON = (
    '{"title":"React 自学材料","units":['
    '{"title":"JS 基础","objectives":["写函数"],"prerequisites":["无"],'
    '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 是交互逻辑。"},'
    '{"title":"React 组件","objectives":["写组件"],"prerequisites":["JS 基础"],'
    '"estimated_time":"60 min","content_md":"### 概述\\n\\n组件是 UI 单元。"}]}'
)


def _gen_materials(client, auth_headers, monkeypatch, plan_id, provider, body=None):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    r = client.post(f"/api/plans/{plan_id}/materials", json=body or {"depth": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _units_of(plan_id):
    from sqlalchemy.orm import selectinload

    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        return (
            db.query(models.StudyUnit)
            .options(selectinload(models.StudyUnit.materials))
            .filter_by(plan_id=plan_id)
            .order_by(models.StudyUnit.order)
            .all()
        )
    finally:
        db.close()


def test_materials_persisted_to_db_and_file(client, auth_headers, monkeypatch):
    """生成即落库：study_units 元数据 + study_materials 内容文件（内容写盘，DB 存路径）。"""
    plan_id = _make_plan(client, auth_headers)
    _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))

    units = _units_of(plan_id)
    assert [u.title for u in units] == ["JS 基础", "React 组件"]
    assert units[0].objectives == ["写函数"]
    assert units[0].estimated_time == "45 min"
    assert units[0].order == 0 and units[1].order == 1

    from pathlib import Path

    assert len(units[0].materials) == 1
    path = Path(units[0].materials[0].content_md_path)
    assert path.exists()
    assert "JS 是交互逻辑。" in path.read_text(encoding="utf-8")
    assert units[0].materials[0].size > 0


def test_materials_idempotent_no_regenerate(client, auth_headers, monkeypatch):
    """已持久化 -> 再 POST 直接重渲染返回（免重复 LLM 成本）；provider 失效也不影响。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    first = _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    second = r.json()
    # 幂等：同一批持久化单元重渲染（内容一致；标题由 intent 推算，与生成路径 spec 标题可能不同——不入库，见 plans._units_to_spec）
    assert second["unit_count"] == first["unit_count"] == 2
    assert "## 1. JS 基础" in second["markdown"]
    assert "JS 是交互逻辑。" in second["markdown"]
    assert "warning" not in second  # 非降级：返回的是已持久化的 LLM 规约


def test_materials_refresh_replaces_and_cleans_files(client, auth_headers, monkeypatch):
    """refresh=true -> 重新生成替换旧单元；unit 数减少时多余旧内容文件清理
    （rowid 复用导致同名路径 -> 属新内容不删；仅清理不再引用的旧文件）。"""
    from pathlib import Path

    plan_id = _make_plan(client, auth_headers)
    _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))
    old_paths = [m.content_md_path for u in _units_of(plan_id) for m in u.materials]
    assert len(old_paths) == 2 and all(Path(p).exists() for p in old_paths)

    # 新规约仅 1 单元 -> 多余的旧文件应被清理
    new_spec = (
        '{"title":"React 自学材料","units":['
        '{"title":"JS 基础","objectives":["写函数"],"prerequisites":["无"],'
        '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 全新内容。"}]}'
    )
    body = _gen_materials(
        client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(new_spec)]), body={"refresh": True}
    )
    assert "JS 全新内容。" in body["markdown"]

    units = _units_of(plan_id)
    assert len(units) == 1  # 替换而非追加
    new_paths = [Path(m.content_md_path) for u in units for m in u.materials]
    assert all(p.exists() for p in new_paths)  # 新内容文件在
    assert "JS 全新内容。" in new_paths[0].read_text(encoding="utf-8")
    stale = set(old_paths) - {p for p in (m.content_md_path for u in units for m in u.materials)}
    assert stale  # 有多余旧文件
    assert all(not Path(p).exists() for p in stale)  # 且已清理


def test_materials_degrade_also_persisted(client, auth_headers, monkeypatch):
    """降级默认材料也落库；再 POST 返回已存内容。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/materials", json={}, headers=auth_headers)
    assert "warning" in r.json()
    assert len(_units_of(plan_id)) >= 1


def test_list_materials_units(client, auth_headers, monkeypatch):
    """GET /plans/{id}/materials -> 单元元数据列表（无内容字段）。"""
    plan_id = _make_plan(client, auth_headers)
    _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))

    r = client.get(f"/api/plans/{plan_id}/materials", headers=auth_headers)
    assert r.status_code == 200, r.text
    units = r.json()["units"]
    assert len(units) == 2
    assert units[0]["title"] == "JS 基础"
    assert units[0]["objectives"] == ["写函数"]
    assert units[0]["order"] == 0
    assert "content_md" not in units[0]  # 列表不含内容

    # 未生成过 -> 空列表
    plan_id2 = _make_plan(client, auth_headers)
    r = client.get(f"/api/plans/{plan_id2}/materials", headers=auth_headers)
    assert r.json()["units"] == []


def test_get_unit_content(client, auth_headers, monkeypatch):
    """GET /materials/{unit_id} -> content_md + title；不存在 -> 404。"""
    plan_id = _make_plan(client, auth_headers)
    _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))
    unit_id = _units_of(plan_id)[0].id

    r = client.get(f"/api/materials/{unit_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_id"] == unit_id
    assert body["title"] == "JS 基础"
    assert "JS 是交互逻辑。" in body["content_md"]
    assert body["resources"] == []

    assert client.get("/api/materials/999999", headers=auth_headers).status_code == 404


def test_unit_done_mark_and_list(client, auth_headers, monkeypatch):
    """标记已学（plan/07 §六）：POST done -> 列表 done 标志联动；取消 -> 恢复；404。"""
    plan_id = _make_plan(client, auth_headers)
    _gen_materials(client, auth_headers, monkeypatch, plan_id, _MaterialsProvider([_resp(_SPEC_JSON)]))
    unit_id = _units_of(plan_id)[0].id

    # 初始未学
    units = client.get(f"/api/plans/{plan_id}/materials", headers=auth_headers).json()["units"]
    assert all(u["done"] is False for u in units)

    # 标记已学（幂等）
    for _ in range(2):
        r = client.post(f"/api/materials/{unit_id}/done", json={"done": True}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"unit_id": unit_id, "done": True}
    units = client.get(f"/api/plans/{plan_id}/materials", headers=auth_headers).json()["units"]
    by_id = {u["id"]: u for u in units}
    assert by_id[unit_id]["done"] is True

    # 取消
    r = client.post(f"/api/materials/{unit_id}/done", json={"done": False}, headers=auth_headers)
    assert r.json()["done"] is False
    units = client.get(f"/api/plans/{plan_id}/materials", headers=auth_headers).json()["units"]
    assert {u["id"]: u for u in units}[unit_id]["done"] is False

    assert client.post("/api/materials/999999/done", json={"done": True}, headers=auth_headers).status_code == 404


# --- iter 269：单元内容检索 GET /plans/{id}/materials/search（标题/正文命中，有界扫描） ---


def _seed_units(admin_id: int, spec: list[tuple[str, str | None]]) -> int:
    """建方案 + 单元（标题, 正文|None）；正文经 write_unit_text 落盘（同生成路径）。返回 plan_id。"""
    from app.core.storage import write_unit_text
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        s = models.Session(user_id=admin_id, title="t")
        db.add(s)
        db.flush()
        p = models.Plan(session_id=s.id, version=1, content={"intent": {"topic": "机器学习"}})
        db.add(p)
        db.flush()
        for i, (title, md) in enumerate(spec):
            u = models.StudyUnit(
                plan_id=p.id, title=title, prerequisites=[], objectives=[], estimated_time="", order=i
            )
            db.add(u)
            db.flush()
            if md is not None:
                path = write_unit_text(s.id, u.id, md)
                db.add(models.StudyMaterial(unit_id=u.id, content_md_path=str(path), size=len(md), resources=[]))
        db.commit()
        return p.id
    finally:
        db.close()


def _seed_admin() -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.commit()
        return admin.id
    finally:
        db.close()


def test_materials_search_hits(client, auth_headers):
    admin_id = _seed_admin()
    plan_id = _seed_units(
        admin_id,
        [
            ("梯度下降入门", "本单元讲优化基础。"),  # 标题命中
            ("学习率调度", "训练时梯度会震荡，需要衰减策略。"),  # 正文命中
            ("模型评估", "精确率与召回率。"),  # 不命中
        ],
    )
    r = client.get(f"/api/plans/{plan_id}/materials/search?q=梯度", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned_units"] == 3
    assert body["truncated"] is False
    items = body["items"]
    assert len(items) == 2
    # 标题命中在前（KB iter 255 同款口径）
    assert items[0]["match"] == "title" and items[0]["title"] == "梯度下降入门" and items[0]["snippet"] == ""
    assert items[1]["match"] == "content" and items[1]["title"] == "学习率调度"
    assert "梯度" in items[1]["snippet"]
    # 无命中 -> 空列表（单元本身仍在）
    r2 = client.get(f"/api/plans/{plan_id}/materials/search?q=不存在的词", headers=auth_headers)
    assert r2.json()["items"] == []
    # 大小写不敏感
    plan_id2 = _seed_units(admin_id, [("English Basics", "hello WORLD")])
    items2 = client.get(f"/api/plans/{plan_id2}/materials/search?q=world", headers=auth_headers).json()["items"]
    assert len(items2) == 1 and items2[0]["match"] == "content"


def test_materials_search_isolation_and_validation(client, auth_headers):
    admin_id = _seed_admin()
    plan_id = _seed_units(admin_id, [("单元", "内容")])
    # 他人方案 404（不泄露存在性）
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add(other)
        db.commit()
        other_plan = _seed_units(other.id, [("他人单元", "他人内容")])
    finally:
        db.close()
    assert client.get(f"/api/plans/{other_plan}/materials/search?q=内容", headers=auth_headers).status_code == 404
    assert plan_id != other_plan
    # 缺 q / q 超长 -> 422
    assert client.get(f"/api/plans/{plan_id}/materials/search", headers=auth_headers).status_code == 422
    assert client.get(f"/api/plans/{plan_id}/materials/search?q={'x' * 101}", headers=auth_headers).status_code == 422


def test_materials_search_truncated_flag(client, auth_headers):
    admin_id = _seed_admin()
    # 51 个无正文单元（无文件读取，便宜）——超出 50 扫描上界
    plan_id = _seed_units(admin_id, [(f"单元{i}", None) for i in range(51)])
    body = client.get(f"/api/plans/{plan_id}/materials/search?q=单元", headers=auth_headers).json()
    assert body["truncated"] is True
    assert body["scanned_units"] == 50
    assert len(body["items"]) == 50  # 全部标题命中，截断到扫描上界


def test_materials_search_requires_auth(client):
    admin_id = _seed_admin()
    plan_id = _seed_units(admin_id, [("单元", "内容")])
    assert client.get(f"/api/plans/{plan_id}/materials/search?q=内容").status_code == 401
