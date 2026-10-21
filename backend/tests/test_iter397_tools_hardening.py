"""iter 397-B6：app/tools 面加固回归（T1 / T2 / T3 / T4 / T5 / T6 / T7 / T8 / T9 / T10 / T11 / T12 / T13 / T15）。

- **T1（high）**：资源筛选的 LLM 用量**只记最后一次尝试**（`last_usage = resp.usage`
  覆盖、异常分支再清零）——重试中已消耗的 token 完全不落 api_usage、不进会话预算，
  「第 1 次成功但不可解析 + 第 2 次超时」的组合下 out.metadata 整个不赋值 ->
  用户反复触发即可绕过 check_daily_budget。且 build_metadata 漏传 model 与
  ctx.model_pricing -> 非 Anthropic 模型筛选成本记 0。
- **T2（high）**：Agent 工具 memory_save 的「计数 -> 插入」**未持配额锁**，与 REST
  /memories 两条路径对同一配额无互斥 -> 并发下每用户记忆数可突破 2000 条上限。
- **T3（medium，用户裁决）**：promote_artifact 的 schema 承诺 target_kind 可为
  "material"，approve 端点只支持 note/plan -> 选 material 必 400 且状态回滚复原
  （反复点批准永远进不去）。改为从枚举移除并在工具侧给明确提示。
- **T4（medium）**：工具层 DB 写在**默认线程池**里做（写锁等待 10s 会占住默认池
  线程，把同池的文件 IO 槽位一起饿死）-> 改走专用写线程池。
- **T5（medium）**：Agent 联网工具（web_search / web_fetch）**无任何出站速率限制**，
  单 run 最多 25 步可连打外呼；web_fetch 还会白下载 10MB（回灌只取 2 万字符）。
- **T6（medium）**：export_plan 在 ctx.data_dir 缺失时回落到相对路径 "data"（按
  进程 CWD 解析，落到管辖目录之外，且 containment 守卫恒真）；编排器上下文正是
  缺 data_dir 的那个 -> 生产路径也在往目录外写文件。写入亦非原子。
- **T7（medium）**：verify_url 的 stage_level 参数逐候选传入却**从未被读取**，而对外
  描述承诺「内容难度适配」-> 表达与实现失真。
- **T8（medium）**：Bing 网页版检索用 follow_redirects=True，只校验**终态** host ->
  违反本仓「重定向逐跳复检 SSRF」不变量（中间跳由外部可控）。
- **T9（low）**：SSRF 判定有两份独立实现（verify_url._is_safe_url 与
  core/urlguard.check_outbound_url），后者自称「单一事实源」-> 收敛为薄封装。
- **T10（low）**：schedule_plan 实际读 upstream.understand_intent 却未声明
  upstream_reads -> 并行组安全断言对它完全失效。
- **T11（low）**：schedule_plan 的 prompt 承诺「日期从 args.start_date 开始」，实现
  **只读 upstream** -> 调用方传的 args.start_date 被静默忽略。
- **T12（low）**：workspace.write_file 写失败时 tmp 残留（会被 list_files 列出、被
  commit_run_workspace 当作正式产物登记）。
- **T13（low）**：memory_save 在 scope=thread 但无 thread_id 时写出
  `thread_id=NULL + scope="thread"` 的矛盾行。
- **T15（low）**：build_plan_slides 把 LLM 抽取的 intent.level/goal 未转义拼进
  content_html（当前被渲染层白名单净化兜住，属纵深防御缺口）。
"""

from __future__ import annotations

import asyncio
import ast
import json
import types
from pathlib import Path

import pytest

from app.agent.context import ExecContext
from app.db import models
from app.db.base import SessionLocal
from app.llm.base import LLMUsage
from app.tools import agent_tools as A
from app.tools import search_resources as sr_mod
from app.tools.base import ToolInput


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------


def _setup():
    db = SessionLocal()
    u = models.User(name="u", username="b6user", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(
        thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return db, u, t, r


def _ctx(db, u, t, r) -> ExecContext:
    ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id)
    ctx.thread_id = t.id
    ctx.run_id = r.id
    return ctx


def _inp(tool: str, **args) -> ToolInput:
    return ToolInput(tool=tool, session_id="1", args=args)


class _FakeProvider:
    """最小 provider 替身：按序返回 (content, usage) 或抛异常。"""

    kind = "anthropic"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, system, user, max_tokens=0, cache_system=False):  # noqa: ARG002
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        content, usage = item
        return types.SimpleNamespace(content=content, usage=usage)


def _usage(tin: int = 100, tout: int = 50, model: str = "m1") -> LLMUsage:
    return LLMUsage(input_tokens=tin, output_tokens=tout, model=model)


# ---------------------------------------------------------------------------
# T1：筛选用量累加 + 失败路径仍计量 + build_metadata 带 model/计价表
# ---------------------------------------------------------------------------


def test_screen_usage_accumulates_across_attempts():
    """T1：第 1 次成功但不可解析 + 第 2 次成功 -> 两次用量**累加**（此前只记最后一次）。"""
    ctx = ExecContext(session_id="1", trace_id="tr")
    ctx.llm_provider = _FakeProvider(
        [
            ("这不是 JSON", _usage(100, 50)),
            (json.dumps([{"url": "https://ex.com/a", "suitable": True, "label": "ok"}]), _usage(200, 70)),
        ]
    )
    resources = [{"stage_index": 0, "title": "T", "url": "https://ex.com/a", "verified": "uncertain"}]
    out = asyncio.run(sr_mod._screen_resources(resources, _inp("search_resources"), ctx))
    screened, usage, warn = out
    assert warn is None
    assert len(screened) == 1
    assert usage is not None
    assert usage.input_tokens == 300, f"两次调用未累加：{usage}"
    assert usage.output_tokens == 120, f"两次调用未累加：{usage}"


def test_screen_usage_survives_total_failure():
    """T1：重试耗尽（全不可解析）-> 仍返回已消耗的 usage（此前异常分支清零 -> 完全失明）。"""
    ctx = ExecContext(session_id="1", trace_id="tr")
    ctx.llm_provider = _FakeProvider([("坏输出", _usage(111, 22))])
    resources = [{"stage_index": 0, "title": "T", "url": "https://ex.com/a", "verified": "uncertain"}]
    screened, usage, warn = asyncio.run(
        sr_mod._screen_resources(resources, _inp("search_resources"), ctx)
    )
    assert screened == []
    assert warn is not None
    assert usage is not None, "筛选失败时 usage 被清零 -> 成本黑洞"
    assert usage.input_tokens == 111 * sr_mod._SCREEN_MAX_ATTEMPTS
    assert usage.output_tokens == 22 * sr_mod._SCREEN_MAX_ATTEMPTS


def test_search_resources_metadata_carries_model_and_pricing(monkeypatch):
    """T1：out.metadata 必须带 model 与 ctx.model_pricing（此前两者皆缺 -> 成本记 0）。"""
    monkeypatch.setattr(sr_mod, "effective_search_engine", lambda key: "bing_web")

    async def _fake_search(query, engine, count=5, key=""):  # noqa: ARG001
        return [{"title": "候选", "url": "https://ex.com/a"}]

    async def _fake_verify(items, session_id, ctx):  # noqa: ARG001
        return {"https://ex.com/a": {"label": "ok", "final_url": "https://ex.com/a", "snippet": "正文"}}

    monkeypatch.setattr(sr_mod, "_search_web", _fake_search)
    monkeypatch.setattr(sr_mod, "_verify_candidates", _fake_verify)

    ctx = ExecContext(session_id="1", trace_id="tr")
    ctx.llm_provider = _FakeProvider(
        [(json.dumps([{"url": "https://ex.com/a", "suitable": True, "label": "ok"}]), _usage(10, 5, "m-x"))]
    )
    ctx.model_pricing = {
        "m-x": {
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_read_per_mtok": 0.0,
            "cache_write_per_mtok": 0.0,
        }
    }
    inp = ToolInput(
        tool="search_resources",
        session_id="1",
        args={},
        upstream={
            "understand_intent": {"data": {"topic": "X", "level": "beginner"}},
            "design_stages": {"data": {"stages": [{"index": 0, "name": "S1", "topics": [{"name": "t1"}]}]}},
        },
    )
    out = asyncio.run(sr_mod.search_resources(inp, ctx))
    assert out.status == "ok", out.errors
    assert out.metadata is not None
    assert out.metadata.model == "m-x", out.metadata
    assert out.metadata.tokens_in == 10 and out.metadata.tokens_out == 5
    assert out.metadata.cost > 0, "计价表未生效 -> 成本记 0"


# ---------------------------------------------------------------------------
# T2：Agent 记忆写入与 REST 路径共用同一把配额锁
# ---------------------------------------------------------------------------


def test_agent_memory_save_holds_shared_quota_lock(monkeypatch):
    """T2：memory_save 必须在 quota_slot('memory:{uid}') 内做「计数 + 插入」（与 REST 同键）。"""
    import app.core.quota as quota_mod

    seen: list[str] = []
    real_slot = quota_mod.quota_slot

    def _spy(key):
        seen.append(key)
        return real_slot(key)

    monkeypatch.setattr(quota_mod, "quota_slot", _spy)

    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    out = asyncio.run(A.memory_save(_inp("memory_save", content="要点", kind="fact"), ctx))
    assert out.status == "ok", out.errors
    assert f"memory:{u.id}" in seen, f"未持 REST 同款配额锁：{seen}"
    db.close()


def test_agent_memory_save_rejects_thread_scope_without_thread_id():
    """T13：scope=thread 但无 thread_id -> 明确报错（此前写出 thread_id=NULL 的矛盾行）。"""
    db, u, _t, _r = _setup()
    ctx = ExecContext(session_id="1", trace_id="tr", db=db, user_id=u.id)
    out = asyncio.run(A.memory_save(_inp("memory_save", content="要点"), ctx))
    assert out.status == "error"
    assert out.errors[0].code == "no_db_context"
    db.close()


# ---------------------------------------------------------------------------
# T4：工具层 DB 写走专用写线程池
# ---------------------------------------------------------------------------


def test_agent_tool_writes_use_dedicated_write_pool(monkeypatch):
    """T4：memory_save / memory_delete 的库写走 dbwrite.run_db_write（写锁等待不占默认池槽位）。"""
    import app.core.dbwrite as dbwrite_mod

    calls: list[str] = []
    real = dbwrite_mod.run_db_write

    async def _spy(fn, *args):
        calls.append(getattr(fn, "__name__", "?"))
        return await real(fn, *args)

    monkeypatch.setattr(dbwrite_mod, "run_db_write", _spy)

    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    saved = asyncio.run(A.memory_save(_inp("memory_save", content="要点"), ctx))
    assert saved.status == "ok", saved.errors
    assert calls, "未走专用写线程池"
    deleted = asyncio.run(A.memory_delete(_inp("memory_delete", id=saved.data["id"]), ctx))
    assert deleted.status == "ok", deleted.errors
    assert len(calls) >= 2
    db.close()


# ---------------------------------------------------------------------------
# T5：Agent 联网工具的出站限流与体积上限
# ---------------------------------------------------------------------------


def test_agent_web_fetch_is_rate_limited(monkeypatch):
    """T5：web_fetch / web_search 入口必须有限流（此前 Agent 联网工具完全无限流）。"""
    import app.core.ratelimit as rl_mod

    seen: list[str] = []

    def _deny(key, limit):  # noqa: ARG001
        seen.append(key)
        return False

    monkeypatch.setattr(rl_mod.limiter, "check", _deny)

    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    out = asyncio.run(A.web_fetch(_inp("web_fetch", url="https://ex.com/a"), ctx))
    assert out.status == "error"
    assert out.errors[0].code == "rate_limited"
    assert seen and seen[0].startswith("agent_fetch:"), seen

    out2 = asyncio.run(A.web_search(_inp("web_search", query="x"), ctx))
    assert out2.status == "error" and out2.errors[0].code == "rate_limited"
    assert any(k.startswith("agent_search:") for k in seen), seen
    db.close()


def test_agent_web_fetch_uses_reduced_byte_cap(monkeypatch):
    """T5：web_fetch 的抓取体积上限收敛（回灌只取 2 万字符，10MB 中间态纯浪费）。"""
    import app.tools.parse_material as pm_mod

    seen: list[int] = []

    async def _fake_parse_url(url, max_bytes=0):  # noqa: ARG001
        seen.append(max_bytes)
        return "正文", {"final_url": url, "title": "t", "chars": 2}

    monkeypatch.setattr(pm_mod, "_parse_url", _fake_parse_url)

    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    out = asyncio.run(A.web_fetch(_inp("web_fetch", url="https://ex.com/a"), ctx))
    assert out.status == "ok", out.errors
    assert seen and seen[0] <= 1024 * 1024, f"抓取上限未收敛：{seen}"
    db.close()


# ---------------------------------------------------------------------------
# T6：export_plan 不落盘到管辖目录之外
# ---------------------------------------------------------------------------


def test_export_plan_refuses_missing_data_dir(tmp_path, monkeypatch):
    """T6：ctx.data_dir 缺失 -> 明确报错，且不向 CWD 相对路径落盘。"""
    from app.tools import export_plan as ep_mod

    monkeypatch.chdir(tmp_path)
    ctx = ExecContext(session_id="1", trace_id="tr")  # 无 data_dir
    out = asyncio.run(ep_mod.export_plan(_inp("export_plan", plan={"intent": {"topic": "T"}}), ctx))
    assert out.status == "error"
    assert out.errors[0].code == "no_data_dir"
    assert not (tmp_path / "data").exists(), "仍向管辖目录之外落盘"


def test_export_plan_writes_into_managed_dir(tmp_path):
    """T6：给了 data_dir -> 文件落在 <data_dir>/sessions/<sid>/exports/ 内。"""
    from app.tools import export_plan as ep_mod

    ctx = ExecContext(session_id="77", trace_id="tr", data_dir=str(tmp_path))
    inp = ToolInput(
        tool="export_plan", session_id="77", args={"plan_id": "9", "plan": {"intent": {"topic": "T"}}}
    )
    out = asyncio.run(ep_mod.export_plan(inp, ctx))
    assert out.status == "ok", out.errors
    written = tmp_path / "sessions" / "77" / "exports" / "9.md"
    assert written.is_file()
    assert "T" in written.read_text(encoding="utf-8")


def test_orchestrator_context_declares_data_dir():
    """T6：编排器（方案生成 PIPELINE）上下文必须带 data_dir——export_plan 靠它落盘。"""
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "plans.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "ExecContext":
            continue
        kw = {k.arg for k in node.keywords if k.arg}
        if "trace_id" in kw and "budget" in kw:  # 编排器上下文（stream.trace_id）
            assert "data_dir" in kw, "编排器 ExecContext 缺 data_dir -> export_plan 落盘到目录外"
            found = True
    assert found, "未定位到编排器 ExecContext（源码结构变化？）"


# ---------------------------------------------------------------------------
# T3：material 提升死胡同
# ---------------------------------------------------------------------------


def test_promote_artifact_rejects_material():
    """T3：target_kind=material -> 明确错误（approve 端点不支持，schema 已移除该值）。"""
    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    out = asyncio.run(A.promote_artifact(_inp("promote_artifact", name="x.md", target_kind="material"), ctx))
    assert out.status == "error"
    assert out.errors[0].code == "invalid_enum"
    assert "note" in out.errors[0].message
    db.close()


def test_promote_artifact_schema_has_no_material():
    """T3：广告给模型的 schema 枚举不得再含 material（避免「承诺 -> 必 400」死胡同）。"""
    from app.tools.agent_tools import AGENT_TOOL_SCHEMAS

    enum = AGENT_TOOL_SCHEMAS["promote_artifact"]["properties"]["target_kind"]["enum"]
    assert "material" not in enum, enum
    assert set(enum) == {"plan", "note"}


# ---------------------------------------------------------------------------
# T7：verify_url 描述与 stage_level 死参数
# ---------------------------------------------------------------------------


def test_verify_url_description_matches_behavior():
    """T7：对外描述不得再承诺「内容难度适配」（verify_url 只判可达性）。"""
    from app.tools import TOOL_DESCRIPTIONS

    desc = TOOL_DESCRIPTIONS["verify_url"]
    assert "可达性" in desc
    assert "难度适配" not in desc, desc


def test_verify_candidates_does_not_pass_dead_stage_level():
    """T7：verify_url 的入参不再携带从未被读取的 stage_level（只查代码，不查注释）。"""
    import inspect

    src = inspect.getsource(sr_mod._verify_candidates)
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert '"stage_level"' not in code
    assert '"stage_level":' not in code


# ---------------------------------------------------------------------------
# T8：Bing 网页版检索的重定向逐跳复检
# ---------------------------------------------------------------------------


def test_bing_web_search_rejects_foreign_redirect(monkeypatch):
    """T8：中间跳指向非 Bing 域 -> 立即拒绝（此前 follow_redirects=True 只查终态 host）。"""

    class _Resp:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        url = "https://cn.bing.com/search"

        def raise_for_status(self):  # pragma: no cover - 不应走到
            raise AssertionError("重定向响应不应进入终态处理")

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, **kw):
            self.kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **kw):  # noqa: ARG002
            return _Stream()

    import httpx as real_httpx

    monkeypatch.setattr(
        sr_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=_Client, Timeout=real_httpx.Timeout),
    )
    with pytest.raises(ValueError, match="重定向到非 Bing 域"):
        asyncio.run(sr_mod._bing_web_search("query"))


def test_bing_web_search_client_does_not_follow_redirects(monkeypatch):
    """T8：客户端不得开启自动重定向（逐跳复检的前提）。"""
    captured: dict = {}

    class _Client:
        def __init__(self, **kw):
            captured.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **kw):  # noqa: ARG002
            raise ValueError("stop")

    import httpx as real_httpx

    monkeypatch.setattr(
        sr_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=_Client, Timeout=real_httpx.Timeout),
    )
    with pytest.raises(ValueError):
        asyncio.run(sr_mod._bing_web_search("query"))
    assert captured.get("follow_redirects") is False


# ---------------------------------------------------------------------------
# T9：SSRF 判定收敛到单一事实源
# ---------------------------------------------------------------------------


def test_is_safe_url_delegates_to_urlguard(monkeypatch):
    """T9：_is_safe_url 是 urlguard.check_outbound_url 的薄封装（消除双实现漂移）。"""
    from app.tools import verify_url as vu

    seen: list[tuple] = []

    def _fake(url, *, resolve=True):
        seen.append((url, resolve))
        return False, "mocked"

    monkeypatch.setattr(vu, "check_outbound_url", _fake)
    ok, reason = vu._is_safe_url("https://example.com/x")
    assert ok is False and reason == "mocked"
    assert seen == [("https://example.com/x", True)]


def test_is_safe_url_blocks_private_and_metadata():
    """T9（行为护栏）：私网 / 云元数据 / 非 http(s) 仍被拒绝。"""
    from app.tools.verify_url import _is_safe_url

    assert _is_safe_url("http://127.0.0.1:8080/x")[0] is False
    assert _is_safe_url("http://169.254.169.254/latest")[0] is False
    assert _is_safe_url("file:///etc/passwd")[0] is False


# ---------------------------------------------------------------------------
# T10 / T11：schedule_plan 的上游声明与 start_date 优先级
# ---------------------------------------------------------------------------


def test_schedule_plan_declares_upstream_reads():
    """T10：schedule_plan 必须声明 upstream_reads（并行组安全断言的事实源）。"""
    from app.tools.schedule_plan import schedule_plan_tool

    assert "understand_intent" in schedule_plan_tool.upstream_reads


def test_schedule_plan_honors_args_start_date():
    """T11：args.start_date 优先于 upstream（prompt 明确承诺的优先级）。"""
    from app.tools.schedule_plan import check_schedule

    inp = ToolInput(
        tool="schedule_plan",
        session_id="1",
        args={"start_date": "2026-03-01"},
        upstream={"understand_intent": {"data": {"start_date": "2026-05-05"}}},
    )
    data = check_schedule({"schedule": [{"date": "?", "tasks": ["x"], "est_minutes": 10}]}, inp)
    assert data["start_date"] == "2026-03-01"
    assert data["schedule"][0]["date"] == "2026-03-01"


def test_schedule_plan_falls_back_to_upstream_start_date():
    """T11：args 无 start_date 时回退 upstream（既有行为不回归）。"""
    from app.tools.schedule_plan import check_schedule

    inp = ToolInput(
        tool="schedule_plan",
        session_id="1",
        args={},
        upstream={"understand_intent": {"data": {"start_date": "2026-05-05"}}},
    )
    data = check_schedule({"schedule": [{"date": "?", "tasks": ["x"], "est_minutes": 10}]}, inp)
    assert data["start_date"] == "2026-05-05"


# ---------------------------------------------------------------------------
# T12：工作区原子写失败清理 tmp
# ---------------------------------------------------------------------------


def test_workspace_write_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """T12：写失败时 tmp 必须清理（残留会被当成正式产物登记）。"""
    from app.agent import workspace as ws

    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    run_id = 424242

    def _boom(*a, **k):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr(ws.os, "replace", _boom)
    with pytest.raises(OSError):
        ws.write_file(run_id, "a.md", "hello")
    scratch = ws.scratch_dir(run_id)
    leftovers = [p.name for p in scratch.glob("*.tmp")] + [p.name for p in scratch.glob(".*.tmp")]
    assert not leftovers, f"tmp 残留：{leftovers}"


# ---------------------------------------------------------------------------
# T15：幻灯片标题页的 intent 文本转义
# ---------------------------------------------------------------------------


def test_slides_escape_intent_level_and_goal():
    """T15：intent.level / goal 拼进 content_html 前必须转义。"""
    from app.tools.build_plan_slides import default_spec_from_plan

    spec = default_spec_from_plan({"intent": {"topic": "T", "goal": "<script>alert(1)</script>"}})
    html = spec.slides[0].content_html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
