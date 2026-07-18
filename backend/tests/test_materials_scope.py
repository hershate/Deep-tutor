"""generate_study_materials §13 补全测试（iter 85）：
scope 局部生成 / index 文档 / 单元过多分模块 / 单文件 >500 行拆子文件。

SSE 模式复用 test_api_plans_materials（_make_plan/_resp/_units_of）与
test_api_plans_materials_sse（_UnitProvider/_parse_sse/_unit_resp）的器具。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.storage import delete_unit_content, read_unit_content, write_unit_content
from app.tools.generate_study_materials import (
    StudyMaterialsSpec,
    StudyUnitSpec,
    build_materials_index,
    render_materials_md,
)

from test_api_plans_materials import _make_plan, _resp, _units_of
from test_api_plans_materials_sse import _parse_sse, _UnitProvider, _unit_resp

_SSE_HEADERS = {"Accept": "text/event-stream"}


@pytest.fixture(autouse=True)
def _stub_unit_search(monkeypatch):
    """同 sse 测试：stub 单元补充资料检索防真实联网。"""
    from app.api import plans as plans_mod

    async def _none(query, ctx):
        return []

    monkeypatch.setattr(plans_mod, "search_unit_resources", _none)


def _gen_sse(client, auth_headers, plan_id, provider, body=None, monkeypatch=None):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    r = client.post(
        f"/api/plans/{plan_id}/materials",
        json=body or {"depth": "full"},
        headers={**auth_headers, **_SSE_HEADERS},
    )
    assert r.status_code == 200, r.text
    return r


# --- scope 局部生成（plan/02 §13 input scope）---


def test_scope_stage_indexes_partial_replace(client, auth_headers, monkeypatch):
    """scope.stage_indexes=[1] + refresh：仅替换「进阶」单元，其余保留；order 重排；stage_index 落库。"""
    plan_id = _make_plan(client, auth_headers)
    r = _gen_sse(
        client, auth_headers, plan_id,
        _UnitProvider([_unit_resp("### 概述\n\n第一版基础。")] * 3),
        monkeypatch=monkeypatch,
    )
    assert _parse_sse(r.text)[-1][0] == "done"
    units = _units_of(plan_id)
    assert [u.stage_index for u in units] == [0, 1, 2]  # 全量生成即写 stage_index
    base_path = units[0].materials[0].content_md_path

    # 局部重生成 stage 1（进阶）
    r2 = _gen_sse(
        client, auth_headers, plan_id,
        _UnitProvider([_unit_resp("### 概述\n\n第二版进阶。")]),
        body={"depth": "full", "refresh": True, "scope": {"stage_indexes": [1]}},
        monkeypatch=monkeypatch,
    )
    events = _parse_sse(r2.text)
    assert events[-1] == ("done", {"unit_count": 1, "degraded_count": 0})

    units = _units_of(plan_id)
    assert [u.title for u in units] == ["基础", "进阶", "实战"]
    assert [u.order for u in units] == [0, 1, 2]  # 重排后顺序不变
    assert [u.stage_index for u in units] == [0, 1, 2]
    # 仅进阶被替换：基础仍是第一版（同一路径未被清理），进阶是第二版
    assert Path(units[0].materials[0].content_md_path).read_text(encoding="utf-8").find("第一版基础。") >= 0
    assert "第二版进阶。" in Path(units[1].materials[0].content_md_path).read_text(encoding="utf-8")
    assert units[0].materials[0].content_md_path == base_path


def test_scope_invalid_stage_index_422(client, auth_headers, monkeypatch):
    plan_id = _make_plan(client, auth_headers)
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: _UnitProvider([]))
    r = client.post(
        f"/api/plans/{plan_id}/materials",
        json={"refresh": True, "scope": {"stage_indexes": [9]}},
        headers={**auth_headers, **_SSE_HEADERS},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_scope"


def test_scope_node_ids_selects_matching_stage(client, auth_headers, monkeypatch):
    """node_ids=["k1"]（name=JS）-> 仅选中 topics 含 JS 的 stage 0（基础）。"""
    plan_id = _make_plan(client, auth_headers)
    r = _gen_sse(
        client, auth_headers, plan_id,
        _UnitProvider([_unit_resp("### 概述\n\n正文。")]),
        body={"scope": {"node_ids": ["k1"]}},
        monkeypatch=monkeypatch,
    )
    events = _parse_sse(r.text)
    assert events[0] == ("unit_start", {"order": 1, "title": "基础", "total": 1})
    assert events[-1] == ("done", {"unit_count": 1, "degraded_count": 0})
    units = _units_of(plan_id)
    assert len(units) == 1 and units[0].title == "基础" and units[0].stage_index == 0


def test_scope_node_ids_no_match_422(client, auth_headers, monkeypatch):
    plan_id = _make_plan(client, auth_headers)
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: _UnitProvider([]))
    r = client.post(
        f"/api/plans/{plan_id}/materials",
        json={"scope": {"node_ids": ["不存在"]}},
        headers={**auth_headers, **_SSE_HEADERS},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_scope"


# --- index 文档（plan/02 §13 output index_url）---


def test_index_md_written_and_returned(client, auth_headers, monkeypatch):
    """SSE 生成后落盘 index.md；GET 列表与幂等 POST 均返回 index_md（含路线图/计划表/复习节点）。"""
    plan_id = _make_plan(client, auth_headers)
    r = _gen_sse(
        client, auth_headers, plan_id,
        _UnitProvider([_unit_resp("### 概述\n\n正文。")] * 3),
        monkeypatch=monkeypatch,
    )
    assert _parse_sse(r.text)[-1][0] == "done"

    g = client.get(f"/api/plans/{plan_id}/materials", headers=auth_headers)
    assert g.status_code == 200
    index_md = g.json()["index_md"]
    assert "## 路线图" in index_md
    assert "## 计划表" in index_md  # plan.schedule 存在
    assert "## 复习节点" in index_md
    assert "基础" in index_md and "实战" in index_md
    # units 元数据含 stage_index
    assert g.json()["units"][0]["stage_index"] == 0

    # 幂等 POST（非 refresh）也返回 index_md
    p = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert p.status_code == 200
    assert p.json()["index_md"] == index_md


def test_build_materials_index_sections():
    plan = {
        "intent": {"topic": "Go"},
        "schedule": [{"date": "2026-07-20", "stage_index": 0, "tasks": ["t"], "est_minutes": 30}],
        "knowledge_map": {"nodes": [{"id": "n1", "name": "协程", "must_learn": True}]},
    }
    spec = StudyMaterialsSpec(
        title="Go",
        units=[StudyUnitSpec(title="基础", objectives=["写 hello"], prerequisites=["无"], estimated_time="30 min")],
    )
    md = build_materials_index(plan, spec)
    assert "# Go · 自学路线图" in md
    assert "| 1 | 基础 | 30 min | 无 | 写 hello |" in md
    assert "| 2026-07-20 |" in md
    assert "- [ ] 单元 1：基础" in md
    assert "协程" in md


# --- 单元过多分模块（plan/02 §13 errors）---


def test_render_materials_md_modules_over_10():
    spec = StudyMaterialsSpec(
        title="T",
        units=[StudyUnitSpec(title=f"U{i}", content_md="x") for i in range(1, 13)],
    )
    md = render_materials_md(spec)
    assert "## 模块 1（单元 1-5）" in md
    assert "## 模块 2（单元 6-10）" in md
    assert "## 模块 3（单元 11-12）" in md
    assert "### 1. U1" in md  # 模块模式单元降为 ### 级
    # ≤10 单元不分模块
    small = StudyMaterialsSpec(title="T", units=[StudyUnitSpec(title="U1", content_md="x")])
    assert "模块" not in render_materials_md(small)


# --- 单文件 >500 行拆子文件（plan/02 §13 errors；storage 层）---


def test_write_unit_content_splits_long_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.storage.settings.data_dir", str(tmp_path))
    long_md = "### 概述\n\n" + "\n".join(f"行 {i}" for i in range(700))
    main = write_unit_content(1, 42, long_md)
    assert main.name == "unit_42.md"
    assert len(main.read_text(encoding="utf-8").split("\n")) <= 500
    p2 = main.with_name("unit_42.p2.md")
    assert p2.exists()
    # 串联读取还原全文
    restored = read_unit_content(main)
    assert "行 699" in restored and "行 0" in restored
    # 删除清理全部子文件
    delete_unit_content(main)
    assert not main.exists() and not p2.exists()


def test_write_unit_content_short_no_split(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.storage.settings.data_dir", str(tmp_path))
    main = write_unit_content(1, 7, "### 概述\n\n短内容")
    assert read_unit_content(main) == "### 概述\n\n短内容"
    assert not main.with_name("unit_7.p2.md").exists()
