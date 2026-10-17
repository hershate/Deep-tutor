"""插件自定义 LLM Provider 注册表（后端插件 API v1.6.0 扩展点）。

契约见 API.md「后端插件 API §LLM Provider 扩展点」。用途：插件为内置仅支持 Anthropic
协议的 LLM 层注册**新协议 Provider**（OpenAI /v1/chat/completions、Ollama、DeepSeek 原生、
Gemini 等），经 api.llm.complete(provider_kind=...) 调用——带宿主日预算守卫 + api_usage
计量（与宿主端点同口径），无需让插件 DIY httpx 绕开预算/计量（"暗花费"）。

设计要点（与 parsers/exporters 扩展点同范式）：
- 注册表为 kind(小写) -> ProviderEntry 的映射；同一 kind 后注册覆盖前者（记 warn）。
- "anthropic" 为内置协议保留名——find_provider("anthropic") 恒返回 None（内置 Anthropic
  Provider 处理），插件不得注册该名（注册时拒绝）。
- handler 契约：async 或 sync (system, user, *, model, max_tokens) -> dict，返回
  {content, model, stop_reason?, usage:{input_tokens, output_tokens, ...}}。async 直接 await；
  sync 在线程池执行（不阻塞事件循环，与内置同步路径经 to_thread 同口径）。
- 凭证由插件自理：handler 不接收 api_key/base_url（宿主不知新协议的凭证模型）——插件从
  api.data_dir / 环境变量 / 自有设置读写其凭证。这是"非侵入式 v1"的取舍：用户多 LLM 平台的
  key 在插件处配置；将 provider 接入宿主供应商档案（用户在设置页统一配 key + 选协议）属
  下一轮的 profile 集成（需 DB 迁移 + get_provider 分发，独立聚焦轮）。

隔离：模块级状态；测试经 reset() 清空（reset_plugins 调用，conftest isolate 夹具每测试前触发）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("deep_tutor.plugins.providers")

# 内置协议名（保留）：Anthropic Messages API 由 app/llm/anthropic_provider.py 处理。
# 插件注册新协议时不得占用此名（register 拒绝）。
KIND_ANTHROPIC = "anthropic"

# kind 命名字符集：小写字母开头，字母数字-_（与 exporters 格式名同口径，URL/标识友好）
_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def normalize_kind(kind: str) -> str:
    """规范化 kind 为小写（"OpenAI" / "openai-compatible" -> 后者原样小写）。"""
    kind = (kind or "").strip().lower()
    if not kind:
        raise ValueError("provider kind 不能为空")
    return kind


def valid_kind_name(kind: str) -> bool:
    """kind 合法性：匹配 [a-z0-9][a-z0-9_-]{0,31}（normalize 后校验）。"""
    return bool(_KIND_RE.match(kind))


@dataclass
class ProviderEntry:
    """单个插件 LLM Provider 注册项。"""

    plugin_id: str
    kind: str  # 已规范化（小写，合法字符集）
    handler: Callable  # (system, user, *, model, max_tokens) -> dict；async 或 sync
    default_model: str = ""  # 可选：该 kind 的默认模型名（api.llm 未显式传 model 时用）
    label: str = ""  # 可选：人类可读协议名（GET /providers/kinds 展示用）


# kind -> entry；同一 kind 后注册覆盖前者
_providers: dict[str, ProviderEntry] = {}
# iter 369：kind -> 被覆盖的前任栈——覆盖者卸载时回填前任（此前直接删键，前任插件仍
# loaded 但 find_provider 返回 None；providers 侧更敏感：用户档案 kind 的 api_key
# 送达对象随装载/卸载顺序漂移）
_displaced: dict[str, list[ProviderEntry]] = {}


def register(entry: ProviderEntry) -> None:
    """注册 Provider。kind 为内置保留名 -> 拒绝（ValueError）；同 kind 后注册覆盖前者（warn）。"""
    if entry.kind == KIND_ANTHROPIC:
        raise ValueError(
            f"provider kind {KIND_ANTHROPIC!r} 为内置保留（Anthropic Messages API），"
            "插件请注册新协议名（如 'openai'、'ollama'）"
        )
    existing = _providers.get(entry.kind)
    if existing is not None and existing is not entry:
        log.warning(
            "插件 Provider 覆盖既有注册: %s（原 %s <- 新 %s）",
            entry.kind,
            existing.plugin_id,
            entry.plugin_id,
        )
        _displaced.setdefault(entry.kind, []).append(existing)
    _providers[entry.kind] = entry


def unregister(entry: ProviderEntry) -> None:
    """移除该 entry 名下的 kind（loader register 失败回滚/热卸载用；按对象身份匹配）。

    iter 369：该 kind 存在被覆盖的前任 -> 回填栈顶前任（覆盖者卸载不连坐被覆盖者）。"""
    for kind in [k for k, v in _providers.items() if v is entry]:
        stack = _displaced.get(kind)
        if stack:
            _providers[kind] = stack.pop()
            if not stack:
                _displaced.pop(kind, None)
        else:
            _providers.pop(kind, None)


def find_provider(kind: str) -> ProviderEntry | None:
    """返回该 kind 的 entry；未知 kind 或内置 'anthropic' -> None。"""
    if not kind:
        return None
    return _providers.get(kind.strip().lower())


def all_entries() -> list[ProviderEntry]:
    """全部 entry（按 kind 升序，GET /providers/kinds 展示用）。"""
    return sorted(_providers.values(), key=lambda e: e.kind)


def reset() -> None:
    """清空注册表（测试隔离 / reset_plugins 调用）。"""
    _providers.clear()
    _displaced.clear()
