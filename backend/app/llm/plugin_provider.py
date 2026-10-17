"""插件自定义 LLM Provider 包装器（后端插件 API v1.6.0 扩展点）。

把 app/plugins/providers.py 注册的插件 handler 适配为 LLMProvider 接口——使 api.llm 与
宿主供应商档案（iter 313 profile 集成）能像 AnthropicProvider 一样透明调用新协议 Provider。

handler 契约（async 或 sync，签名相同）：
    (system: str, user: str, *, model: str, max_tokens: int,
     api_key: str, base_url: str) -> dict
返回 dict：
    {
      "content": str,                 # 必填：补全文本
      "model": str,                   # 必填：实际所用模型名
      "stop_reason": str | None,      # 可选
      "usage": {                      # 必填（成本计量用）
        "input_tokens": int,          # 必填
        "output_tokens": int,         # 必填
        "cache_read_input_tokens"?: int,        # 可选，缺省 0
        "cache_creation_input_tokens"?: int,    # 可选，缺省 0
      },
    }
凭证（api_key/base_url）：
- profile 集成路径（get_provider 分发）：来自用户供应商档案（用户在设置页统一配 key + 选协议）。
- api.llm.complete(provider_kind=) 路径：无档案上下文 -> 传空串，插件 handler 据此回退
  自有凭证（api.data_dir/环境变量）。即：api_key/base_url 非空时用之，空时插件自理。

错误约定：handler 抛任何异常 -> 经 _classify 映射——插件可在异常上设 transient=True
（或抛常见网络瞬态类型）标记为 TransientLLMError（宿主退避重试）；其余原样抛（api.llm
映射为 bad_gateway，pipeline 走工具错误修复/降级）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.llm.base import LLMProvider, LLMResponse, LLMUsage, TransientLLMError
from app.llm.pricing import estimate_cost
from app.plugins.providers import ProviderEntry

log = logging.getLogger("deep_tutor.llm.plugin_provider")

# iter 369：插件 handler 宿主级执行上限（秒）——scheduler 300s、webhook 10s、Anthropic
# SDK read 300s，唯独插件 Provider 此前无界（悬挂 handler 吊死请求与生成主线）；
# 对齐 scheduler 的 300s 量级
_HANDLER_TIMEOUT_S = 300.0


class PluginProviderError(Exception):
    """插件 Provider handler 抛出的非瞬态错误 / 返回契约不合规。"""


def _classify(exc: BaseException) -> Exception:
    """把 handler 异常映射为瞬态/非瞬态。

    约定（插件无需 import 宿主内部）：在异常实例上设 ``transient = True`` 即标记瞬态
    （限流/5xx/连接失败），宿主据此退避重试。另对常见网络库异常类型名做特征探测
    （ConnectError/TimeoutException/APIConnectionError/RateLimitError 或消息含 timeout），
    避免硬依赖具体 SDK。其余原样返回（非瞬态）。
    """
    if getattr(exc, "transient", False):
        return TransientLLMError(str(exc) or exc.__class__.__name__)
    name = type(exc).__name__
    msg = str(exc).lower()
    if (
        name in {"ConnectError", "TimeoutException", "ReadTimeout", "APIConnectionError", "RateLimitError"}
        or "timeout" in msg
        or "temporarily" in msg
    ):
        return TransientLLMError(str(exc) or name)
    if isinstance(exc, Exception):
        return exc
    return PluginProviderError(str(exc) or exc.__class__.__name__)


def _require(d: Any, key: str, *, container: str = "") -> Any:
    if not isinstance(d, dict) or key not in d:
        raise PluginProviderError(f"插件 Provider 返回缺字段 {container}{key!r}")
    return d[key]


def _as_int(v: Any, key: str, *, container: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise PluginProviderError(f"插件 Provider 返回 {container}{key!r} 非整数: {v!r}") from exc


class PluginProvider(LLMProvider):
    """把插件 handler 适配为 LLMProvider。"""

    def __init__(self, entry: ProviderEntry, *, model: str, api_key: str = "", base_url: str = "") -> None:
        self._entry = entry
        self._model = model or entry.default_model
        self._api_key = api_key or ""
        self._base_url = base_url or ""
        self.kind = entry.kind  # iter 370：计量归属（api_usage.provider 不再一律 anthropic）

    async def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> LLMResponse:
        used_model = model or self._model or ""
        handler = self._entry.handler
        try:
            # iter 369：宿主级执行上限——插件 handler 无契约强制超时，悬挂（无超时的外部
            # 调用/死循环 await）会吊死请求与生成主线（经供应商档案 kind 分发后该用户整条
            # 管线无限悬挂）；超时映射 TransientLLMError（可重试语义）
            coro = (
                handler(
                    system,
                    user,
                    model=used_model,
                    max_tokens=max_tokens,
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
                if asyncio.iscoroutinefunction(handler)
                else asyncio.to_thread(
                    handler,
                    system,
                    user,
                    model=used_model,
                    max_tokens=max_tokens,
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
            )
            raw = await asyncio.wait_for(coro, timeout=_HANDLER_TIMEOUT_S)
        except TimeoutError:
            raise TransientLLMError(
                f"插件 Provider handler 执行超时（>{_HANDLER_TIMEOUT_S:.0f}s，kind={self._entry.kind}）"
            ) from None
        except Exception as exc:  # noqa: BLE001  handler 任意异常 -> 分类映射
            raise _classify(exc) from exc

        content = _require(raw, "content")
        if not isinstance(content, str):
            raise PluginProviderError(f"插件 Provider 返回 content 非字符串: {type(content).__name__}")
        resp_model = _require(raw, "model")
        if not isinstance(resp_model, str) or not resp_model:
            resp_model = used_model
        usage_raw = _require(raw, "usage", container=".")
        usage = LLMUsage(
            input_tokens=max(
                0, _as_int(_require(usage_raw, "input_tokens", container="usage."), "input_tokens", container="usage.")
            ),
            output_tokens=max(
                0,
                _as_int(_require(usage_raw, "output_tokens", container="usage."), "output_tokens", container="usage."),
            ),
            cache_read_input_tokens=max(
                0, _as_int(usage_raw.get("cache_read_input_tokens", 0), "cache_read_input_tokens", container="usage.")
            ),
            cache_creation_input_tokens=max(
                0,
                _as_int(
                    usage_raw.get("cache_creation_input_tokens", 0), "cache_creation_input_tokens", container="usage."
                ),
            ),
            model=resp_model,
        )
        stop_reason = raw.get("stop_reason") if isinstance(raw, dict) else None
        return LLMResponse(
            content=content, usage=usage, stop_reason=stop_reason if isinstance(stop_reason, str) else None
        )

    def estimate_cost(self, usage: LLMUsage) -> float:
        # 复用内置价目表：已知模型族计价，未知（多数新协议模型）-> 0（用户用量页可见、不计费）
        return estimate_cost(usage, usage.model or self._model or "")
