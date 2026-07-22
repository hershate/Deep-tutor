"""插件自定义导出格式注册表（后端插件 API v1.4.0 扩展点）。

契约见 API.md「后端插件 API §导出格式扩展点」。用途：插件为内置导出（md/pdf/slides/
ics/anki）不支持的格式（私有格式/小众格式，如 .epub 电子书、Anki 以外的卡片格式、
企业归档格式）注册导出器，POST /api/export/{plan_id} 的 format 校验与分发链路自动
识别并委托——无需改宿主代码即可"让后端支持新导出格式"（与 api.parsers 对称的出口侧）。

设计要点：
- 注册表为 format(小写) -> ExporterEntry 的映射；同一 format 后注册覆盖前者（记 warn）。
- 内置优先（安全默认）：find_exporter(fmt) 对内置格式默认返回 None（内置处理），
  除非插件显式 override=True（启动告警，属高级用法）。
- renderer 契约：async 或 sync (plan: dict, ctx: dict) -> dict。async 直接 await；
  sync 在线程池执行（不阻塞事件循环，与内置同步导出经 to_thread 运行同一口径）。
  返回 dict 须含 filename，且含 content(str) 或 data(bytes) 其一，详见 api.py 门面注释。
- 端点层经 BUILTIN_FORMATS/find_exporter() 做 format 校验（替代原 pydantic Literal，
  插件格式不再被 422 拦截）。

隔离：模块级状态；测试经 reset() 清空（reset_plugins 调用，conftest isolate 夹具每测试前触发）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("deep_tutor.plugins.exporters")

# 内置导出格式（exports.py 端点分发）。插件对这些 format 注册默认不生效（内置优先）。
BUILTIN_FORMATS: frozenset[str] = frozenset({"md", "pdf", "slides", "ics", "anki"})

# format 字符集：小写字母数字开头，允许 - _（出现在响应 JSON 与 Content-Disposition 文件名，
# 严格白名单防注入；与插件 id 同款收敛思路）
_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# 插件返回二进制产物（data/bytes）的大小上限——防失控插件把巨型二进制塞进 JSON 响应
# （base64 再膨胀 ~1.33x）。32MiB 对文档/电子书类导出足够宽松。
MAX_BYTES = 32 * 1024 * 1024


def normalize_format(fmt: str) -> str:
    """规范化格式名为小写去空白（" EPUB " -> "epub"）；空串抛 ValueError。"""
    fmt = (fmt or "").strip().lower()
    if not fmt:
        raise ValueError("格式名不能为空")
    return fmt


@dataclass
class ExporterEntry:
    """单个插件导出器注册项。"""

    plugin_id: str
    format: str  # 已规范化（小写）
    handler: Callable  # (plan: dict, ctx: dict) -> dict；async 或 sync
    override: bool = False  # True = 即便对内置格式也接管（高级，启动告警）
    label: str = ""  # 可选：人类可读格式名（GET /export/formats 展示用）


_exporters: dict[str, ExporterEntry] = {}


def register(entry: ExporterEntry) -> None:
    """注册导出器。同一 format 后注册覆盖前者；对内置 format（未 override）注册记 warn 提示不生效。"""
    fmt = entry.format
    existing = _exporters.get(fmt)
    if existing is not None and existing is not entry:
        log.warning(
            "插件导出器覆盖既有注册: %s（原 %s <- 新 %s）", fmt, existing.plugin_id, entry.plugin_id
        )
    if not entry.override and fmt in BUILTIN_FORMATS:
        log.warning(
            "插件 %s 为内置导出格式 %s 注册导出器但未 override=True -> 不会生效（内置优先）；"
            "如确需覆盖内置导出，请 override=True 并充分测试",
            entry.plugin_id,
            fmt,
        )
    _exporters[fmt] = entry


def unregister(entry: ExporterEntry) -> None:
    """移除该 entry 的注册（loader register 失败回滚用；按对象身份匹配）。"""
    for fmt in [k for k, v in _exporters.items() if v is entry]:
        _exporters.pop(fmt, None)


def find_exporter(fmt: str) -> ExporterEntry | None:
    """返回应处理 fmt 的 entry（内置优先，override 例外）；无合适插件导出器 -> None。

    exports.py 端点据此决定是否委托：None -> 走内置分发（零回归）。
    """
    if not fmt:
        return None
    fmt = fmt.lower()
    entry = _exporters.get(fmt)
    if entry is None:
        return None
    if not entry.override and fmt in BUILTIN_FORMATS:
        return None  # 内置接管（安全默认）
    return entry


def registered_formats() -> set[str]:
    """全部插件注册的 format（端点校验/发现用；含 override 的内置 format）。"""
    return set(_exporters.keys())


def all_entries() -> list[ExporterEntry]:
    """全部 entry（按首次注册顺序，GET /export/formats 展示用）。"""
    return list(_exporters.values())


def valid_format_name(fmt: str) -> bool:
    """注册时校验 format 字符集（白名单）。"""
    return bool(_FORMAT_RE.match(fmt))


def reset() -> None:
    """清空注册表（测试隔离 / reset_plugins 调用）。"""
    _exporters.clear()
