"""iter 369 第三轮审计 B15：插件/skills 装载层与门面回归。

覆盖：
- failed 插件修复后 reload 可成功（此前旧 failed 状态行残留 -> 误报「插件 id 重复」，
  台账无限叠加；热重载核心用例必踩）
- 包插件子模块热重载生效（sys.modules 前缀清理）
- StorageNamespace 线程安全（并发 set 不损坏 storage.json；tmp 名唯一）
- WebhookHandle.unregister 同步清台账与回滚闭包（改配置不再单调增长）
- 注册表（parsers/exporters/providers）覆盖者卸载回填被覆盖者
- PluginProvider.chat 宿主级超时（悬挂 handler -> TransientLLMError）
- SKILL.md 正文超 64KB 截断
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

BROKEN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "brk", "name": "BRK", "version": "0.1.0"}

def register(api):
    raise RuntimeError("改坏了")
'''

FIXED = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "brk", "name": "BRK", "version": "0.2.0"}

def register(api):
    router = APIRouter()

    @router.get("/ok")
    def ok():
        return {"ok": True}

    api.routes.mount(router, auth=False)
'''

PKG_PLUGIN = '''
from . import helper

from fastapi import APIRouter

PLUGIN_META = {"id": "pkg", "name": "PKG", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/v")
    def v():
        return {"helper_value": helper.VALUE}

    api.routes.mount(router, auth=False)
'''


def _write(directory: Path, name: str, source: str) -> Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


def _fresh_app():
    from app.main import create_app

    return create_app()


# --- H1：failed 插件修复后 reload ---


def test_reload_failed_plugin_after_fix(tmp_path):
    _write(tmp_path, "brk.py", BROKEN)
    app = _fresh_app()
    from app.plugins import loader

    loader.load_plugins(app, tmp_path)
    st = [s for s in loader.get_plugin_statuses() if s.id == "brk"][0]
    assert st.state == "failed" and "register" in (st.error or "")

    _write(tmp_path, "brk.py", FIXED)  # 修复源码
    st2 = loader.reload_plugin(app, "brk")
    assert st2 is not None and st2.state == "loaded", st2.error if st2 else None
    # 台账不叠加 failed 行
    rows = [s for s in loader.get_plugin_statuses() if s.id == "brk"]
    assert len(rows) == 1 and rows[0].state == "loaded"
    client = TestClient(app)
    assert client.get("/api/plugins/brk/ok").status_code == 200


# --- M2：包插件子模块热重载 ---


def test_package_plugin_submodule_reload(tmp_path):
    pkg = tmp_path / "pkg"
    _write(pkg, "plugin.py", PKG_PLUGIN)
    _write(pkg, "helper.py", "VALUE = 1\n")
    app = _fresh_app()
    from app.plugins import loader

    loader.load_plugins(app, tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/pkg/v").json()["helper_value"] == 1

    _write(pkg, "helper.py", "VALUE = 2\n")  # 改子模块
    st = loader.reload_plugin(app, "pkg")
    assert st is not None and st.state == "loaded"
    assert client.get("/api/plugins/pkg/v").json()["helper_value"] == 2  # 新代码生效
    # 卸载后子模块不残留 sys.modules
    loader.unload_plugin("pkg")
    import sys

    # iter 384：精确匹配本插件的模块名与前缀子模块——裸 startswith("dt_plugin_pkg")
    # 会误命中他测试装载的 dt_plugin_pkgp（pkgp），组合序下假红
    assert "dt_plugin_pkg" not in sys.modules
    assert not [m for m in sys.modules if m.startswith("dt_plugin_pkg.")]


# --- M1：StorageNamespace 线程安全 ---


def test_storage_namespace_concurrent_writes(tmp_path):
    from app.plugins.api import StorageNamespace

    ns = StorageNamespace(tmp_path)
    errs: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for j in range(60):
                ns.set(f"k{i}", {"j": j, "pad": "x" * 100})
        except BaseException as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs, errs

    # 崩溃等价场景：新实例重新加载——损坏 JSON 此前会被静默重置 {}
    ns2 = StorageNamespace(tmp_path)
    assert ns2.get("k0") == {"j": 59, "pad": "x" * 100}
    assert ns2.get("k7") == {"j": 59, "pad": "x" * 100}
    # 无 .tmp 残留
    assert not list(tmp_path.glob("*.tmp"))


# --- L2：WebhookHandle 台账联动 ---


def test_webhook_handle_clears_ledger_on_unregister():
    from app.plugins.api import WebhooksNamespace

    status = SimpleNamespace(webhooks=[], middleware=[])
    rollbacks: list = []
    wn = WebhooksNamespace("pid", status, rollbacks)
    handle = wn.register("https://example.com/hook", ["plan_created"])
    assert status.webhooks == ["https://example.com/hook"]
    assert len(rollbacks) == 1

    handle.unregister()
    assert status.webhooks == []  # 台账不再单调增长
    assert rollbacks == []  # 回滚闭包同步移除
    handle.unregister()  # 幂等
    assert status.webhooks == [] and rollbacks == []

    from app.plugins import bus

    bus.reset()


# --- L1：注册表覆盖回填 ---


def test_registry_displaced_restore():
    from app.plugins import exporters, parsers, providers

    def _h(path, parse_mode=""):  # noqa: ARG001
        return ("", {})

    try:
        a = parsers.ParserEntry(plugin_id="pa", extensions=(".foo",), handler=_h)
        b = parsers.ParserEntry(plugin_id="pb", extensions=(".foo",), handler=_h)
        parsers.register(a)
        parsers.register(b)
        assert parsers.find_parser(".foo").plugin_id == "pb"
        parsers.unregister(b)  # 覆盖者卸载 -> 回填前任
        assert parsers.find_parser(".foo").plugin_id == "pa"
        parsers.unregister(a)
        assert parsers.find_parser(".foo") is None

        def _e(content, ctx):  # noqa: ARG001
            return {"filename": "x.txt", "content": ""}

        ea = exporters.ExporterEntry(plugin_id="pa", format="fooexp", handler=_e)
        eb = exporters.ExporterEntry(plugin_id="pb", format="fooexp", handler=_e)
        exporters.register(ea)
        exporters.register(eb)
        assert exporters.find_exporter("fooexp").plugin_id == "pb"
        exporters.unregister(eb)
        assert exporters.find_exporter("fooexp").plugin_id == "pa"
        exporters.unregister(ea)
        assert exporters.find_exporter("fooexp") is None

        async def _p(system, user, **kw):  # noqa: ARG001
            return {"content": "", "model": "m", "usage": {"input_tokens": 0, "output_tokens": 0}}

        pr_a = providers.ProviderEntry(plugin_id="pa", kind="slowproto", handler=_p)
        pr_b = providers.ProviderEntry(plugin_id="pb", kind="slowproto", handler=_p)
        providers.register(pr_a)
        providers.register(pr_b)
        assert providers.find_provider("slowproto").plugin_id == "pb"
        providers.unregister(pr_b)
        assert providers.find_provider("slowproto").plugin_id == "pa"
        providers.unregister(pr_a)
        assert providers.find_provider("slowproto") is None
    finally:
        parsers.reset()
        exporters.reset()
        providers.reset()


# --- M3：PluginProvider 宿主级超时 ---


def test_plugin_provider_chat_timeout(monkeypatch):
    import app.llm.plugin_provider as pp
    from app.llm.base import TransientLLMError
    from app.plugins.providers import ProviderEntry

    monkeypatch.setattr(pp, "_HANDLER_TIMEOUT_S", 0.1)

    async def hanging(system, user, **kw):  # noqa: ARG001
        await asyncio.sleep(30)
        return {}

    entry = ProviderEntry(plugin_id="p", kind="hangproto", handler=hanging)
    prov = pp.PluginProvider(entry, model="m")

    t0 = time.monotonic()
    try:
        asyncio.run(prov.chat("s", "u"))
        raise AssertionError("悬挂 handler 应超时")
    except TransientLLMError:
        pass
    assert time.monotonic() - t0 < 5  # 未等满 30s


# --- L5：SKILL.md 截断 ---


def test_skill_md_body_truncated(tmp_path):
    from app.skills.loader import _MAX_BODY_CHARS, load_skills

    skill = tmp_path / "big"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: big\n---\n" + "x" * (_MAX_BODY_CHARS + 10_000), encoding="utf-8"
    )
    reg = load_skills(tmp_path)
    assert "big" in reg
    assert len(reg["big"]) <= _MAX_BODY_CHARS + 100  # 截断 + 标注
    assert "已截断" in reg["big"]
