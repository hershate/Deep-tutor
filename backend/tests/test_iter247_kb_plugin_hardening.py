"""iter 247 加固回归：插件 register 回滚、kb 解析/删除竞态、_persist_parsed 孤儿清理、
slides 预算守卫位置（零成本降级不误拦）。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.api import kb as kb_mod
from app.core.storage import kb_parsed_dir
from app.db import models
from app.db.base import SessionLocal

BASE = "/api/kb/documents"


def _mk_admin() -> int:
    """直接建管理员行（env token 路径下首个 authed 请求本可懒建，但部分断言在
    请求前就要 user_id，显式建行避免依赖请求顺序）。"""
    db = SessionLocal()
    try:
        u = models.User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _first_user_id() -> int:
    db = SessionLocal()
    try:
        return db.query(models.User).order_by(models.User.id).first().id
    finally:
        db.close()


def _upload(client, headers, name="a.md", body=b"# hello\n", mime="text/markdown"):
    return client.post(f"{BASE}/upload", files={"file": (name, body, mime)}, headers=headers)


# --- 插件 register 中途失败回滚（iter 247 审计 M1）---

_HALF_PLUGIN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "half"}


def register(api):
    r = APIRouter()

    @r.get("/ping")
    def _ping():
        return {"pong": True}

    api.routes.mount(r, auth=False)
    api.events.on("half.custom", lambda payload: None)
    api.tools.register_python("half_tool", lambda inp, ctx: {})
    raise RuntimeError("中途失败")
'''


def test_plugin_register_failure_rolls_back(tmp_path):
    """register 挂载路由+订阅事件+注册工具后抛异常 -> failed 且三路注册全部回滚
    （此前 failed 插件的路由仍响应/事件仍触发/工具仍汇入注册表，台账与运行态不一致）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.plugins import bus, runtime
    from app.plugins.loader import load_plugins

    (tmp_path / "half.py").write_text(_HALF_PLUGIN, encoding="utf-8")
    app = create_app()
    statuses = load_plugins(app, tmp_path)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.id == "half" and s.state == "failed"
    assert "回滚" in s.error
    # 台账与运行态一致：失败插件的路由/事件/工具清单已清空
    assert s.routes == [] and s.events == [] and s.tools == []
    # 路由已摘除（公开路由本可匿名 200 返回 pong，回滚后不再由插件视图函数响应）
    r = TestClient(app).get("/api/plugins/half/ping")
    assert not (r.status_code == 200 and r.json() == {"pong": True})
    # 事件订阅已退订（off 留空桶为 bus 既有语义，断言无监听器残留）
    assert bus._listeners.get("half.custom", []) == []
    # 工具已移出运行时表
    assert all(t.name != "half_tool" for t in runtime.plugin_tools())


def test_plugin_register_success_keeps_registrations(tmp_path):
    """对照：register 成功时回滚台账不触发，路由/事件/工具全部保留。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.plugins import bus, runtime
    from app.plugins.loader import load_plugins

    (tmp_path / "half.py").write_text(_HALF_PLUGIN.replace('raise RuntimeError("中途失败")', "pass"), encoding="utf-8")
    app = create_app()
    statuses = load_plugins(app, tmp_path)
    assert statuses[0].state == "loaded"
    assert statuses[0].routes == ["/api/plugins/half"]
    assert TestClient(app).get("/api/plugins/half/ping").json() == {"pong": True}
    assert len(bus._listeners.get("half.custom", [])) == 1
    assert any(t.name == "half_tool" for t in runtime.plugin_tools())


# --- kb 解析/删除竞态（iter 247 审计 LOW-1）---


def _stub_parse_blocking(monkeypatch, started, release, side_effect=None):
    """file 类解析桩：置 started 后阻塞至 release；side_effect 在阻塞期间执行（模拟并发删除）。"""
    import asyncio

    from app.tools.base import ToolOutput

    async def _fake(inp, ctx):
        if inp.args.get("kind") == "file":
            started.set()
            if side_effect is not None:
                side_effect()
            await asyncio.to_thread(release.wait, 10)
        return ToolOutput(tool="parse_material", status="ok", data={"text": "正文", "meta": {}})

    monkeypatch.setattr(kb_mod, "parse_material", _fake)


def test_delete_document_while_parsing_409(client, auth_headers, monkeypatch):
    """解析进行中删除 -> 409（此前删除成功：解析完写出孤儿文件，rowid 复用时错绑新文档）。"""
    started, release = threading.Event(), threading.Event()
    _stub_parse_blocking(monkeypatch, started, release)

    d = _upload(client, auth_headers).json()
    first: dict = {}

    def _parse_first():
        first["r"] = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers)

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "解析未开始"
        r = client.delete(f"{BASE}/{d['id']}", headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    # 文档仍在且正常解析完成
    assert client.get(f"{BASE}/{d['id']}", headers=auth_headers).json()["status"] == "parsed"


def test_parse_row_deleted_midflight_cleans_orphan(client, auth_headers, monkeypatch):
    """解析期间行被删（绕过端点直删，模拟竞态残余窗口）-> 404 且刚写出的解析文件被清理。"""
    doc_id_box: list[int] = []

    def _delete_row():
        db = SessionLocal()
        try:
            row = db.get(models.KbDocument, doc_id_box[0])
            db.delete(row)
            db.commit()
        finally:
            db.close()

    started, release = threading.Event(), threading.Event()
    release.set()  # 不阻塞：删除在 side_effect 内完成
    d = _upload(client, auth_headers).json()
    doc_id_box.append(d["id"])
    _stub_parse_blocking(monkeypatch, started, release, side_effect=_delete_row)

    parsed_dir = kb_parsed_dir(_first_user_id())
    before = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    r = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 404
    after = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    assert after == before  # 无孤儿解析文件残留


def test_parse_rowid_reused_midflight_rejected(client, auth_headers, monkeypatch):
    """rowid 复用：解析期间行被删且新文档拿到同 id -> 409 冲突，新文档行不被错绑。"""
    box: dict = {}

    def _delete_and_recreate():
        db = SessionLocal()
        try:
            row = db.get(models.KbDocument, box["id"])
            db.delete(row)
            db.flush()
            # 强制同 id 重建（模拟 SQLite 最大 rowid 复用）
            new = models.KbDocument(
                id=box["id"], user_id=box["uid"], title="新文档",
                kind=models.MaterialKind.text, ext="txt", source="s",
                parsed_chars=0, meta={}, content_hash="different-hash", status="confirmed",
            )
            db.add(new)
            db.commit()
        finally:
            db.close()

    started, release = threading.Event(), threading.Event()
    release.set()
    d = _upload(client, auth_headers).json()
    box["id"] = d["id"]
    box["uid"] = _first_user_id()
    _stub_parse_blocking(monkeypatch, started, release, side_effect=_delete_and_recreate)

    r = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    # 新文档行未被错绑（无解析文本路径、状态未变）
    db = SessionLocal()
    try:
        row = db.get(models.KbDocument, d["id"])
        assert row.content_hash == "different-hash"
        assert row.parsed_text_path is None and row.status == "confirmed"
    finally:
        db.close()


# --- _persist_parsed commit 失败孤儿清理（iter 247 审计 LOW-2）---


def test_persist_parsed_commit_failure_cleans_file(client, auth_headers, monkeypatch):
    """text 入库 commit 失败（磁盘满/库锁）-> 已落盘解析文本随之清理，不留孤儿。"""
    from sqlalchemy.orm import Session

    async def _ok(inp, ctx):
        from app.tools.base import ToolOutput

        return ToolOutput(tool="parse_material", status="ok", data={"text": "正文", "meta": {}})

    monkeypatch.setattr(kb_mod, "parse_material", _ok)
    _mk_admin()

    def _boom(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Session, "commit", _boom)
    parsed_dir = kb_parsed_dir(_first_user_id())
    before = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    with pytest.raises(RuntimeError, match="disk full"):  # TestClient 上抛服务端异常（500 已发）
        client.post(f"{BASE}/text", json={"text": "commit 会失败的笔记"}, headers=auth_headers)
    after = set(parsed_dir.glob("*.txt")) if parsed_dir.is_dir() else set()
    assert after == before  # 解析文件已清理


# --- slides 预算守卫位置（iter 247 审计 LOW-3）---


def _mk_plan_direct() -> int:
    """直接建行造 plan（不经生成流，隔离 slides 端点本身）。"""
    user_id = _mk_admin()
    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title="s")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id, version=1,
            content={"intent": {"topic": "T"}, "stages": [{"index": 0, "name": "阶段", "goal": "g"}]},
        )
        db.add(plan)
        db.commit()
        return plan.id
    finally:
        db.close()


_REAL_SKILLS = str(Path(__file__).resolve().parents[2] / "skills")


def test_slides_no_provider_bypasses_budget_guard(client, auth_headers, monkeypatch):
    """无 LLM key（纯 Python 默认 deck 零成本）时预算耗尽可能性不拦截——与 md/ics 等
    零成本格式口径一致（此前守卫在 provider 解析前，免费兜底被 402 误拦）。"""
    from app.api import exports as exports_mod
    from app.api.errors import ApiError
    from app.core import settings as settings_mod

    plan_id = _mk_plan_direct()

    def _no_provider(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    def _budget_boom(*a, **k):
        raise ApiError("budget_exceeded", "日预算已用尽", status=402)

    monkeypatch.setattr(exports_mod, "get_provider", _no_provider)
    monkeypatch.setattr(exports_mod, "check_daily_budget", _budget_boom)
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    r = client.post(f"/api/export/{plan_id}", json={"format": "slides"}, headers=auth_headers)
    assert r.status_code == 200, r.text  # 零成本降级不被预算拦截
    assert r.json()["format"] == "html"
    assert "warning" in r.json()


def test_slides_with_provider_budget_guard_active(client, auth_headers, monkeypatch):
    """有 provider（会产生 LLM 成本）时预算守卫仍然生效 -> 402。"""
    from app.api import exports as exports_mod
    from app.api.errors import ApiError
    from app.core import settings as settings_mod
    from app.llm.base import LLMProvider, LLMResponse, LLMUsage

    class _P(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return LLMResponse(content="{}", usage=LLMUsage(input_tokens=1, output_tokens=1, model="m"))

    plan_id = _mk_plan_direct()

    def _budget_boom(*a, **k):
        raise ApiError("budget_exceeded", "日预算已用尽", status=402)

    monkeypatch.setattr(exports_mod, "get_provider", lambda *a, **k: _P())
    monkeypatch.setattr(exports_mod, "check_daily_budget", _budget_boom)
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    r = client.post(f"/api/export/{plan_id}", json={"format": "slides"}, headers=auth_headers)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "budget_exceeded"


# --- 上传限流（iter 247 审计 LOW-4）---


def test_kb_upload_rate_limited(client, auth_headers, monkeypatch):
    """上传 20/min 限流（此前无防，多用户部署 N×50MB 并发可 OOM）。"""
    for i in range(20):
        r = _upload(client, auth_headers, name=f"f{i}.md", body=f"# doc{i}\n".encode())
        assert r.status_code == 200, r.text
    r = _upload(client, auth_headers, name="f21.md", body=b"# doc21\n")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
