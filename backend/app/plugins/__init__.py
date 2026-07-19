"""后端插件系统（契约见仓库根 API.md「后端插件 API」）。

- loader：PLUGINS_DIR 目录自动发现 + 隔离加载（坏插件不阻塞宿主）
- api：插件门面 BackendPluginAPI（tools/routes/events/settings/deps/data_dir/log）
- bus：事件总线（核心事件 + 插件自定义事件，监听器异常隔离）
- runtime：加载状态台账 + 插件工具表
"""

from app.plugins.api import API_VERSION, BackendPluginAPI
from app.plugins.loader import get_plugin_statuses, load_plugins, reset_plugins

__all__ = [
    "API_VERSION",
    "BackendPluginAPI",
    "load_plugins",
    "get_plugin_statuses",
    "reset_plugins",
]
