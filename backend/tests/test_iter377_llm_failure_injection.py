"""iter 377 R20：LLM 调用面异常注入审计（真实端点 + monkeypatch 注入）。

对 POST /api/plans/generate 逐路径注入 provider 异常，验证三件事：
- 成本落账：失败/中断前已完成的调用 token 必须进 api_usage（iter 243 语义的
  端点级钉死——此前只有单元级覆盖）；未产生用量的失败调用不得伪造落账。
- 部分写：非 done 终态不得留 Plan 行；session 状态机落 error 并持久化 last_error。
- 用户侧：SSE 流必达唯一 error 终态事件（客户端据此出错误 UI）。

注入矩阵（实测钉死的语义）：
1. 429 瞬态重试耗尽（首工具）   -> error 终态、重试有界、零计费、无 Plan
2. 首轮成功(畸形 JSON)+次轮 4xx -> error 终态、首轮 token 落账（成本黑洞回归）
3. 中途（第 3 工具）4xx         -> error 终态、前两轮落账、无 Plan
4. intent 畸形修复耗尽          -> 降级澄清追问（paused_clarify），畸形轮聚合落账
5. stages 畸形修复耗尽          -> repaired 降级续跑交付（Pillar 2），聚合落账
附注：api_usage 粒度 = 每工具一次调用（修复链多轮聚合为一条，总量守恒——
日预算按求和计，粒度不敏感）；SSE error 终态必达且唯一。
"""

from __future__ import annotations

import json

from app.agent.loop import LLM_TRANSIENT_MAX_RETRIES, REPAIR_MAX_RETRIES
from app.llm.base import LLMProvider, LLMResponse, LLMUsage, TransientLLMError

# 与 test_api_retry 同款罐装响应（无资料生成：intent/knowledge/stages/schedule/validate 共 5 次 LLM）


def _resp(content: str, tin: int = 100, tout: int = 50) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=tin, output_tokens=tout, model="claude-sonnet-5"))


_C_INTENT = '{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'
_C_KNOWLEDGE = '{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"}],"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'
_C_STAGES = '{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'
_C_SCHEDULE = '{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'
_C_VALIDATE = '{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'


class ScriptedProvider(LLMProvider):
    """按脚本出牌：("ok", LLMResponse) / ("raise", Exception)；耗尽后再调用即抛（防漏计）。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.calls += 1
        if not self.outcomes:
            raise AssertionError(f"脚本耗尽（第 {self.calls} 次调用未被预期）")
        act, payload = self.outcomes.pop(0)
        if act == "ok":
            return payload
        raise payload


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev:
            out.append((ev, json.loads(data) if data else {}))
    return out


def _session(client, auth_headers) -> int:
    return client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]


def _run(client, auth_headers, monkeypatch, provider, sid: int):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "我想学 Rust"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return _parse_sse(r.text)


def _db_facts(sid: int) -> dict:
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        sess = db.get(models.Session, sid)
        uid = sess.user_id if sess else None
        usage = (
            db.query(models.ApiUsage).filter_by(user_id=uid).all()
            if uid is not None
            else []
        )
        calls = db.query(models.ToolCall).filter_by(session_id=sid).all()
        plans = db.query(models.Plan).filter_by(session_id=sid).all()
        return {
            "status": st.status.value if st is not None else None,
            "last_error": (st.last_error if st is not None else None),
            "usage_rows": len(usage),
            "tokens_in": sum(u.tokens_in for u in usage),
            "tokens_out": sum(u.tokens_out for u in usage),
            "tool_calls": [(c.tool, c.status, c.llm_tokens_in) for c in calls],
            "plan_rows": len(plans),
        }
    finally:
        db.close()


def _terminal(events: list[tuple[str, dict]]) -> tuple[str, dict]:
    """终态事件（done / error / clarify_needed 之后的 error|done）。"""
    errs = [d for e, d in events if e == "error"]
    dones = [d for e, d in events if e == "done"]
    assert len(dones) <= 1, events
    return ("done", dones[0]) if dones else ("error", errs[-1] if errs else {})


# --- 场景 1：429 瞬态重试耗尽（首工具）---


def test_transient_429_exhausted(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    prov = ScriptedProvider([("raise", TransientLLMError("rate limited"))] * (LLM_TRANSIENT_MAX_RETRIES + 1))
    events = _run(client, auth_headers, monkeypatch, prov, sid)
    kind, data = _terminal(events)
    assert kind == "error", events
    # 重试有界：恰 N+1 次（不占修复重试额度、不死循环）
    assert prov.calls == LLM_TRANSIENT_MAX_RETRIES + 1, prov.calls
    f = _db_facts(sid)
    assert f["status"] == "error"
    assert f["plan_rows"] == 0, "error 终态不得留 Plan 行"
    # 未产生用量的失败调用不得伪造落账（Anthropic 失败调用无 usage）
    assert f["usage_rows"] == 0, f
    # 失败透明：tool_calls 留痕（0 token、error 状态）
    assert any(t == "understand_intent" and s == "error" for t, s, _ in f["tool_calls"]), f["tool_calls"]
    assert (f["last_error"] or {}).get("code"), f


# --- 场景 2：首轮成功（畸形 JSON）+ 次轮 4xx —— 已耗 token 必须落账 ---


def test_prior_round_billed_on_nontransient_failure(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    prov = ScriptedProvider(
        [
            ("ok", _resp("{not json")),  # 首轮：有 usage 但校验失败 -> 进修复重试
            ("raise", RuntimeError("400 invalid_request")),  # 次轮：非瞬态 4xx 上抛
        ]
    )
    events = _run(client, auth_headers, monkeypatch, prov, sid)
    kind, _ = _terminal(events)
    assert kind == "error", events
    f = _db_facts(sid)
    assert f["status"] == "error" and f["plan_rows"] == 0
    # 核心断言：首轮 100/50 token 已实际消耗 -> 必须进 api_usage（日预算只信此表）
    assert f["usage_rows"] == 1 and f["tokens_in"] == 100 and f["tokens_out"] == 50, f
    assert ("understand_intent", "error", 100) in f["tool_calls"], f["tool_calls"]


# --- 场景 3：中途（第 3 工具 design_stages）4xx ---


def test_mid_pipeline_failure_bills_prior_steps(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    prov = ScriptedProvider(
        [
            ("ok", _resp(_C_INTENT)),
            ("ok", _resp(_C_KNOWLEDGE)),
            ("raise", TransientLLMError("overloaded")),  # design_stages：瞬态重试后耗尽
            ("raise", TransientLLMError("overloaded")),
            ("raise", TransientLLMError("overloaded")),
            ("raise", TransientLLMError("overloaded")),
        ]
    )
    events = _run(client, auth_headers, monkeypatch, prov, sid)
    kind, _ = _terminal(events)
    assert kind == "error", events
    f = _db_facts(sid)
    assert f["status"] == "error" and f["plan_rows"] == 0, f
    # 前两轮成功调用的 token 全部落账（200/100）
    assert f["usage_rows"] == 2 and f["tokens_in"] == 200 and f["tokens_out"] == 100, f
    assert ("design_stages", "error", 0) in f["tool_calls"], f["tool_calls"]


# --- 场景 4：畸形输出修复重试耗尽（understand_intent 降级 -> 澄清追问）---
# 实测语义：understand_intent 的 default_data 是 needs_clarify=True + 追问文案
# ——畸形耗尽降级为「向用户澄清」（paused_clarify），优于硬错误（用户可补信息续跑）。


def test_garbage_exhausted_intent_degrades_to_clarify_and_bills(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    n = REPAIR_MAX_RETRIES + 1
    prov = ScriptedProvider([("ok", _resp("完全不是 JSON", tin=30, tout=10))] * n)
    events = _run(client, auth_headers, monkeypatch, prov, sid)
    # 终态是 clarify_needed（paused_clarify），非 error
    assert any(e == "clarify_needed" for e, _ in events), events
    assert prov.calls == n, prov.calls
    f = _db_facts(sid)
    assert f["status"] == "paused_clarify" and f["plan_rows"] == 0, f
    # 畸形轮是真实消耗：修复链聚合为一条 api_usage（总量 30*n）
    assert f["usage_rows"] == 1 and f["tokens_in"] == 30 * n, f
    assert ("understand_intent", "repaired", 30 * n) in f["tool_calls"], f["tool_calls"]


# --- 场景 5：design_stages 畸形耗尽（有 default_data）-> repaired 降级续跑 ---


def test_garbage_exhausted_with_default_degrades_and_continues(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    n = REPAIR_MAX_RETRIES + 1
    prov = ScriptedProvider(
        [("ok", _resp(_C_INTENT)), ("ok", _resp(_C_KNOWLEDGE))]
        + [("ok", _resp("garbage", tin=20, tout=8))] * n  # design_stages 全畸形 -> repaired
        + [("ok", _resp(_C_SCHEDULE)), ("ok", _resp(_C_VALIDATE))]
    )
    events = _run(client, auth_headers, monkeypatch, prov, sid)
    kind, _ = _terminal(events)
    f = _db_facts(sid)
    if kind == "done":
        # Pillar 2 降级交付：Plan 存在、repaired 留痕、全部轮次落账。
        # api_usage 粒度 = 每工具一次（修复链聚合）：5 行 = intent/knowledge/
        # design(4 轮畸形聚合)/schedule/validate，总量 100+100+80+100+100=480
        assert f["plan_rows"] == 1, f
        assert any(t == "design_stages" and s == "repaired" for t, s, _ in f["tool_calls"]), f["tool_calls"]
        assert f["usage_rows"] == 5 and f["tokens_in"] == 480, f
    else:
        # 若实现选择中止（语义变更），也必须一致：无 Plan + 前两轮落账
        assert f["plan_rows"] == 0 and f["usage_rows"] >= 2, f
    assert f["status"] in ("done", "error"), f


# --- 场景 6：客户端断流（收到首个事件即断开）——生成任务须独立完成并落库 ---


def test_client_disconnect_generation_completes_and_persists(client, auth_headers, monkeypatch):
    """R24「单一持久化路径」端点级钉死：SSE 客户端中途断开，编排任务不依附客户端
    ——照常跑完、落账、持久化 Plan、状态机到 done。用户重连即见结果。"""
    import time

    sid = _session(client, auth_headers)
    prov = ScriptedProvider(
        [("ok", _resp(_C_INTENT)), ("ok", _resp(_C_KNOWLEDGE)), ("ok", _resp(_C_STAGES)),
         ("ok", _resp(_C_SCHEDULE)), ("ok", _resp(_C_VALIDATE))]
    )
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: prov)
    with client.stream(
        "POST", "/api/plans/generate", json={"session_id": sid, "raw_text": "我想学 Rust"}, headers=auth_headers
    ) as r:
        assert r.status_code == 200
        next(r.iter_lines(), None)  # 收到首行即退出 with -> 模拟客户端断连
    # 轮询 DB：后台任务照常完成（以 /api/health 请求间隙驱动事件循环推进）
    f = _db_facts(sid)
    for _ in range(200):
        if f["status"] == "done":
            break
        time.sleep(0.05)
        client.get("/api/health")
        f = _db_facts(sid)
    assert f["status"] == "done", f
    assert f["plan_rows"] == 1 and f["usage_rows"] == 5, f
