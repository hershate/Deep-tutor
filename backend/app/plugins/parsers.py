"""插件自定义文件解析器注册表（后端插件 API v1.3.0 扩展点）。

契约见 API.md「后端插件 API §文件解析扩展点」。用途：插件为内置 parse_material
不支持的文件格式（私有格式/小众格式，如 .epub 电子书、.xmind 笔记、企业私有格式）
注册解析器，上传校验与解析链路自动识别并委托——无需改宿主代码即可"让后端支持新格式"。

设计要点：
- 注册表为 ext(小写带点) -> ParserEntry 的映射；同一 ext 后注册覆盖前者（记 warn）。
- 内置优先（安全默认）：find_parser(ext) 对内置已支持的 ext 默认返回 None（内置处理），
  除非插件显式 override=True（启动告警，属高级用法）。
- handler 契约与内置 _parse_xxx 完全一致：async 或 sync (path, parse_mode) -> (text, meta)，
  便于插件作者照搬内置解析器写法。async 直接 await；sync 在线程池执行（不阻塞事件循环，
  与内置同步解析器经 to_thread 运行同一口径）。
- 上传层经 registered_extensions()/magic_for() 接纳插件格式 + 可选 magic-bytes 校验。

隔离：模块级状态；测试经 reset() 清空（reset_plugins 调用，conftest isolate 夹具每测试前触发）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("deep_tutor.plugins.parsers")

# 内置已支持的扩展名缓存（parse_material.SUPPORTED_FILE_EXTS）。懒导入避免与
# parse_material 的反向依赖（parse_material 调本模块 find_parser）成导入期循环。
_builtin_cache: set[str] | None = None


def _builtin_exts() -> set[str]:
    global _builtin_cache
    if _builtin_cache is None:
        from app.tools.parse_material import SUPPORTED_FILE_EXTS

        _builtin_cache = set(SUPPORTED_FILE_EXTS)
    return _builtin_cache


def normalize_ext(ext: str) -> str:
    """规范化扩展名为小写带前导点（".FOO" / "foo" -> ".foo"）。"""
    ext = (ext or "").strip().lower()
    if not ext:
        raise ValueError("扩展名不能为空")
    return ext if ext.startswith(".") else "." + ext


@dataclass
class ParserEntry:
    """单个插件解析器注册项。"""

    plugin_id: str
    extensions: tuple[str, ...]  # 已规范化（小写带点）
    handler: Callable  # (path: Path, parse_mode: str) -> (text, meta)；async 或 sync
    override: bool = False  # True = 即便对内置 ext 也接管（高级，启动告警）
    magic: bytes | None = None  # 可选：上传 magic-bytes 校验签名
    label: str = ""  # 可选：人类可读格式名（GET /materials/formats 展示用）


# ext -> entry；同一 entry 可覆盖多个 ext，故 _parsers.values() 含重复 entry（去重见 all_entries）
_parsers: dict[str, ParserEntry] = {}


def register(entry: ParserEntry) -> None:
    """注册解析器。同一 ext 后注册覆盖前者；对内置 ext（未 override）注册记 warn 提示不生效。"""
    for ext in entry.extensions:
        existing = _parsers.get(ext)
        if existing is not None and existing is not entry:
            log.warning(
                "插件解析器覆盖既有注册: %s（原 %s <- 新 %s）", ext, existing.plugin_id, entry.plugin_id
            )
        if not entry.override and ext in _builtin_exts():
            log.warning(
                "插件 %s 为内置格式 %s 注册解析器但未 override=True -> 不会生效（内置优先）；"
                "如确需覆盖内置解析器，请 override=True 并充分测试",
                entry.plugin_id,
                ext,
            )
        _parsers[ext] = entry


def unregister(entry: ParserEntry) -> None:
    """移除该 entry 名下全部 ext（loader register 失败回滚用；按对象身份匹配）。"""
    for ext in [k for k, v in _parsers.items() if v is entry]:
        _parsers.pop(ext, None)


def find_parser(ext: str) -> ParserEntry | None:
    """返回应处理 ext 的 entry（内置优先，override 例外）；无合适插件解析器 -> None。

    parse_material 据此决定是否委托：None -> 走内置 dispatch（零回归）。
    """
    if not ext:
        return None
    ext = ext.lower()
    entry = _parsers.get(ext)
    if entry is None:
        return None
    if not entry.override and ext in _builtin_exts():
        return None  # 内置接管（安全默认）
    return entry


def registered_extensions() -> set[str]:
    """全部插件注册的 ext（上传层接纳判定用；含 override 的内置 ext，但后者本就在内置集合内）。"""
    return set(_parsers.keys())


def magic_for(ext: str) -> bytes | None:
    """插件为 ext 注册的 magic 签名；无（或该 ext 无注册）-> None。上传层校验用。"""
    entry = _parsers.get((ext or "").lower())
    return entry.magic if entry else None


def all_entries() -> list[ParserEntry]:
    """去重后的全部 entry（按首次注册顺序，GET /materials/formats 展示用）。"""
    seen: set[int] = set()
    out: list[ParserEntry] = []
    for entry in _parsers.values():
        if id(entry) in seen:
            continue
        seen.add(id(entry))
        out.append(entry)
    return out


def reset() -> None:
    """清空注册表（测试隔离 / reset_plugins 调用）。"""
    _parsers.clear()
