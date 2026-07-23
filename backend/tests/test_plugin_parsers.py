"""后端插件 API v1.3.0 文件解析扩展点测试（API.md「§文件解析扩展点」）。

覆盖：
- 注册表语义：gap-fill / 内置优先 / override / magic / 规范化 / 去重 / reset
- parse_material 委托：async handler / sync handler / handler 抛异常 parse_failed / 空文本 empty_content
- 上传接纳：插件 ext 放行 / magic 校验 / kb 复用同函数 / GET /materials/formats 发现
- 门面与装载：register 经 api.parsers 注册 / 装载可观测性 / register 失败回滚解析器
"""

from __future__ import annotations

import asyncio
import tempfile
from io import BytesIO
from pathlib import Path

import pytest

from app.agent.context import ExecContext
from app.tools.base import ToolInput
from app.tools.parse_material import parse_material


# --- 通用辅助 ---


def _ctx() -> ExecContext:
    return ExecContext(session_id="sess_test", trace_id="trace_test")


def _inp(args: dict) -> ToolInput:
    return ToolInput(tool="parse_material", session_id="sess_test", args=args)


def _run(args: dict):
    return asyncio.run(parse_material(_inp(args), _ctx()))


def _tmp(suffix: str, content: str | bytes = "") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb")
    f.write(content.encode("utf-8") if isinstance(content, str) else content)
    f.close()
    return f.name


def _entry(plugin_id, handler, exts, **kw):
    from app.plugins.parsers import ParserEntry, normalize_ext

    return ParserEntry(
        plugin_id=plugin_id,
        extensions=tuple(normalize_ext(e) for e in exts),
        handler=handler,
        **kw,
    )


# --- 注册表语义 ---


def test_normalize_ext():
    from app.plugins.parsers import normalize_ext

    assert normalize_ext("FOO") == ".foo"
    assert normalize_ext(".FOO") == ".foo"
    assert normalize_ext(" .Bar ") == ".bar"
    with pytest.raises(ValueError):
        normalize_ext("")


def test_gap_fill_for_unknown_ext():
    """未知扩展名注册后 find_parser 命中（内置不支持的格式由插件接管）。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo"]))
    assert parsers.find_parser(".foo") is not None
    assert parsers.find_parser(".FOO") is not None  # 大小写不敏感
    assert ".foo" in parsers.registered_extensions()


def test_builtin_priority_without_override():
    """内置已支持的 ext：未 override -> find_parser 返回 None（内置接管）。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".pdf"]))  # .pdf 内置
    assert parsers.find_parser(".pdf") is None


def test_override_takes_builtin():
    """override=True -> 即便内置 ext 也由插件接管。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".pdf"], override=True))
    entry = parsers.find_parser(".pdf")
    assert entry is not None and entry.plugin_id == "p1"


def test_later_register_overrides_earlier():
    from app.plugins import parsers

    async def h1(path, mode):
        return ("one", {})

    async def h2(path, mode):
        return ("two", {})

    parsers.register(_entry("p1", h1, [".foo"]))
    parsers.register(_entry("p2", h2, [".foo"]))
    assert parsers.find_parser(".foo").plugin_id == "p2"


def test_magic_and_all_entries():
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    e = _entry("p1", h, [".foo", ".bar"], magic=b"FOO", label="Foo/Bar")
    parsers.register(e)
    assert parsers.magic_for(".foo") == b"FOO"
    assert parsers.magic_for(".baz") is None
    entries = parsers.all_entries()  # 单 entry 多 ext -> 去重为一个
    assert len(entries) == 1 and entries[0].plugin_id == "p1"


def test_unregister_by_identity():
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    e = _entry("p1", h, [".foo", ".bar"])
    parsers.register(e)
    parsers.unregister(e)
    assert parsers.find_parser(".foo") is None
    assert parsers.find_parser(".bar") is None


def test_reset_clears_registry():
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo"]))
    parsers.reset()
    assert parsers.registered_extensions() == set()
    assert parsers.find_parser(".foo") is None


# --- parse_material 委托 ---


def test_parse_delegates_to_async_plugin_handler():
    from app.plugins import parsers

    async def h(path, mode):
        return (Path(path).read_text(encoding="utf-8").upper(), {"source_type": "foo", "parser": "p1"})

    parsers.register(_entry("p1", h, [".foo"]))
    path = _tmp(".foo", "hello private format")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert out.data["text"] == "HELLO PRIVATE FORMAT"
    assert out.data["meta"]["source_type"] == "foo"
    assert out.data["meta"]["parser"] == "p1"


def test_parse_delegates_to_sync_plugin_handler():
    """sync handler 经线程池执行（与内置同步解析器同口径）。"""
    from app.plugins import parsers

    def h(path, mode):
        return ("sync result", {"source_type": "foo"})

    parsers.register(_entry("p1", h, [".foo"]))
    path = _tmp(".foo", "anything")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert out.data["text"] == "sync result"


def test_parse_plugin_handler_raise_is_parse_failed():
    from app.plugins import parsers

    async def h(path, mode):
        raise RuntimeError("private lib crash")

    parsers.register(_entry("p1", h, [".foo"]))
    path = _tmp(".foo", "x")
    out = _run({"kind": "file", "source": path})
    assert out.status == "error"
    assert out.errors[0].code == "parse_failed"
    assert "p1" in out.errors[0].message


def test_parse_plugin_empty_text_is_empty_content():
    from app.plugins import parsers

    async def h(path, mode):
        return ("   ", {})

    parsers.register(_entry("p1", h, [".foo"]))
    path = _tmp(".foo", "x")
    out = _run({"kind": "file", "source": path})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"


def test_parse_override_replaces_builtin():
    """override=True 的插件解析器替换内置（如自定义 .md 处理）。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("OVERRIDDEN", {"source_type": "custom-md"})

    parsers.register(_entry("p1", h, [".md"], override=True))
    path = _tmp(".md", "# real markdown")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert out.data["text"] == "OVERRIDDEN"
    assert out.data["meta"]["source_type"] == "custom-md"


def test_parse_unknown_ext_without_plugin_still_unsupported():
    """无插件注册的未知 ext -> 内置 unsupported_format（零回归）。"""
    path = _tmp(".xlsx", "fake")
    out = _run({"kind": "file", "source": path})
    assert out.status == "error"
    assert out.errors[0].code == "unsupported_format"


def test_parse_builtin_pdf_unchanged_without_override():
    """对内置 ext 注册但未 override -> 内置照常（零回归）。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("SHOULD NOT BE USED", {})

    parsers.register(_entry("p1", h, [".txt"]))
    path = _tmp(".txt", "plain text")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert out.data["text"] == "plain text"  # 内置处理，未走插件


# --- 上传接纳（materials + kb 复用同函数）---


def _session(client, auth_headers, title="t"):
    r = client.post("/api/sessions", json={"title": title}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_upload_accepts_plugin_extension(client, auth_headers):
    """插件注册的 .foo 格式 -> 上传通过 _validate_upload（不再 unsupported_format）。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo"]))
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("note.foo", BytesIO(b"FOO" + b"\x00" * 4), "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"


def test_upload_plugin_magic_mismatch_rejected(client, auth_headers):
    """插件注册 magic=b'FOO'，上传内容不以 FOO 开头 -> unsupported_format。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo"], magic=b"FOO"))
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("note.foo", BytesIO(b"NOTFOO"), "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_kb_upload_uses_same_validation(client, auth_headers):
    """kb.py 复用 materials._validate_upload -> 插件格式同样被知识库上传接纳。"""
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo"]))
    r = client.post(
        "/api/kb/documents/upload",
        files={"file": ("doc.foo", BytesIO(b"FOO" + b"\x00" * 4), "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


def test_formats_endpoint_lists_plugin_parsers(client, auth_headers):
    from app.plugins import parsers

    async def h(path, mode):
        return ("x", {})

    parsers.register(_entry("p1", h, [".foo", ".bar"], label="Foo/Bar", override=False))
    r = client.get("/api/materials/formats", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert ".pdf" in body["builtin"]
    plugin = [p for p in body["plugin"] if p["plugin"] == "p1"]
    assert len(plugin) == 1
    assert set(plugin[0]["extensions"]) == {".foo", ".bar"}
    assert plugin[0]["label"] == "Foo/Bar"
    assert plugin[0]["override"] is False


def test_formats_endpoint_requires_auth(client):
    r = client.get("/api/materials/formats")
    assert r.status_code == 401


# --- 门面与装载（api.parsers / load_plugins / 回滚 / 可观测性）---


PARSER_PLUGIN = '''
PLUGIN_META = {"id": "fooparser", "name": "Foo Parser", "version": "0.1.0"}

def register(api):
    async def parse_foo(path, parse_mode):
        return ("foo content", {"source_type": "foo"})

    api.parsers.register([".foo", ".bar"], parse_foo, label="Foo/Bar", magic=b"FOO")
'''


PARSER_THEN_RAISE = '''
PLUGIN_META = {"id": "boomparser"}

def register(api):
    async def parse_foo(path, parse_mode):
        return ("x", {})

    api.parsers.register([".foo"], parse_foo)  # 注册后立刻抛
    raise RuntimeError("boom after register")
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
    """经 api.parsers 注册 -> 解析器生效 + 状态台账 parsers 字段记录扩展名。"""
    from app.plugins import parsers

    _write(tmp_path, "fooparser.py", PARSER_PLUGIN)
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.id == "fooparser" and s.state == "loaded"
    assert set(s.parsers) == {".foo", ".bar"}
    # 解析器确实生效
    entry = parsers.find_parser(".foo")
    assert entry is not None and entry.plugin_id == "fooparser"
    assert parsers.magic_for(".foo") == b"FOO"


def test_register_failure_rolls_back_parsers(tmp_path):
    """register 注册解析器后抛异常 -> 解析器回滚（find_parser None）+ status.parsers 清空。"""
    from app.plugins import parsers

    _write(tmp_path, "boomparser.py", PARSER_THEN_RAISE)
    statuses = _load(_fresh_app(), tmp_path)
    s = statuses[0]
    assert s.state == "failed"
    assert "register: boom" in s.error
    assert s.parsers == []  # 回滚清空
    assert parsers.find_parser(".foo") is None  # 注册表也已回滚


def test_status_endpoint_exposes_parsers(tmp_path):
    """GET /api/plugins 状态行包含 parsers 字段（管理员可见）。"""
    from fastapi.testclient import TestClient

    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    # require_admin：落库一个管理员（env token 认证解析为首个管理员，同 test_plugins 模式）
    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()

    _write(tmp_path, "fooparser.py", PARSER_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/plugins", headers={"Authorization": "Bearer test-token"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_version"] == "1.11.0"
        foo = [p for p in body["plugins"] if p["id"] == "fooparser"][0]
        assert set(foo["parsers"]) == {".foo", ".bar"}
