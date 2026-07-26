"""Agent 工具集测试（iter 345）—— 工作区/记忆/历史/提升/联网(mock)。"""

from __future__ import annotations

import asyncio

from app.agent import workspace as ws
from app.agent.context import ExecContext
from app.db import models
from app.db.base import SessionLocal
from app.tools import agent_tools as A
from app.tools.base import ToolInput


def _setup():
    db = SessionLocal()
    u = models.User(name="u", username="u", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running)
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


def test_workspace_tools_roundtrip(tmp_path, monkeypatch):
    db, u, t, r = _setup()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ctx = _ctx(db, u, t, r)
    w = asyncio.run(A.workspace_write(_inp("workspace_write", name="a.md", content="hello"), ctx))
    assert w.status == "ok" and w.data["bytes"] == 5
    rd = asyncio.run(A.workspace_read(_inp("workspace_read", name="a.md"), ctx))
    assert rd.data["content"] == "hello"
    ed = asyncio.run(A.workspace_edit(_inp("workspace_edit", name="a.md", old="hello", new="hi"), ctx))
    assert ed.data["replacements"] == 1
    lst = asyncio.run(A.workspace_list(_inp("workspace_list"), ctx))
    assert lst.data["files"][0]["name"] == "a.md"
    db.close()


def test_workspace_tool_requires_run_context():
    ctx = ExecContext(session_id="1", trace_id="tr")  # 无 run_id
    out = asyncio.run(A.workspace_write(_inp("workspace_write", name="a", content="x"), ctx))
    assert out.status == "error" and out.errors[0].code == "no_run_context"


def test_memory_save_recall_delete():
    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    s = asyncio.run(A.memory_save(_inp("memory_save", content="用户想学 React", kind="fact"), ctx))
    assert s.status == "ok" and s.data["scope"] == "thread"
    rc = asyncio.run(A.memory_recall(_inp("memory_recall", q="React"), ctx))
    assert rc.data["count"] == 1 and "React" in rc.data["memories"][0]["content"]
    dl = asyncio.run(A.memory_delete(_inp("memory_delete", id=s.data["id"]), ctx))
    assert dl.data["deleted"] == s.data["id"]
    rc2 = asyncio.run(A.memory_recall(_inp("memory_recall"), ctx))
    assert rc2.data["count"] == 0
    db.close()


def test_load_history_and_search_messages():
    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)
    db.add_all([
        models.Message(thread_id=t.id, role=models.MessageRole.user, content=[{"type": "text", "text": "什么是 React"}]),
        models.Message(thread_id=t.id, role=models.MessageRole.assistant, content=[{"type": "text", "text": "React 是一个 UI 库"}]),
    ])
    db.commit()
    h = asyncio.run(A.load_thread_history(_inp("load_thread_history", limit=10), ctx))
    assert h.data["count"] == 2
    sm = asyncio.run(A.search_messages(_inp("search_messages", q="UI 库"), ctx))
    assert sm.data["count"] == 1 and sm.data["matches"][0]["role"] == "assistant"
    db.close()


def test_promote_artifact_proposes(tmp_path, monkeypatch):
    db, u, t, r = _setup()
    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    ctx = _ctx(db, u, t, r)
    # iter 363 恢复流程：run 进行中草稿在 scratch（无需先 commit），promote 从 scratch
    # 提交到持久区 + 登记 RunArtifact（approve 端点据此读取）
    ws.write_file(r.id, "plan.md", "计划内容")
    out = asyncio.run(A.promote_artifact(_inp("promote_artifact", name="plan.md", target_kind="plan"), ctx))
    assert out.status == "ok" and out.data["proposed"] is True
    assert db.get(models.Run, r.id).status == models.RunStatus.awaiting_approval
    art = db.query(models.RunArtifact).filter_by(run_id=r.id, name="plan.md").first()
    assert art is not None and art.kind == "committed"
    # scratch 无此文件 -> not_found（旧 not_committed 已废弃）
    r2 = models.Run(thread_id=t.id, user_id=u.id, status=models.RunStatus.running)
    db.add(r2)
    db.commit()
    db.refresh(r2)
    ctx2 = _ctx(db, u, t, r2)
    bad = asyncio.run(A.promote_artifact(_inp("promote_artifact", name="missing.md", target_kind="note"), ctx2))
    assert bad.status == "error" and bad.errors[0].code == "not_found"
    db.close()


def test_web_search_mocked(monkeypatch):
    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)

    async def fake(query, ctx, limit=8):  # noqa: ARG001
        return [{"title": "React 官网", "url": "https://react.dev", "verified": "ok"}]

    monkeypatch.setattr("app.tools.search_resources.search_unit_resources", fake)
    out = asyncio.run(A.web_search(_inp("web_search", query="react"), ctx))
    assert out.status == "ok" and out.data["results"][0]["url"] == "https://react.dev"
    # 空 query -> error
    assert asyncio.run(A.web_search(_inp("web_search", query=""), ctx)).status == "error"
    db.close()


def test_web_fetch_mocked(monkeypatch):
    db, u, t, r = _setup()
    ctx = _ctx(db, u, t, r)

    async def fake_parse(url):  # noqa: ARG001
        return ("# 标题\n正文", {"title": "页", "chars": 8, "final_url": "https://x.com/final"})

    monkeypatch.setattr("app.tools.parse_material._parse_url", fake_parse)
    out = asyncio.run(A.web_fetch(_inp("web_fetch", url="https://x.com"), ctx))
    assert out.status == "ok"
    assert "正文" in out.data["text"] and out.data["url"] == "https://x.com/final"
    db.close()
