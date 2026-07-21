"""iter 245 tools 层加固回归测试：

- schedule_plan 容量校验从 upstream.understand_intent 派生 daily_minutes（生产 args 从不携带）
  + 单日排期同样校验
- search_resources：引擎全面失败降级对称（bing_api 同 bing_web）/ 显式 stage_topics 白名单 /
  畸形候选 url 单条丢弃不拖垮整批
- BudgetTracker：NaN/负 cost 拒绝入账、NaN limit 按不限
- build_knowledge_map：重复节点 id 去重
"""

from __future__ import annotations

import asyncio
import math

from app.agent.budget import BudgetTracker
from app.agent.context import ExecContext
from app.tools.base import ToolInput
from app.tools.schedule_plan import check_schedule


def _inp(args=None, upstream=None) -> ToolInput:
    return ToolInput(tool="t", session_id="s", args=args or {}, upstream=upstream or {})


# --- schedule_plan 容量校验 ---


def test_check_schedule_derives_daily_from_upstream():
    upstream = {"understand_intent": {"data": {"daily_minutes": 60}}}
    data = {"schedule": [
        {"date": "2026-07-21", "stage_index": 0, "tasks": ["a"], "est_minutes": 300},
        {"date": "2026-07-22", "stage_index": 0, "tasks": ["b"], "est_minutes": 300},
    ]}
    out = check_schedule(data, _inp(upstream=upstream))
    # 600 分钟 > 2 天 × 60 分钟容量——此前 args 无 daily_minutes 时校验整体失效
    assert out["warning"] == "insufficient_time"
    assert out["required_days"] == 10
    assert out["capacity_minutes"] == 120


def test_check_schedule_single_day_capacity():
    upstream = {"understand_intent": {"data": {"daily_minutes": 60}}}
    data = {"schedule": [{"date": "2026-07-21", "stage_index": 0, "tasks": ["a"], "est_minutes": 600}]}
    out = check_schedule(data, _inp(upstream=upstream))
    assert out["warning"] == "insufficient_time"  # 单日排期此前永不做容量校验
    assert out["span_days"] == 1


def test_check_schedule_no_daily_no_warning():
    # daily_minutes 不可得（None）-> 跳过容量校验但不炸（B17 语义保持）
    data = {"schedule": [{"date": "2026-07-21", "stage_index": 0, "tasks": ["a"], "est_minutes": 600}]}
    out = check_schedule(data, _inp(upstream={"understand_intent": {"data": {"daily_minutes": None}}}))
    assert "warning" not in out
    assert out["total_minutes"] == 600


# --- search_resources ---


def test_search_engine_total_failure_degrades_symmetrically(monkeypatch):
    from app.tools import search_resources as sr

    async def _fail(topic, engine, key=""):
        raise RuntimeError("HTTP 401 invalid key")

    monkeypatch.setattr(sr, "_search_web", _fail)
    ctx = ExecContext(session_id="s", trace_id="t", bing_api_key="fake-key")  # -> engine bing_api
    inp = _inp(upstream={"design_stages": {"data": {"stages": [{"topics": [{"name": "微积分"}]}]}}})
    out = asyncio.run(sr.search_resources(inp, ctx))
    # bing_api 全面失败：此前报 ok+空结果（与真空结果不可区分），现与 bing_web 同构降级
    assert out.status == "repaired"
    assert out.errors[0].code == "search_unavailable"
    assert out.data["engine"] == "bing_api"
    assert out.data["suggested_keywords"]


def test_derive_stage_topics_explicit_whitelist():
    from app.tools.search_resources import _derive_stage_topics

    upstream = {"design_stages": {"data": {"stages": [{"topics": [{"name": "派生主题"}]}]}}}
    # str：此前按字符迭代成单字查询 -> 忽略改派生
    assert _derive_stage_topics(_inp(args={"stage_topics": "abc"}, upstream=upstream)) == [["派生主题"]]
    # dict：此前在查询推导处 TypeError -> 忽略改派生
    assert _derive_stage_topics(_inp(args={"stage_topics": {"a": 1}}, upstream=upstream)) == [["派生主题"]]
    # 合法 list：原样清洗（截断/去空/限量）
    out = _derive_stage_topics(_inp(args={"stage_topics": [[" 主题一 ", "", "x" * 200]]}))
    assert out == [["主题一", "x" * 150]]


def test_verify_candidates_malformed_url_dropped(monkeypatch):
    from app.tools import search_resources as sr
    from app.tools.base import ToolOutput

    async def _fake_verify(v_inp, ctx, client=None):
        return ToolOutput(tool="verify_url", status="ok", data={"label": "ok", "final_url": v_inp.args["url"]})

    monkeypatch.setattr(sr, "verify_url", _fake_verify)
    ctx = ExecContext(session_id="s", trace_id="t")
    items = [(0, {"url": ["畸形"]}), (0, {"url": "http://ok.example"})]
    verdicts = asyncio.run(sr._verify_candidates(items, "s", ctx))
    # 畸形 url 单条丢弃（此前 unhashable TypeError 拖垮整工具）；合法候选不受影响
    assert list(verdicts.keys()) == ["http://ok.example"]


# --- BudgetTracker 非法值防御 ---


def test_budget_rejects_nan_and_negative():
    b = BudgetTracker(limit=1.0)
    b.add(0.5)
    b.add(math.nan)  # 拒绝入账（此前 spent=NaN -> exceeded 恒 False，守卫静默失效）
    b.add(-0.3)  # 负 cost 拒绝（抵减已花费变相绕过预算）
    b.add(math.inf)
    assert b.spent == 0.5
    assert not b.exceeded()
    b.add(0.5)
    assert b.exceeded()  # err.md B15：达上限即超限（语义保持）


def test_budget_nan_limit_treated_as_unlimited():
    b = BudgetTracker(limit=math.nan)
    assert b.limit == 0.0
    b.add(999.0)
    assert not b.exceeded()  # 非法 limit 按不限（此前 exceeded 恒 False 但语义未定义）


# --- build_knowledge_map 重复节点 id ---


def test_clean_map_dedupes_duplicate_node_ids():
    from app.tools.build_knowledge_map import clean_map

    data = {
        "nodes": [
            {"id": "k1", "name": "首"},
            {"id": "k1", "name": "重复"},
            {"id": "k2", "name": "二"},
        ],
        "edges": [{"from": "k1", "to": "k2", "relation": "前置"}],
    }
    out = clean_map(data, _inp())
    assert [n["name"] for n in out["nodes"]] == ["首", "二"]  # 保首个
    assert out["edges"] == [{"from": "k1", "to": "k2", "relation": "前置"}]  # 边语义确定
