"""R29（iter 386）：displaced 栈幽灵前任——覆盖链 × 热重载的正确性回归。

iter 369 的 displaced 回填栈按对象身份管理"被覆盖的前任"，但前任插件**重载**后，
其旧 entry 的 unregister 对 map 是 no-op（现行注册是覆盖者），旧 entry 以幽灵身份
留在栈里；后续卸载按栈顶回填时可能**复活已卸载插件的 handler**：

    A 注册 E -> B 覆盖 E（A1 进栈）-> A 重载（A1 幽灵留栈，A2 把 B1 压栈）
    -> B 卸载（B1 no-op 但幽灵留栈）-> A 卸载 -> 栈顶回填 B1（已死插件复活）

修复：unregister(entry) 同步把该 entry 从全部 displaced 栈剔除（无论其在不在 map）。
三注册表（parsers/exporters/providers）同构，逐一刻度。
"""

from __future__ import annotations

from app.plugins import exporters, parsers, providers


def _mk_parser(pid: str, tag: str):
    return parsers.ParserEntry(plugin_id=pid, extensions=(".ghost",), handler=lambda p, m: (tag, {}))


def _mk_exporter(pid: str, tag: str):
    return exporters.ExporterEntry(plugin_id=pid, format="ghost", handler=lambda plan, ctx: {"content": tag})


def _mk_provider(pid: str, tag: str):
    async def _h(system, user, *, model, max_tokens, api_key="", base_url=""):
        return {}

    return providers.ProviderEntry(plugin_id=pid, kind="ghostproto", handler=_h, default_model=tag)


def test_parsers_displaced_ghost_not_resurrected():
    a1, b1 = _mk_parser("plug_a", "A1"), _mk_parser("plug_b", "B1")
    parsers.register(a1)
    parsers.register(b1)  # 覆盖：displaced[.ghost]=[a1]
    a2 = _mk_parser("plug_a", "A2")
    parsers.unregister(a1)  # A 重载的卸载半程：map 是 b1 -> no-op，a1 幽灵须被剔栈
    parsers.register(a2)  # A 重载的注册半程
    parsers.unregister(b1)  # B 卸载：map 是 a2 -> no-op；b1 若留栈即幽灵
    parsers.unregister(a2)  # A 卸载：无幽灵时 E 应彻底消失
    assert parsers.find_parser(".ghost") is None, "已卸载插件的解析器被复活（displaced 幽灵）"
    assert ".ghost" not in parsers.registered_extensions()


def test_exporters_displaced_ghost_not_resurrected():
    a1, b1 = _mk_exporter("plug_a", "A1"), _mk_exporter("plug_b", "B1")
    exporters.register(a1)
    exporters.register(b1)
    a2 = _mk_exporter("plug_a", "A2")
    exporters.unregister(a1)
    exporters.register(a2)
    exporters.unregister(b1)
    exporters.unregister(a2)
    assert exporters.find_exporter("ghost") is None, "已卸载插件的导出器被复活（displaced 幽灵）"
    assert "ghost" not in exporters.registered_formats()


def test_providers_displaced_ghost_not_resurrected():
    a1, b1 = _mk_provider("plug_a", "A1"), _mk_provider("plug_b", "B1")
    providers.register(a1)
    providers.register(b1)  # 同 kind 覆盖：displaced[kind]=[a1]
    a2 = _mk_provider("plug_a", "A2")
    providers.unregister(a1)
    providers.register(a2)
    providers.unregister(b1)
    providers.unregister(a2)
    assert providers.find_provider("ghostproto") is None, "已卸载插件的 Provider 被复活（displaced 幽灵）"


def test_displaced_live_predecessor_still_restored():
    """修复不回归正常回填：仅覆盖者卸载时，仍在前台的原任（活插件）恢复。"""
    a1, b1 = _mk_parser("plug_a", "A1"), _mk_parser("plug_b", "B1")
    parsers.register(a1)
    parsers.register(b1)
    parsers.unregister(b1)  # 覆盖者卸载 -> a1（仍 loaded）恢复
    entry = parsers.find_parser(".ghost")
    assert entry is a1
    parsers.unregister(a1)  # 清场
    assert parsers.find_parser(".ghost") is None
