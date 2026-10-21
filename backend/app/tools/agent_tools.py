"""Agent 工具集（iter 345 记忆系统）—— Agent 循环可调用的工具。

注册到 ``default_registry()``，与核心工具同构（经 ``run_tool`` 调用，返回 ToolOutput）。
两类来源：
- 工作区/记忆/历史/提升：本模块实现（需求③ Claude Code 式文件 + 记忆 CRUD + 草稿提升）；
- 联网：``web_search`` 复用 search_resources 双引擎、``web_fetch`` 复用 parse_material._parse_url
  （含 SSRF 防护 _is_safe_url + trafilatura 正文提取），不重复实现。

工具须在 agent run 上下文内调用（ctx.run_id/thread_id/user_id/db）；脱离 run 调用 -> error 信封。
工具执行器（phase G 装配）把 ToolOutput 转 AgentToolResult 回灌模型。
"""

from __future__ import annotations

import asyncio
import logging

from app.agent import workspace as ws
from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.tools.base import ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.agent_tools")


def _ok(tool: str, data: dict) -> ToolOutput:
    return ToolOutput(tool=tool, status="ok", data=data)


def _err(tool: str, code: str, message: str) -> ToolOutput:
    from app.tools.base import ToolError

    return ToolOutput(tool=tool, status="error", errors=[ToolError(code=code, message=message)])


def _need_run(ctx: ExecContext, tool: str) -> ToolOutput | None:
    if not ctx.run_id:
        return _err(tool, "no_run_context", "此工具须在 agent run 内调用（ctx.run_id 缺失）")
    return None


def _need_db(ctx: ExecContext, tool: str) -> ToolOutput | None:
    if not ctx.db or not ctx.user_id:
        return _err(tool, "no_db_context", "此工具须在带 DB 会话的 run 内调用（ctx.db/user_id 缺失）")
    return None


# iter 369：memory_save 与 REST /memories（app/api/memory.py）同口径的配额常量
_MAX_MEMORIES_PER_USER = 2000
_MAX_MEMORY_CHARS = 20000

# iter 397（T5）：Agent 联网工具的出站速率上限（每用户每分钟，检索与抓取各一个桶）
_AGENT_NET_RATE_PER_MIN = 30
# iter 397（T5）：Agent 抓取的体积上限（回灌模型的正文最终被 2 万字符截断，
# 默认 10MB 上限的中间态纯属浪费——单 run 25 步可白下载 250MB）
_AGENT_FETCH_MAX_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# 工作区文件工具（沙箱内，原子写）
# ---------------------------------------------------------------------------


async def workspace_write(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "workspace_write"
    if e := _need_run(ctx, tool):
        return e
    name = str(inp.args.get("name", "")).strip()
    content = str(inp.args.get("content", ""))
    if not name:
        return _err(tool, "empty_required", "name 必填")
    try:
        # iter 369：文件 IO 移 to_thread（async 壳同步体此前直接跑在事件循环上）
        rel = await asyncio.to_thread(ws.write_file, ctx.run_id, name, content)
    except ValueError as exc:
        return _err(tool, "invalid_path", str(exc))
    return _ok(tool, {"name": rel, "bytes": len(content.encode("utf-8"))})


async def workspace_read(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "workspace_read"
    if e := _need_run(ctx, tool):
        return e
    name = str(inp.args.get("name", "")).strip()
    if not name:
        return _err(tool, "empty_required", "name 必填")
    try:
        content = await asyncio.to_thread(ws.read_file, ctx.run_id, name)
    except FileNotFoundError:
        return _err(tool, "not_found", f"工作区无此文件: {name}")
    except ValueError as exc:
        return _err(tool, "invalid_path", str(exc))
    return _ok(tool, {"name": name, "content": content, "bytes": len(content.encode("utf-8"))})


async def workspace_edit(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "workspace_edit"
    if e := _need_run(ctx, tool):
        return e
    name = str(inp.args.get("name", "")).strip()
    old = str(inp.args.get("old", ""))
    new = str(inp.args.get("new", ""))
    replace_all = bool(inp.args.get("replace_all", False))
    if not name:
        return _err(tool, "empty_required", "name 必填")
    try:
        n = await asyncio.to_thread(ws.edit_file, ctx.run_id, name, old, new, replace_all=replace_all)
    except FileNotFoundError:
        return _err(tool, "not_found", f"工作区无此文件: {name}")
    except ValueError as exc:
        return _err(tool, "edit_failed", str(exc))
    return _ok(tool, {"name": name, "replacements": n})


async def workspace_list(inp: ToolInput, ctx: ExecContext) -> ToolOutput:  # noqa: ARG001
    tool = "workspace_list"
    if e := _need_run(ctx, tool):
        return e
    return _ok(tool, {"files": await asyncio.to_thread(ws.list_files, ctx.run_id)})


# ---------------------------------------------------------------------------
# 联网（复用既有实现，不重复）
# ---------------------------------------------------------------------------


async def web_search(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "web_search"
    query = str(inp.args.get("query", "")).strip()
    if not query:
        return _err(tool, "empty_required", "query 必填")
    # iter 397（T5）：Agent 联网工具此前**无任何出站速率限制**——模型可在一次 run 内
    # 连打 25 步（MAX_AGENT_STEPS），既放大人为/注入驱动的外呼量，也让 Bing 引擎
    # 侧把本实例判为爬虫。此处按用户限流（与材料 URL 抓取同族，键不同）。
    from app.core.ratelimit import limiter

    if not limiter.check(f"agent_search:{ctx.user_id}", _AGENT_NET_RATE_PER_MIN):
        return _err(
            tool,
            "rate_limited",
            f"检索过于频繁（{_AGENT_NET_RATE_PER_MIN}/分钟），请稍后再试",
        )
    try:
        count = int(inp.args.get("count", 8))
    except (TypeError, ValueError):
        count = 8
    count = max(1, min(count, 10))
    from app.tools.search_resources import search_unit_resources

    try:
        results = await search_unit_resources(query, ctx, limit=count)
    except Exception as exc:  # noqa: BLE001  检索失败不崩，返 error 信封
        log.warning("agent web_search 失败 q=%s: %s", query[:80], exc)
        return _err(tool, "search_failed", str(exc))
    items = [{"title": r.get("title", ""), "url": r.get("url", ""), "verified": r.get("verified", "")} for r in results]
    return _ok(tool, {"results": items, "count": len(items)})


async def web_fetch(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "web_fetch"
    url = str(inp.args.get("url", "")).strip()
    if not url:
        return _err(tool, "empty_required", "url 必填")
    # iter 397（T5）：出站限流（此前 Agent 联网工具无任何速率限制，单 run 最多可
    # 触发 25 次外呼；材料 URL 抓取路径有限流而 Agent 路径无限，口径不一致）。
    from app.core.ratelimit import limiter

    if not limiter.check(f"agent_fetch:{ctx.user_id}", _AGENT_NET_RATE_PER_MIN):
        return _err(
            tool,
            "rate_limited",
            f"抓取过于频繁（{_AGENT_NET_RATE_PER_MIN}/分钟），请稍后再试",
        )
    from app.tools.parse_material import _parse_url

    try:
        # iter 397（T5）：抓取体积上限收敛到 _AGENT_FETCH_MAX_BYTES——回灌给模型的
        # 正文最终被 _MAX_TOOL_RESULT_CHARS(2 万字符) 截断，而默认上限 10MB 的
        # 中间态纯属浪费（单 run 最多 25 次 -> 250MB 白下载）。
        text, meta = await _parse_url(url, max_bytes=_AGENT_FETCH_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001  SSRF/超时/超大 -> error 信封（消息经服务器清洗）
        log.warning("agent web_fetch 失败 url=%s: %s", url[:80], exc)
        return _err(tool, "fetch_failed", str(exc) if "Data" not in str(exc) else "抓取失败")
    # 引用头（供模型 grounding 引用）
    source = meta.get("final_url") or url
    return _ok(
        tool, {"url": source, "title": meta.get("title", ""), "text": text, "chars": meta.get("chars", len(text))}
    )


# ---------------------------------------------------------------------------
# 记忆 / 历史（按需加载——决策②）
# ---------------------------------------------------------------------------


async def memory_save(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "memory_save"
    if e := _need_db(ctx, tool):
        return e
    content = str(inp.args.get("content", ""))
    if not content.strip():
        return _err(tool, "empty_required", "content 必填")
    # iter 369：与 REST /memories 同口径的配额族——模型自写路径此前不设防，长跑 run
    # 可把 memories 表刷爆（iter 365 为防刷库加的配额对 agent 路径失效）
    # 顺序对齐 REST（Pydantic 先验长度不耗限流额度，再限流，最后查配额）
    if len(content) > _MAX_MEMORY_CHARS:
        return _err(tool, "too_long", f"content 超 {_MAX_MEMORY_CHARS} 字符上限（与 /memories 同口径，请精简后重存）")
    from app.core.ratelimit import limiter

    if not limiter.check(f"memory_write:{ctx.user_id}", 60):
        return _err(tool, "rate_limited", "记忆写入过于频繁（60/分钟，与 /memories 同口径）")
    kind = str(inp.args.get("kind", "note") or "note")[:64]
    scope = str(inp.args.get("scope", "thread") or "thread")
    if scope not in ("thread", "user"):
        scope = "thread"
    # iter 397（T13）：scope=thread 但 ctx.thread_id 缺失时，写出的行是
    # `thread_id=NULL` + `scope="thread"`——thread 视图查不到（按 thread_id 过滤），
    # 却出现在 user 视图（thread_id IS NULL）里，归属自相矛盾。此时明确报错。
    if scope == "thread" and not ctx.thread_id:
        return _err(tool, "no_db_context", "scope=thread 须在带 thread_id 的 run 内调用")
    from app.db.models import Memory

    def _save():
        # 计数+插入+提交一次线程内完成（commit 移出事件循环防写锁争用冻结事件循环）
        #
        # iter 397（T2）：与 REST /memories **同一把配额锁**（键 `memory:{uid}`）。
        # 此前 Agent 路径「计数 -> 插入」无互斥：并发 run（或 Agent 与 REST 并发）
        # 可同时通过 count 检查 -> 每用户记忆数按并发数突破 2000 条上限。
        # 锁在**写线程内**持有（threading.Lock 不可跨 await 持有，见 quota.py 说明）。
        from app.core.quota import quota_slot

        with quota_slot(f"memory:{ctx.user_id}"):
            if ctx.db.query(Memory).filter_by(user_id=ctx.user_id).count() >= _MAX_MEMORIES_PER_USER:
                return None
            m = Memory(
                user_id=ctx.user_id,
                thread_id=ctx.thread_id if (scope == "thread" and ctx.thread_id) else None,
                scope=scope,
                kind=kind,
                content=content,
            )
            ctx.db.add(m)
            ctx.db.commit()
            ctx.db.refresh(m)
            return m

    # iter 397（T4）：走**专用写线程池**（dbwrite.run_db_write）而非 asyncio 默认线程池
    # ——SQLite 写锁等待（busy_timeout 10s）会占住默认池线程，多用户并发下把同池的
    # 文件 IO 槽位一起饿死（dbwrite.py 的既有设计意图）。
    from app.core.dbwrite import run_db_write

    m = await run_db_write(_save)
    if m is None:
        return _err(tool, "quota_exceeded", f"记忆数达上限 {_MAX_MEMORIES_PER_USER} 条/用户（与 /memories 同口径）")
    return _ok(tool, {"id": m.id, "scope": scope})


async def memory_recall(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "memory_recall"
    if e := _need_db(ctx, tool):
        return e
    q = str(inp.args.get("q", "")).strip()
    scope = inp.args.get("scope")
    try:
        limit = int(inp.args.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    from app.db.models import Memory

    qry = ctx.db.query(Memory).filter(Memory.user_id == ctx.user_id)
    if scope == "thread" and ctx.thread_id:
        qry = qry.filter(Memory.thread_id == ctx.thread_id)
    elif scope == "user":
        qry = qry.filter(Memory.thread_id.is_(None))
    if q:
        qry = qry.filter(Memory.content.contains(q))
    rows = qry.order_by(Memory.updated_at.desc()).limit(limit).all()
    return _ok(
        tool,
        {
            "memories": [{"id": r.id, "kind": r.kind, "scope": r.scope, "content": r.content[:1000]} for r in rows],
            "count": len(rows),
        },
    )


async def memory_delete(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "memory_delete"
    if e := _need_db(ctx, tool):
        return e
    mid = inp.args.get("id")
    if not mid:
        return _err(tool, "empty_required", "id 必填")
    from app.db.models import Memory

    def _delete() -> bool:
        m = ctx.db.query(Memory).filter_by(id=mid, user_id=ctx.user_id).first()
        if m is None:
            return False
        ctx.db.delete(m)
        ctx.db.commit()
        return True

    # iter 397（T4）：走专用写线程池（写锁等待不占默认池的文件 IO 槽位）
    from app.core.dbwrite import run_db_write

    if not await run_db_write(_delete):  # iter 369：commit 移出事件循环
        return _err(tool, "not_found", "记忆不存在或无权访问")
    return _ok(tool, {"deleted": mid})


async def load_thread_history(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "load_thread_history"
    if not ctx.db or not ctx.thread_id:
        return _err(tool, "no_db_context", "须在带 DB 的 run 内调用（ctx.thread_id 缺失）")
    try:
        limit = int(inp.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    from app.agent.agent_loop import load_thread_messages

    msgs = load_thread_messages(ctx.db, ctx.thread_id, limit)
    out = []
    for m in msgs:
        content = m["content"]
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "thinking")
            )
            tools = [b.get("name") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        else:
            text = str(content)
            tools = []
        out.append({"role": m["role"], "text": text[:2000], "tools": tools})
    return _ok(tool, {"messages": out, "count": len(out)})


async def search_messages(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "search_messages"
    if not ctx.db or not ctx.thread_id:
        return _err(tool, "no_db_context", "须在带 DB 的 run 内调用（ctx.thread_id 缺失）")
    q = str(inp.args.get("q", "")).strip()
    if not q:
        return _err(tool, "empty_required", "q 必填")
    from app.db.models import Message

    # JSON 列 LIKE 会转义非 ASCII（中文存 \uXXXX），故加载后在 Python 内做子串匹配
    # （content 读出已反序列化）。扫最近 200 条，命中取前 20。
    # iter 369：查询+全量反序列化+匹配移 to_thread（长会话 200 条消息含长正文，
    # 此前整段跑在事件循环上，多 run 并发时全局卡顿）
    def _search() -> list:
        rows = (
            ctx.db.query(Message)
            .filter(Message.thread_id == ctx.thread_id)
            .order_by(Message.id.desc())
            .limit(200)
            .all()
        )
        return [r for r in rows if q in _msg_text(r.content)][:20]

    hits = await asyncio.to_thread(_search)
    return _ok(
        tool,
        {
            "matches": [{"id": r.id, "role": r.role.value, "snippet": _msg_text(r.content)[:500]} for r in hits],
            "count": len(hits),
        },
    )


def _msg_text(content) -> str:
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "thinking")
        )
    return str(content)


# ---------------------------------------------------------------------------
# 草稿提升（propose——决策③；实际提升由 phase G approve 端点执行）
# ---------------------------------------------------------------------------


async def promote_artifact(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    tool = "promote_artifact"
    if not ctx.db or not ctx.run_id or not ctx.thread_id:
        return _err(tool, "no_db_context", "须在带 DB 的 run 内调用（run_id/thread_id 缺失）")
    name = str(inp.args.get("name", "")).strip()
    target_kind = str(inp.args.get("target_kind", "note"))
    if not name:
        return _err(tool, "empty_required", "name 必填")
    # iter 397（T3；用户 2026-10-21 裁决）：schema 向模型承诺 target_kind 可为
    # "material"，但 approve 端点只支持 note/plan——选 material 必 400
    # material_promote_not_supported，且事务回滚后状态复原（仍在
    # awaiting_approval），用户反复点批准永远进不去（死胡同）。现从枚举移除
    # material，收到时给出可执行提示（改提 note），模型可自行改口。
    if target_kind == "material":
        return _err(
            tool,
            "invalid_enum",
            "target_kind=material 暂不支持（审批端点只落笔记/方案），请改用 target_kind=note",
        )
    if target_kind not in ("plan", "note"):
        return _err(tool, "invalid_enum", "target_kind 须为 plan/note")
    from app.db.models import RunArtifact, RunStatus, RunStep
    from app.agent.workspace import scratch_dir, committed_dir, _resolve_in, _root
    from app.core.storage import relative_to_root

    # iter 363 恢复流程：run 进行中草稿在 scratch（workspace_committed 此时为 False——仅 run
    # 结束后 _finalize_chat 调 commit_run_workspace 才置 True；旧逻辑据此判 promote 永远
    # not_committed，整个 draft→promote→approve 链路是死代码）。改为从 scratch 读指定草稿，
    # 复制到持久区 + 登记 RunArtifact，供 approve 端点读取提升。
    src = _resolve_in(scratch_dir(ctx.run_id), name)
    if not src.is_file():
        return _err(tool, "not_found", f"工作区无此产物: {name}")

    # iter 369/397（T4）：**文件复制与库写入拆成两段**——
    # ① 文件复制（MB 级草稿读写）走默认线程池，不占写线程池的 8 个槽位；
    # ② 库写入（flush 取写锁 + commit）走**专用写线程池**（dbwrite.run_db_write），
    #    此前整段在默认线程池里做 commit——SQLite 写锁等待（busy_timeout 10s）
    #    会占住默认池线程，多用户并发下把同池的文件 IO 槽位一起饿死。
    def _copy():
        import os

        dst_dir = committed_dir(ctx.thread_id, ctx.run_id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = _resolve_in(dst_dir, name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # iter 364：原子写（tmp + os.replace），防复制中途崩溃致 dst 半写
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(src.read_bytes())
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return dst

    dst = await asyncio.to_thread(_copy)

    def _register() -> int:
        from app.db.models import Run

        run = ctx.db.get(Run, ctx.run_id)
        if run is None:
            raise LookupError("run 不存在")
        # iter 364：同名重 promote 复用既有 RunArtifact（免重复登记）
        art = ctx.db.query(RunArtifact).filter_by(run_id=ctx.run_id, name=name).first()
        if art is None:
            art = RunArtifact(
                run_id=ctx.run_id,
                thread_id=ctx.thread_id,
                name=name,
                path=relative_to_root(_root(), dst),
                kind="committed",
                size=dst.stat().st_size,
            )
            ctx.db.add(art)
            ctx.db.flush()  # 取 art.id 供 run_step 记录
        else:
            art.path = relative_to_root(_root(), dst)
            art.size = dst.stat().st_size
        # 提议：run 进待审批 + 记 run_step（实际提升由 POST /runs/{id}/approve 执行）
        run.status = RunStatus.awaiting_approval
        ctx.db.add(
            RunStep(
                run_id=ctx.run_id,
                step_index=run.step_count,
                type="approval",
                name="promote_artifact",
                input={"name": name, "target_kind": target_kind, "artifact_id": art.id},
                output={"status": "awaiting_approval"},
            )
        )
        ctx.db.commit()
        return art.id

    from app.core.dbwrite import run_db_write

    try:
        artifact_id = await run_db_write(_register)
    except LookupError:
        return _err(tool, "not_found", "run 不存在")
    # iter 364：设 cancel_event 让 agent_loop 工具循环尽快 break（缩小 awaiting_approval 与
    # approve 的竞态窗口——原要等下一轮主循环检查，期间 approve 可致 agent 多跑 1+ 轮烧预算）
    ctx.cancel_event.set()
    return _ok(
        tool,
        {
            "proposed": True,
            "artifact_id": artifact_id,
            "name": name,
            "target_kind": target_kind,
            "run_status": "awaiting_approval",
        },
    )


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

workspace_write_tool = Tool(
    name="workspace_write",
    llm=False,
    func=workspace_write,
    description="向本任务工作区写入/覆盖文件（草拟大文件/学习计划）",
)
workspace_read_tool = Tool(
    name="workspace_read", llm=False, func=workspace_read, description="读取本任务工作区文件内容"
)
workspace_edit_tool = Tool(
    name="workspace_edit",
    llm=False,
    func=workspace_edit,
    description="字符串替换编辑工作区文件（old 须存在，多处需 replace_all）",
)
workspace_list_tool = Tool(
    name="workspace_list", llm=False, func=workspace_list, description="列出本任务工作区全部文件（名+大小）"
)
web_search_tool = Tool(
    name="web_search", llm=False, func=web_search, description="联网搜索（Bing 双引擎，返标题/URL/验证标记）"
)
web_fetch_tool = Tool(
    name="web_fetch", llm=False, func=web_fetch, description="抓取网页正文（SSRF 防护+正文提取，返 markdown+引用）"
)
memory_save_tool = Tool(
    name="memory_save", llm=False, func=memory_save, description="保存一条记忆（会话级/用户级，agent 草稿记忆库）"
)
memory_recall_tool = Tool(
    name="memory_recall", llm=False, func=memory_recall, description="按需召回记忆（子串过滤，按需加载——非自动召回）"
)
memory_delete_tool = Tool(
    name="memory_delete", llm=False, func=memory_delete, description="删除一条记忆（按 id，归属校验）"
)
load_thread_history_tool = Tool(
    name="load_thread_history",
    llm=False,
    func=load_thread_history,
    description="加载本会话最近历史消息（按需回放对话内记忆）",
)
search_messages_tool = Tool(
    name="search_messages", llm=False, func=search_messages, description="在本会话历史中搜索含某关键词的消息"
)
promote_artifact_tool = Tool(
    name="promote_artifact",
    llm=False,
    func=promote_artifact,
    description="提议把工作区产物提升为正式方案/笔记（待用户确认）",
)

AGENT_TOOLS = [
    workspace_write_tool,
    workspace_read_tool,
    workspace_edit_tool,
    workspace_list_tool,
    web_search_tool,
    web_fetch_tool,
    memory_save_tool,
    memory_recall_tool,
    memory_delete_tool,
    load_thread_history_tool,
    search_messages_tool,
    promote_artifact_tool,
]


# 工具入参 JSON schema（广告给模型的 tools[].input_schema）
AGENT_TOOL_SCHEMAS: dict[str, dict] = {
    "workspace_write": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
        "required": ["name", "content"],
    },
    "workspace_read": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "workspace_edit": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["name", "old", "new"],
    },
    "workspace_list": {"type": "object", "properties": {}},
    "web_search": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["query"],
    },
    "web_fetch": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    "memory_save": {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "kind": {"type": "string"},
            "scope": {"type": "string", "enum": ["thread", "user"]},
        },
        "required": ["content"],
    },
    "memory_recall": {
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "scope": {"type": "string", "enum": ["thread", "user"]},
            "limit": {"type": "integer"},
        },
    },
    "memory_delete": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    "load_thread_history": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    "search_messages": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    "promote_artifact": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            # iter 397（T3；用户 2026-10-21 裁决）：移除 "material"——审批端点
            # （POST /runs/{id}/approve）只落 note/plan，选 material 必 400 且状态
            # 回滚复原（用户反复点批准永远进不去）。从枚举移除即消除「schema 承诺
            # -> 端点 400 -> 状态不变」死胡同；工具侧另给明确提示引导改提 note。
            "target_kind": {"type": "string", "enum": ["plan", "note"]},
        },
        "required": ["name", "target_kind"],
    },
}


def agent_tool_definitions() -> list:
    """构造广告给模型的 ToolDefinition 列表（本会话 Agent 可用工具）。"""
    from app.llm.base import ToolDefinition

    return [
        ToolDefinition(
            name=t.name,
            description=t.description or "",
            input_schema=AGENT_TOOL_SCHEMAS.get(t.name, {"type": "object"}),
        )
        for t in AGENT_TOOLS
    ]
