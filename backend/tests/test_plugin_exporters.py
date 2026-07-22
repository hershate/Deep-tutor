"""后端插件 API v1.4.0 导出格式扩展点测试（API.md「§导出格式扩展点」）。

覆盖：
- 注册表语义：normalize/字符集校验/gap-fill/内置优先/override/后注册覆盖/去重/reset
- 端点分发：async/sync renderer / 异常 export_failed / 契约校验（filename/content/data/
  大小上限/路径注入清洗/保留键保护）/ override 接管内置 / 未知格式 unsupported_format
  （而非 pydantic 422）/ 内置零回归 / wrong_only 拒绝插件格式 / GET /export/formats
- 门面与装载：register 经 api.exporters 注册 / 装载可观测性 / register 失败回滚导出器
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.db.base import SessionLocal


# --- 通用辅助 ---


def _entry(plugin_id, handler, fmt, **kw):
    from app.plugins.exporters import ExporterEntry, normalize_format

    return ExporterEntry(
        plugin_id=plugin_id,
        format=normalize_format(fmt),
        handler=handler,
        **kw,
    )


def _seed_plan() -> int:
    """落库 1 管理员 + 1 会话 + 1 方案（env token 认证解析为首个管理员，同 test_plugins 模式）。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="导出会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id,
            version=1,
            content={"intent": {"topic": "算法"}, "weeks": []},
        )
        db.add(plan)
        db.commit()
        return plan.id
    finally:
        db.close()


# --- 注册表语义 ---


def test_normalize_format():
    from app.plugins.exporters import normalize_format

    assert normalize_format("EPUB") == "epub"
    assert normalize_format(" Mobi-Pocket ") == "mobi-pocket"
    with pytest.raises(ValueError):
        normalize_format("")
    with pytest.raises(ValueError):
        normalize_format("   ")


def test_valid_format_name():
    from app.plugins.exporters import valid_format_name

    assert valid_format_name("epub")
    assert valid_format_name("fmt_2-x")
    assert not valid_format_name("Epub")  # 注册前须 normalize（facade 已保证）
    assert not valid_format_name("..")
    assert not valid_format_name("a/b")  # 路径分隔符禁止
    assert not valid_format_name("a b")
    assert not valid_format_name("")


def test_gap_fill_for_unknown_format():
    """未知格式注册后 find_exporter 命中（内置不支持的格式由插件接管）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "x.epub", "data": b"x"}

    exporters.register(_entry("p1", h, "epub"))
    assert exporters.find_exporter("epub") is not None
    assert exporters.find_exporter("EPUB") is not None  # 大小写不敏感
    assert "epub" in exporters.registered_formats()


def test_builtin_priority_without_override():
    """内置格式：未 override -> find_exporter 返回 None（内置接管）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "x.md", "content": "x"}

    exporters.register(_entry("p1", h, "md"))  # md 内置
    assert exporters.find_exporter("md") is None


def test_override_takes_builtin():
    """override=True -> 即便内置格式也由插件接管。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "x.md", "content": "x"}

    exporters.register(_entry("p1", h, "md", override=True))
    entry = exporters.find_exporter("md")
    assert entry is not None and entry.plugin_id == "p1"


def test_later_register_overrides_earlier():
    from app.plugins import exporters

    async def h1(plan, ctx):
        return {"filename": "a", "content": "one"}

    async def h2(plan, ctx):
        return {"filename": "a", "content": "two"}

    exporters.register(_entry("p1", h1, "epub"))
    exporters.register(_entry("p2", h2, "epub"))
    assert exporters.find_exporter("epub").plugin_id == "p2"


def test_all_entries_and_label():
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a", "content": "x"}

    exporters.register(_entry("p1", h, "epub", label="EPUB 电子书"))
    entries = exporters.all_entries()
    assert len(entries) == 1
    assert entries[0].label == "EPUB 电子书"


def test_unregister_by_identity():
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a", "content": "x"}

    e = _entry("p1", h, "epub")
    exporters.register(e)
    exporters.unregister(e)
    assert exporters.find_exporter("epub") is None
    assert exporters.registered_formats() == set()


def test_reset_clears_registry():
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a", "content": "x"}

    exporters.register(_entry("p1", h, "epub"))
    exporters.reset()
    assert exporters.registered_formats() == set()
    assert exporters.find_exporter("epub") is None


# --- 端点分发（POST /api/export/{plan_id}）---


def test_export_delegates_to_async_plugin_renderer(client, auth_headers):
    """插件格式走插件 renderer：文本产物 content 随响应返回，额外键并入。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        assert plan["intent"]["topic"] == "算法"  # plan 内容字典传入
        assert ctx["plan_id"] and ctx["user_id"] and ctx["wrong_only"] is False
        return {
            "filename": "plan.epub",
            "content": "EPUB-TEXT",
            "mime": "application/epub+zip",
            "pages": 3,
        }

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "epub"
    assert body["filename"] == "plan.epub"
    assert body["content"] == "EPUB-TEXT"
    assert body["mime"] == "application/epub+zip"  # 额外键并入
    assert body["pages"] == 3
    assert "bytes_b64" not in body


def test_export_delegates_to_sync_plugin_renderer_bytes(client, auth_headers):
    """sync renderer 经线程池执行；bytes 产物 base64 为 bytes_b64 返回。"""
    from app.plugins import exporters

    def h(plan, ctx):
        return {"filename": "plan.mobi", "data": b"MOBI-BINARY"}

    exporters.register(_entry("p1", h, "mobi"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "mobi"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "mobi"
    assert base64.b64decode(body["bytes_b64"]) == b"MOBI-BINARY"
    assert "content" not in body
    assert "data" not in body  # 原始 bytes 键不外泄


def test_export_plugin_renderer_raise_is_export_failed(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        raise RuntimeError("renderer crash")

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_failed"
    assert "p1" in r.json()["error"]["message"]


def test_export_plugin_missing_filename_rejected(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"content": "x"}  # 缺 filename

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_failed"
    assert "filename" in r.json()["error"]["message"]


def test_export_plugin_missing_payload_rejected(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a.epub"}  # content/data 均无

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_failed"


def test_export_plugin_bad_types_rejected(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a.epub", "data": "not-bytes"}  # data 非 bytes

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_failed"


def test_export_plugin_oversize_data_rejected(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a.epub", "data": b"\x00" * (exporters.MAX_BYTES + 1)}

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_failed"
    assert "上限" in r.json()["error"]["message"]


def test_export_plugin_filename_path_sanitized(client, auth_headers):
    """filename 仅取 basename（防路径注入；字段会进下载 Content-Disposition）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "../../etc/evil.epub", "content": "x"}

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "evil.epub"


def test_export_plugin_reserved_keys_not_overridden(client, auth_headers):
    """保留键（file_url/format/filename/content/bytes_b64/data）由宿主控制，插件不可覆盖。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {
            "filename": "real.epub",
            "content": "REAL",
            "format": "md",  # 试图篡改 -> 被忽略
            "file_url": "http://evil.example/x",
        }

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "epub"
    assert body["file_url"] == ""


def test_export_override_replaces_builtin_md(client, auth_headers):
    """override=True 的插件导出器替换内置 md（如自定义 Markdown 模板）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "custom.md", "content": "OVERRIDDEN-MD"}

    exporters.register(_entry("p1", h, "md", override=True))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "md"
    assert body["content"] == "OVERRIDDEN-MD"


def test_export_unknown_format_is_unsupported_not_422(client, auth_headers):
    """未知格式 -> 显式 unsupported_format 400（Literal 移除后不再是 pydantic 422）。"""
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "xyz"}, headers=auth_headers)
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "unsupported_format"
    assert "xyz" in body["error"]["message"]


def test_export_empty_format_is_unsupported(client, auth_headers):
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "  "}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_export_builtin_md_zero_regression(client, auth_headers):
    """无插件注册时内置 md 导出行为不变（零回归）。"""
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "md"
    assert body["filename"].endswith(".md")
    assert "算法" in body["content"]


def test_export_builtin_register_without_override_still_builtin(client, auth_headers):
    """对内置格式注册但未 override -> 内置照常（零回归）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "x.md", "content": "SHOULD NOT BE USED"}

    exporters.register(_entry("p1", h, "md"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["content"] != "SHOULD NOT BE USED"  # 内置处理，未走插件


def test_export_wrong_only_rejected_for_plugin_format(client, auth_headers):
    """wrong_only 是内置 anki 专属口径；插件格式显式 400（不静默忽略）。"""
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a.epub", "content": "x"}

    exporters.register(_entry("p1", h, "epub"))
    plan_id = _seed_plan()
    r = client.post(
        f"/api/export/{plan_id}",
        json={"format": "epub", "wrong_only": True},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_export_wrong_only_passes_to_override_anki(client, auth_headers):
    """override 接管内置 anki 的插件收到 ctx.wrong_only（可自行实现错题口径）。"""
    from app.plugins import exporters

    seen: dict = {}

    async def h(plan, ctx):
        seen["wrong_only"] = ctx["wrong_only"]
        return {"filename": "cards.txt", "content": "wrong-cards"}

    exporters.register(_entry("p1", h, "anki", override=True))
    plan_id = _seed_plan()
    r = client.post(
        f"/api/export/{plan_id}",
        json={"format": "anki", "wrong_only": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert seen["wrong_only"] is True
    assert r.json()["content"] == "wrong-cards"


def test_export_formats_endpoint_lists_plugin_exporters(client, auth_headers):
    from app.plugins import exporters

    async def h(plan, ctx):
        return {"filename": "a", "content": "x"}

    exporters.register(_entry("p1", h, "epub", label="EPUB 电子书"))
    exporters.register(_entry("p2", h, "md", override=True))
    r = client.get("/api/export/formats", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["builtin"] == ["anki", "ics", "md", "pdf", "slides"]
    plugin = {p["format"]: p for p in body["plugin"]}
    assert plugin["epub"]["label"] == "EPUB 电子书"
    assert plugin["epub"]["plugin_id"] == "p1"
    assert plugin["epub"]["override"] is False
    assert plugin["md"]["override"] is True


def test_export_formats_endpoint_requires_auth(client):
    r = client.get("/api/export/formats")
    assert r.status_code == 401


def test_export_endpoint_requires_auth(client):
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "epub"})
    assert r.status_code == 401


# --- 门面与装载（api.exporters / load_plugins / 回滚 / 可观测性）---


EXPORTER_PLUGIN = '''
PLUGIN_META = {"id": "epubexp", "name": "EPUB Exporter", "version": "0.1.0"}

def register(api):
    async def render_epub(plan, ctx):
        return {"filename": "plan.epub", "data": b"EPUB", "mime": "application/epub+zip"}

    api.exporters.register("EPUB", render_epub, label="EPUB 电子书")  # 大写 -> 规范化 epub
'''


EXPORTER_THEN_RAISE = '''
PLUGIN_META = {"id": "boomexp"}

def register(api):
    async def render_epub(plan, ctx):
        return {"filename": "a", "content": "x"}

    api.exporters.register("epub", render_epub)  # 注册后立刻抛
    raise RuntimeError("boom after register")
'''


EXPORTER_BAD_NAME = '''
PLUGIN_META = {"id": "badname"}

def register(api):
    async def render(plan, ctx):
        return {"filename": "a", "content": "x"}

    api.exporters.register("../evil", render)  # 非法字符集 -> ValueError -> 装载 failed
'''


def _write(directory: Path, name: str, source: str) -> Path:
    p = directory / name
    p.write_text(source, encoding="utf-8")
    return p


def _fresh_app():
    from app.main import create_app

    return create_app()


def _load(app, directory: Path):
    from app.plugins.loader import load_plugins

    return load_plugins(app, directory)


def test_facade_registers_and_is_observable(tmp_path):
    """经 api.exporters 注册 -> 导出器生效 + 状态台账 exporters 字段记录格式名。"""
    from app.plugins import exporters

    _write(tmp_path, "epubexp.py", EXPORTER_PLUGIN)
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.id == "epubexp" and s.state == "loaded"
    assert s.exporters == ["epub"]  # 规范化小写
    entry = exporters.find_exporter("epub")
    assert entry is not None and entry.plugin_id == "epubexp"
    assert entry.label == "EPUB 电子书"


def test_facade_rejects_bad_format_name(tmp_path):
    """非法格式名（字符集白名单外）-> register 抛 ValueError -> 插件装载 failed 且不残留注册。"""
    from app.plugins import exporters

    _write(tmp_path, "badname.py", EXPORTER_BAD_NAME)
    statuses = _load(_fresh_app(), tmp_path)
    s = statuses[0]
    assert s.state == "failed"
    assert "格式名非法" in s.error
    assert exporters.registered_formats() == set()


def test_register_failure_rolls_back_exporters(tmp_path):
    """register 注册导出器后抛异常 -> 导出器回滚（find_exporter None）+ status.exporters 清空。"""
    from app.plugins import exporters

    _write(tmp_path, "boomexp.py", EXPORTER_THEN_RAISE)
    statuses = _load(_fresh_app(), tmp_path)
    s = statuses[0]
    assert s.state == "failed"
    assert "register: boom" in s.error
    assert s.exporters == []  # 回滚清空
    assert exporters.find_exporter("epub") is None  # 注册表也已回滚


def test_status_endpoint_exposes_exporters(tmp_path):
    """GET /api/plugins 状态行包含 exporters 字段（管理员可见）。"""
    from fastapi.testclient import TestClient

    from app.core.security import hash_password
    from app.db.models import User

    # require_admin：落库一个管理员（env token 认证解析为首个管理员，同 test_plugins 模式）
    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()

    _write(tmp_path, "epubexp.py", EXPORTER_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/plugins", headers={"Authorization": "Bearer test-token"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_version"] == "1.7.0"
        foo = [p for p in body["plugins"] if p["id"] == "epubexp"][0]
        assert foo["exporters"] == ["epub"]


def test_loaded_plugin_exporter_serves_export_endpoint(tmp_path, client, auth_headers):
    """装载的插件导出器端到端：POST /api/export/{plan_id} format=epub 走插件渲染器。

    注：client 夹具的 app 在测试进程启动时已加载插件（plugins/ 目录）；本测试经
    注册表直注模拟已装载状态（端点只读注册表，与装载路径解耦，语义等价）。
    """
    from app.plugins import exporters

    async def render_epub(plan, ctx):
        return {"filename": "plan.epub", "data": b"EPUB-BYTES"}

    exporters.register(_entry("epubexp", render_epub, "epub"))
    plan_id = _seed_plan()
    r = client.post(f"/api/export/{plan_id}", json={"format": "EPUB"}, headers=auth_headers)
    assert r.status_code == 200, r.text  # 大写 format 规范化后命中
    assert base64.b64decode(r.json()["bytes_b64"]) == b"EPUB-BYTES"
