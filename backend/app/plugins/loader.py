"""后端插件加载器（契约见 API.md「后端插件 API」§装载）。

装载约定：
- 扫描 PLUGINS_DIR（默认 <仓库根>/plugins，见 settings.plugins_dir）：
  - 单文件插件：`plugins/foo.py`
  - 包插件：`plugins/foo/plugin.py`（包内模块用相对导入，`from . import xxx`）
- 文件名/目录名以 `_` 开头 -> 跳过（临时停用的便捷约定）；
- 模块须定义 `register(api)`；可选 `PLUGIN_META = {"id","name","version","description","min_api"}`；
- 插件 id 作为路由命名空间（/api/plugins/<id>/），须匹配 [a-z0-9][a-z0-9._-]{0,63}；
  缺省取文件名/目录名；
- `min_api`（如 "1.0"）：插件要求的最低后端插件 API 版本；宿主主版本不同或
  次版本更低 -> 记 failed 不加载（插件可用 api.version 自行做更细判断）。

稳定性（与 skills/loader 同一范式）：单个插件导入/register 失败 -> 记 failed +
日志，绝不阻塞其余插件与宿主启动；重复 id -> 后者 failed。
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.settings import settings
from app.plugins import bus, runtime
from app.plugins.api import API_VERSION, BackendPluginAPI

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("deep_tutor.plugins")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _parse_version(raw: object) -> tuple[int, int, int] | None:
    """宽松解析 "1.2.3" / "1.2" / "1" -> (major, minor, patch)；无法解析返回 None。"""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(".")
    if not 1 <= len(parts) <= 3:
        return None
    nums: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _api_compatible(min_api: object) -> bool:
    """min_api 与宿主 API_VERSION 的兼容性：主版本须相同，次版本不超过宿主。"""
    want = _parse_version(min_api)
    if want is None:
        return True  # 未声明/无法解析 -> 不阻拦（插件可用 api.version 自行判断）
    host = _parse_version(API_VERSION)
    assert host is not None
    if want[0] != host[0]:
        return False
    return want[1] <= host[1]


def _iter_plugin_files(root: Path) -> list[Path]:
    """枚举插件入口文件（sorted 保证确定性）：单文件 + 包插件 plugin.py。"""
    singles = sorted(p for p in root.glob("*.py") if not p.name.startswith("_"))
    packages = sorted(
        (d / "plugin.py")
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "plugin.py").is_file()
    )
    return singles + packages


def _fallback_id(path: Path) -> str:
    return path.parent.name if path.name == "plugin.py" else path.stem


def _import_module(path: Path):
    """以独立模块名导入插件文件；包插件带 submodule_search_locations 支持相对导入。"""
    mod_name = f"dt_plugin_{_fallback_id(path)}"
    sys.modules.pop(mod_name, None)  # 重复加载（测试/重载）时以新模块对象为准
    if path.name == "plugin.py":
        spec = importlib.util.spec_from_file_location(
            mod_name, path, submodule_search_locations=[str(path.parent)]
        )
    else:
        spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为插件创建 import spec: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def load_plugins(app: FastAPI, plugins_dir: str | Path | None = None) -> list[runtime.PluginStatus]:
    """扫描并加载全部插件；返回加载状态台账（loaded/failed 均记录）。

    app 用于路由挂载（lifespan 传入）；目录缺失/为空 -> 空台账，不视为错误。
    """
    root = Path(plugins_dir or settings.plugins_dir)
    if not root.is_dir():
        log.info("插件目录不存在，跳过插件加载: %s", root)
        return []
    for path in _iter_plugin_files(root):
        _load_one(app, path)
    statuses = runtime.statuses()
    loaded = sum(1 for s in statuses if s.state == "loaded")
    failed = sum(1 for s in statuses if s.state == "failed")
    log.info("插件加载完成：loaded=%d failed=%d（目录 %s）", loaded, failed, root)
    return statuses


def _load_one(app: FastAPI, path: Path) -> None:
    fallback = _fallback_id(path)
    try:
        module = _import_module(path)
    except Exception as exc:  # noqa: BLE001  隔离：坏插件不阻塞宿主启动
        log.exception("插件导入失败 %s", path)
        runtime.record(
            runtime.PluginStatus(
                id=fallback, name=fallback, version="", file=str(path),
                state="failed", error=f"import: {exc}",
            )
        )
        return

    meta = getattr(module, "PLUGIN_META", None)
    meta = meta if isinstance(meta, dict) else {}
    pid = str(meta.get("id") or fallback)
    status = runtime.PluginStatus(
        id=pid,
        name=str(meta.get("name") or pid),
        version=str(meta.get("version") or ""),
        file=str(path),
        state="failed",
    )
    if not _ID_RE.match(pid):
        status.error = f"插件 id 非法: {pid!r}（需匹配 [a-z0-9][a-z0-9._-]{{0,63}}）"
    elif any(s.id == pid for s in runtime.statuses()):
        status.error = f"插件 id 重复: {pid}"
    elif not _api_compatible(meta.get("min_api")):
        status.error = f"min_api {meta.get('min_api')!r} 与宿主插件 API {API_VERSION} 不兼容"
    elif not callable(getattr(module, "register", None)):
        status.error = "缺少 register(api) 入口函数"
    if status.error:
        log.error("插件 %s 加载失败：%s（%s）", pid, status.error, path)
        runtime.record(status)
        return

    data_dir = Path(settings.data_dir) / "plugins" / pid
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        status.error = f"插件数据目录创建失败: {exc}"
        log.error("插件 %s 加载失败：%s", pid, status.error)
        runtime.record(status)
        return

    api = BackendPluginAPI(app=app, plugin_id=pid, data_dir=data_dir, status=status)
    try:
        module.register(api)
    except Exception as exc:  # noqa: BLE001  隔离：register 异常不阻塞宿主
        log.exception("插件 %s register(api) 执行失败", pid)
        status.error = f"register: {exc}"
        runtime.record(status)
        return
    status.state = "loaded"
    runtime.record(status)
    log.info("插件已加载：%s %s <- %s", pid, status.version or "(未标版本)", path)


def get_plugin_statuses() -> list[runtime.PluginStatus]:
    """当前加载状态台账（GET /api/plugins 数据源）。"""
    return runtime.statuses()


def reset_plugins() -> None:
    """清空状态台账/工具表/事件订阅（测试隔离用；已挂载的路由随 app 实例存在）。"""
    runtime.reset_runtime()
    bus.reset()
