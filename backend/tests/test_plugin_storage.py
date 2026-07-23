"""后端插件 API v1.11.0 api.storage 持久化 KV 测试（API.md「§持久化 KV 存储」）。

覆盖：get/set/default、delete、has、keys、clear、原子持久化往返（新实例读盘）、
非 JSON 可序列化值拒绝、损坏文件容错（静默重置）、按插件 data_dir 命名空间隔离、键校验、门面接线。
"""

from __future__ import annotations

import pytest

from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


def _facade(data_dir: str) -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id="storagetest", name="storagetest", version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id="storagetest", data_dir=data_dir, status=status)


# --- 基本 KV ---


def test_set_get_default(tmp_path):
    api = _facade(str(tmp_path))
    assert api.storage.get("missing") is None
    assert api.storage.get("missing", "fallback") == "fallback"
    api.storage.set("k", {"n": 1, "list": [1, 2], "nested": {"a": True}})
    assert api.storage.get("k") == {"n": 1, "list": [1, 2], "nested": {"a": True}}


def test_has_keys_delete_clear(tmp_path):
    api = _facade(str(tmp_path))
    api.storage.set("a", 1)
    api.storage.set("b", 2)
    assert api.storage.has("a") and not api.storage.has("z")
    assert set(api.storage.keys()) == {"a", "b"}
    assert api.storage.delete("a") is True  # 删除存在键
    assert api.storage.delete("a") is False  # 幂等：再次删返回 False
    assert api.storage.keys() == ["b"]
    api.storage.clear()
    assert api.storage.keys() == []


# --- 原子持久化往返（新实例从盘加载）---


def test_persists_across_instances(tmp_path):
    """set 后落盘（原子 tmp+replace）；新 StorageNamespace 实例读盘可见。"""
    _facade(str(tmp_path)).storage.set("persisted", ["x", "y"])
    api2 = _facade(str(tmp_path))  # 新实例，懒加载从 storage.json 读
    assert api2.storage.get("persisted") == ["x", "y"]


def test_overwrite_persists(tmp_path):
    api = _facade(str(tmp_path))
    api.storage.set("k", "v1")
    api.storage.set("k", "v2")  # 覆盖
    assert api.storage.get("k") == "v2"
    # 新实例亦见覆盖值
    assert _facade(str(tmp_path)).storage.get("k") == "v2"


# --- 健壮性 ---


def test_non_serializable_value_rejected(tmp_path):
    api = _facade(str(tmp_path))
    with pytest.raises(ValueError, match="JSON 可序列化"):
        api.storage.set("k", {1, 2, 3})  # set 非 JSON 可序列化


def test_corrupt_file_graceful_reset(tmp_path):
    """storage.json 损坏 -> 静默重置（不抛、keys 空），插件功能不中断。"""
    (tmp_path / "storage.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    api = _facade(str(tmp_path))
    assert api.storage.keys() == []  # 损坏容错：当作空
    api.storage.set("after_corrupt", 1)  # 仍可正常用（覆盖损坏文件）
    assert api.storage.get("after_corrupt") == 1


def test_namespacing_per_plugin(tmp_path):
    """不同插件（不同 data_dir）的 KV 互相隔离。"""
    a = tmp_path / "pluginA"
    b = tmp_path / "pluginB"
    _facade(str(a)).storage.set("shared_key", "A_value")
    _facade(str(b)).storage.set("shared_key", "B_value")
    assert _facade(str(a)).storage.get("shared_key") == "A_value"
    assert _facade(str(b)).storage.get("shared_key") == "B_value"


def test_key_validation(tmp_path):
    api = _facade(str(tmp_path))
    with pytest.raises(ValueError, match="key"):
        api.storage.set("", "v")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="key"):
        api.storage.get(123)  # type: ignore[arg-type]


# --- 门面接线 ---


def test_facade_exposes_storage(tmp_path):
    api = _facade(str(tmp_path))
    assert api.storage is not None
    for m in ("get", "set", "delete", "has", "keys", "clear"):
        assert callable(getattr(api.storage, m))
