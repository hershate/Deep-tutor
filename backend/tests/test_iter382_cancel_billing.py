"""iter 382 R25：长连流家族取消计量守恒（对照 R23/R24 已钉死的三类流语义标准）。

缺口（本轮修复）：LLM 工具调用在途被取消（CancelledError 传播）时，已累积的部分
usage（修复链前几轮已烧 token）随协程局部变量丢弃——run_tool 层不捕 CancelledError
（Python 3.8+ 属 BaseException），api_usage 无行 = 绕过日预算守卫的不可观测成本黑洞
（iter 243 语义的取消路径缺口）。材料流另有「worker 已完成、主循环未消费」窗口
（断连恰在 result() 前）同丢。

修复（寄存器设计，不吞取消——编排器/前端依赖 cancelled 事件码）：
- ExecContext.orphan_billed 列表：loop._run_llm_tool 取消传播前寄存 build_metadata；
- plans._run_start 把 ctx.orphan_billed 挂到 stream（同一 list），_finalize 开头统一
  drain（cancelled/异常/守卫分支全覆盖）；
- 材料流：worker run_tool 完成即寄存，主循环消费后移除，finally 的清理线程统一落账。

测试：T1 单测级（loop 寄存 + CancelledError 照常传播）；T2 端到端（生成中 DELETE
会话触发取消 → _finalize drain 落部分计量，复用 iter 380 后台线程流模式）；T3 材料
流断连（agen.aclose = Starlette 客户端断连的内部机制）：unit1 挂起、unit2 完成——
主循环卡在等 task[0]，断连后 finally 取消 task[0]（无计量不写行）、drain task[1] 的
未消费计量（内容丢弃可重生成，token 不蒸发）。

其余长连面分派（clean，见 stage.md iter R25）：slides/quiz/kb 同步或短后台任务，
失败隔离已有、计费在调用完成点即时落账；quiz 后台对会话删除的 FK 冲突已留档
（R23：计费已先行落账，语义即「会话已不存在」）；前端材料流为裸 fetch 无自动重连
（非幂等端点无误重连双计费）。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from app.db import models
from app.db.base import SessionLocal
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

_BAD_INTENT = "not-json{"  # 触发校验失败 -> 修复重试（第二轮挂起被取消）
_UNIT_OK = json.dumps(
    {
        "objectives": ["学完后能够写出示例"],
        "prerequisites": ["无"],
        "estimated_time": "45 min",
        "content_md": "### 概述\n\n示例内容。",
    },
    ensure_ascii=False,
)


def _resp(content: str, tin: int = 100, tout: int = 50) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=tin, output_tokens=tout, model="claude-sonnet-5"))


class GatedProvider(LLMProvider):
    """脚本耗尽后阻塞在 asyncio.Event（等 task.cancel 打断——与生产硬取消同路径）。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.blocked = False

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        if self.outcomes:
            act, payload = self.outcomes.pop(0)
            if act == "ok":
                return payload
            raise payload
        self.blocked = True
        await asyncio.Event().wait()
        raise AssertionError("不应走到这")


def _usage_rows(uid: int) -> tuple[int, int, int]:
    db = SessionLocal()
    try:
        rows = db.query(models.ApiUsage).filter_by(user_id=uid).all()
        return len(rows), sum(r.tokens_in for r in rows), sum(r.tokens_out for r in rows)
    finally:
        db.close()


# --- T1：loop 层——取消传播前寄存部分计量 ---


def test_run_llm_tool_cancel_registers_orphan_billing():
    from app.agent.context import ExecContext
    from app.agent.loop import _run_llm_tool
    from app.tools import default_registry
    from app.tools.base import ToolInput

    prov = GatedProvider([("ok", _resp(_BAD_INTENT))])  # 第一轮畸形（accumulate 100/50）-> 第二轮挂起
    ctx = ExecContext(session_id="s", trace_id="t", registry=default_registry(), llm_provider=prov)
    tool = ctx.registry.get("understand_intent")

    inp = ToolInput(tool="understand_intent", session_id="s", args={"raw_text": "学 Rust", "parsed": [], "answers": []})

    async def scenario():
        task = asyncio.ensure_future(_run_llm_tool(tool, inp, ctx))
        for _ in range(500):
            if prov.blocked:
                break
            await asyncio.sleep(0.02)
        assert prov.blocked, "第二轮调用未进入在途"
        task.cancel()
        try:
            await task
            raise AssertionError("CancelledError 应照常传播（不吞——cancelled 事件码依赖）")
        except asyncio.CancelledError:
            pass
        # 取消前第一轮已烧的 100/50 token 寄存成功（黑洞补全）
        assert len(ctx.orphan_billed) == 1, ctx.orphan_billed
        m = ctx.orphan_billed[0]
        assert m.tokens_in == 100 and m.tokens_out == 50, (m.tokens_in, m.tokens_out)

    asyncio.run(scenario())


# --- T2：端到端——生成中删会话，取消路径的部分计量经 _finalize drain 落账 ---


def test_session_delete_mid_generation_bills_cancelled_usage(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db = SessionLocal()
    try:
        uid = db.get(models.Session, sid).user_id
    finally:
        db.close()

    prov = GatedProvider([("ok", _resp(_BAD_INTENT))])  # intent 畸形 -> knowledge 挂起
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: prov)

    box: dict = {}

    def _worker() -> None:
        try:
            with client.stream(
                "POST", "/api/plans/generate", json={"session_id": sid, "raw_text": "我想学 Rust"}, headers=auth_headers
            ) as r:
                box["status"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            box["error"] = repr(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and not prov.blocked:
        client.get("/api/health")
        time.sleep(0.05)
    assert prov.blocked, "第二工具调用未进入在途"

    r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    # 任务收敛（流对象 terminal 判据）
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        client.get("/api/health")
        s = plans_mod._STREAMS.get(sid)
        if s is None or s.terminal_id is not None:
            break
        time.sleep(0.05)
    t.join(timeout=25.0)
    assert not t.is_alive() and "error" not in box and box.get("status") == 200, box
    time.sleep(0.2)
    client.get("/api/health")

    db = SessionLocal()
    try:
        assert db.get(models.Session, sid) is None  # 删除生效
    finally:
        db.close()
    # 取消时 intent 修复链第一轮已烧的 100/50 落 api_usage（孤儿计量 drain，用户键幸存）
    rows, tin, tout = _usage_rows(uid)
    assert rows >= 1 and tin >= 100 and tout >= 50, (rows, tin, tout)


# --- T3：材料流断连——「已完成未消费」worker 的计量 drain（内容丢弃，token 不蒸发） ---


def test_materials_disconnect_bills_unconsumed_unit(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod
    from test_api_plans_materials import _make_plan

    plan_id = _make_plan(client, auth_headers)
    db = SessionLocal()
    try:
        plan = db.get(models.Plan, plan_id)
        session_id = plan.session_id
        uid = db.get(models.Session, session_id).user_id
        content = plans_mod._load_plan_content(plan)
    finally:
        db.close()
    base = _usage_rows(uid)  # plan 生成（_make_plan）已落的基线，断言只看材料流增量

    class OrderGateProvider(LLMProvider):
        """order=1 挂起（主循环将卡在等 task[0]）；order=2 正常返回（完成未消费窗口）。"""

        blocked = False

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            if '"order": 2' in user or '"order":2' in user:
                return _resp(_UNIT_OK)
            self.blocked = True
            await asyncio.Event().wait()
            raise AssertionError("不应走到这")

    prov = OrderGateProvider()
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: prov)

    async def _no_search(query, ctx, limit=3):
        return []

    monkeypatch.setattr(plans_mod, "search_unit_resources", _no_search)
    monkeypatch.setattr(plans_mod, "_HEARTBEAT_SECONDS", 0.2)  # 加速 keepalive yield（aclose 注入点）

    from app.api.plans import MaterialsIn, UnitPlan

    body = MaterialsIn(depth="full", refresh=True)
    plans = [
        UnitPlan(title="u1", stage={}, stage_index=None, nodes=[], node_sids=[]),
        UnitPlan(title="u2", stage={}, stage_index=None, nodes=[], node_sids=[]),
    ]

    async def scenario():
        agen = plans_mod._materials_sse(plan_id, session_id, content, body, [], uid, plans)
        # unit_start ×2 前置发出（async generator 挂在 yield 处不自行推进——两次 anext
        # 后 ensure_future 尚未执行到，须第三次驱动到主循环的 keepalive yield，worker
        # 才被创建并在事件循环上跑）
        await agen.__anext__()
        await agen.__anext__()
        keepalive = await asyncio.wait_for(agen.__anext__(), 5.0)
        assert keepalive.startswith(":")  # 心跳注释帧（u1 挂起 -> 主循环等 task[0]）
        for _ in range(500):
            if prov.blocked:
                break
            await asyncio.sleep(0.02)
        assert prov.blocked
        # 客户端断连（Starlette 内部即 aclose）-> GeneratorExit 于 keepalive yield
        await agen.aclose()

    asyncio.run(scenario())
    time.sleep(0.2)

    db = SessionLocal()
    try:
        unit_rows = db.query(models.StudyUnit).filter_by(plan_id=plan_id).count()
    finally:
        db.close()
    # 语义：unit2 内容丢弃（主循环未消费不落库，可重生成），但其 100/50 token 经
    # finally 的孤儿计量 drain 落 api_usage；unit1 挂起取消无计量不写行（只看增量）
    rows, tin, tout = _usage_rows(uid)
    assert (rows - base[0], tin - base[1], tout - base[2]) == (1, 100, 50), (base, (rows, tin, tout))
    assert unit_rows == 0, "未消费单元不应落库（内容丢弃）"
