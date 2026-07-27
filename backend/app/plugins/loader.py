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
import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.settings import settings
from app.plugins import bus, exporters, parsers, providers, runtime, scheduler
from app.plugins.api import API_VERSION, BackendPluginAPI

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("deep_tutor.plugins")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# 热重启支持：保留每个已加载插件的 API 门面（持有 _rollbacks 供 unload 调用）
_plugin_apis: dict[str, BackendPluginAPI] = {}

# iter 365 C11：unload/load/disable/enable 串行化——并发热管理（两 admin 同时 reload 同插件）
# 可竞态 _plugin_apis/_statuses（后写覆盖、重复台账、旧 API 未 rollback 残留监听器）。
# RLock 可重入：reload->unload、disable->unload 同线程内调用安全。
_management_lock = threading.RLock()

# 禁用状态持久化（重启后保持）；存 data/plugin_state.json
_disabled_ids: set[str] = set()
_DISABLED_FILE = "plugin_state.json"


def _load_disabled() -> None:
    """从 data/plugin_state.json 加载禁用插件 id 集合。"""
    global _disabled_ids
    path = Path(settings.data_dir) / _DISABLED_FILE
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = data.get("disabled", [])
            _disabled_ids = {str(i) for i in ids} if isinstance(ids, list) else set()
    except (OSError, ValueError):
        _disabled_ids = set()


def _save_disabled() -> None:
    """持久化禁用插件 id 集合到 data/plugin_state.json。"""
    path = Path(settings.data_dir) / _DISABLED_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"disabled": sorted(_disabled_ids)}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 持久化失败不影响功能（仅重启后丢失禁用状态）


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
    _load_disabled()  # 加载禁用状态
    for path in _iter_plugin_files(root):
        _load_one(app, path)  # 禁用插件由 _load_one 记 disabled 状态
    statuses = runtime.statuses()
    loaded = sum(1 for s in statuses if s.state == "loaded")
    failed = sum(1 for s in statuses if s.state == "failed")
    log.info("插件加载完成：loaded=%d failed=%d（目录 %s）", loaded, failed, root)
    return statuses


def _peek_plugin_id(path: Path) -> str | None:
    """快速探测插件 id（不完整加载——仅读 PLUGIN_META）。"""
    fallback = _fallback_id(path)
    try:
        # 用 ast 安全读取 PLUGIN_META（不执行插件代码）
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == "PLUGIN_META":
                    if isinstance(node.value, ast.Dict):
                        for key, val in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Constant) and key.value == "id" and isinstance(val, ast.Constant):
                                return str(val.value)
                    return fallback  # 有 PLUGIN_META 但解析不出 id -> 用 fallback
    except Exception:  # noqa: BLE001
        pass
    return fallback


def _load_one(app: FastAPI, path: Path) -> runtime.PluginStatus | None:
    fallback = _fallback_id(path)
    # 启动时跳过被禁用的插件
    if fallback in _disabled_ids or _peek_plugin_id(path) in _disabled_ids:
        pid = _peek_plugin_id(path) or fallback
        status = runtime.PluginStatus(
            id=pid, name=pid, version="", file=str(path), state="disabled",
        )
        runtime.record(status)
        log.info("插件 %s 已禁用，跳过加载", pid)
        return status
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
        # iter 247：回滚其失败前已完成的注册——此前仅记 failed，但路由仍在响应、
        # 事件监听器仍触发、工具仍汇入注册表，状态台账与真实运行态不一致
        api._rollback()
        status.routes.clear()
        status.events.clear()
        status.tools.clear()
        status.parsers.clear()
        status.exporters.clear()
        status.error = f"register: {exc}（其已注册的路由/事件/工具/解析器/导出器已回滚）"
        runtime.record(status)
        return
    status.state = "loaded"
    _plugin_apis[pid] = api  # 保留供 unload/reload 使用
    runtime.record(status)
    log.info("插件已加载：%s %s <- %s", pid, status.version or "(未标版本)", path)
    return status


def unload_plugin(pid: str) -> bool:
    """热卸载一个插件：调用其 _rollback() 逆序撤销全部注册（路由/事件/工具/解析器/导出器/Provider/scheduler/webhook/middleware），
    从 _plugin_apis + _statuses 移除。返回是否确实卸载了一个插件。"""
    with _management_lock:  # iter 365 C11：串行化热管理
        api = _plugin_apis.pop(pid, None)
        if api is None:
            return False
        api._rollback()
        runtime.remove_status(pid)
        log.info("插件已卸载（热）：%s", pid)
        return True


def reload_plugin(app: FastAPI, pid: str) -> runtime.PluginStatus | None:
    """热重载一个插件：卸载 + 重新导入 + 重新注册。返回新状态；插件不存在返回 None。"""
    with _management_lock:  # iter 365 C11：串行化热管理
        # 找到文件路径（从已有状态或扫描目录）
        path = _find_plugin_file(pid)
        if path is None:
            return None
        unload_plugin(pid)  # RLock 可重入：reload 持锁调 unload 安全
        return _load_one(app, path)


def disable_plugin(pid: str) -> bool:
    """禁用一个插件：卸载（若已加载）+ 记 disabled 状态 + 持久化。返回是否操作了真实插件。

    仅对真实插件生效（已加载，或文件存在如启动时跳过的禁用插件）；未知 id 返回 False
    （端点据此 404，与 reload/enable 一致）。
    """
    with _management_lock:  # iter 365 C11：串行化热管理
        # 先捕获文件路径——unload_plugin 会移除状态台账，之后读不到 file，enable 会找不到文件
        file = next((s.file for s in runtime.statuses() if s.id == pid), "")
        if pid in _plugin_apis:
            unload_plugin(pid)  # RLock 可重入
        else:
            # 未加载：须能定位到文件（如启动时跳过的禁用插件），否则视为不存在
            if not file:
                found = _find_plugin_file(pid)
                if found is None:
                    return False
                file = str(found)
        # 记 disabled 状态（台账可见，可从插件页重新启用；file 供 enable 定位）
        runtime.remove_status(pid)
        runtime.record(runtime.PluginStatus(id=pid, name=pid, version="", file=file, state="disabled"))
        _disabled_ids.add(pid)
        _save_disabled()
        log.info("插件已禁用：%s", pid)
        return True


def enable_plugin(app: FastAPI, pid: str) -> runtime.PluginStatus | None:
    """启用一个被禁用的插件：移除禁用标记 + 从文件重新加载。返回新状态；文件不存在返回 None。"""
    with _management_lock:  # iter 365 C11：串行化热管理
        _disabled_ids.discard(pid)
        _save_disabled()
        path = _find_plugin_file(pid)
        if path is None:
            return None
        runtime.remove_status(pid)  # 清除旧的 disabled 状态记录
        return _load_one(app, path)


def _find_plugin_file(pid: str) -> Path | None:
    """从已有状态或扫描插件目录找到 pid 对应的文件路径。"""
    # 先从已有状态查
    for s in runtime.statuses():
        if s.id == pid and s.file:
            p = Path(s.file)
            if p.is_file():
                return p
    # 扫描目录
    root = Path(settings.plugins_dir)
    if not root.is_dir():
        return None
    for path in _iter_plugin_files(root):
        if _peek_plugin_id(path) == pid or _fallback_id(path) == pid:
            return path
    return None

def get_plugin_statuses() -> list[runtime.PluginStatus]:
    """当前加载状态台账（GET /api/plugins 数据源）。"""
    return runtime.statuses()


def reset_plugins() -> None:
    """清空状态台账/工具表/事件订阅/解析器/导出器/LLM Provider 注册表/调度任务/API 实例/禁用状态（测试隔离用；已挂载的路由随 app 实例存在）。"""
    runtime.reset_runtime()
    bus.reset()
    parsers.reset()
    exporters.reset()
    providers.reset()
    scheduler.reset()
    _plugin_apis.clear()
    _disabled_ids.clear()
