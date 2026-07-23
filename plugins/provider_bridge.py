"""OpenAI 兼容 LLM Provider 桥（后端插件，复合 providers+llm+routes+storage）。

为内置仅支持 Anthropic 协议的 LLM 层注册 OpenAI 兼容协议（OpenAI / DeepSeek / Ollama / vLLM 等
任何 /v1/chat/completions 端点）——用户经插件即可接入非 Anthropic LLM，复用宿主日预算守卫 +
api_usage 计量（非 Anthropic 协议不再须 DIY httpx 绕开预算 / 计量的「暗花费」）。

实际用途：用户在插件配置自己的 OpenAI/Ollama key + base_url，插件的 /chat 即用新协议；
或经供应商档案 kind=openai 让方案生成主线也用新协议（宿主 get_provider 分发）。

复合 API（验证扩展点协同）：
- api.providers：注册 "openai" 协议 handler（OpenAI 兼容 /v1/chat/completions）
- api.llm：complete(provider_kind="openai") 调用（宿主日预算守卫 + api_usage 计量同口径）
- api.routes：PUT /config（管理员配 key+base_url+model）、GET /config、POST /chat
- api.storage：持久化凭证（api_key/base_url/model，响应不回显明文 key）

稳定性：凭证优先用供应商档案传入（api_key/base_url 非空），空则回退插件 storage 自有配置；
key 缺失 -> RuntimeError -> 宿主分类 bad_gateway(502)。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

PLUGIN_META = {
    "id": "provider_bridge",
    "name": "OpenAI 兼容 LLM Provider 桥",
    "version": "0.1.0",
    "description": "接入 OpenAI 兼容协议 LLM（providers+llm+routes+storage）",
    "min_api": "1.6",  # providers(1.6) + llm.complete(provider_kind) + routes + storage
}

_CFG_KEY = "openai_config"


class ConfigIn(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com"
    model: str = "gpt-4o"


class ChatIn(BaseModel):
    prompt: str
    system: str = "你是助教"


def register(api):
    def _load_cfg() -> dict:
        cfg = api.storage.get(_CFG_KEY, {})
        return cfg if isinstance(cfg, dict) else {}

    async def chat_openai(system, user, *, model, max_tokens, api_key="", base_url=""):
        """OpenAI 兼容协议 handler：凭证优先档案传入，空则回退插件 storage 配置。"""
        cfg = _load_cfg()
        key = api_key or cfg.get("api_key", "")
        url = (base_url or cfg.get("base_url") or "https://api.openai.com").rstrip("/")
        if not key:
            raise RuntimeError("OpenAI provider 未配置 api_key（PUT /config 或供应商档案）")
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model or cfg.get("model", "gpt-4o"),
                    "max_tokens": max_tokens,
                    "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
                },
            )
            r.raise_for_status()
            data = r.json()
        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data.get("model", model),
            "stop_reason": data["choices"][0].get("finish_reason"),
            "usage": {
                "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
            },
        }

    api.providers.register("openai", chat_openai, default_model="gpt-4o", label="OpenAI 兼容")

    router = APIRouter()

    @router.put("/config")
    def set_config(body: ConfigIn, user=Depends(api.deps.require_admin)):
        api.storage.set(
            _CFG_KEY, {"api_key": body.api_key, "base_url": body.base_url, "model": body.model}
        )
        return {"ok": True, "base_url": body.base_url, "model": body.model, "has_key": True}

    @router.get("/config")
    def get_config(user=Depends(api.deps.get_current_user)):
        cfg = _load_cfg()
        if not cfg.get("api_key"):
            return {"configured": False}
        return {
            "configured": True,
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "has_key": True,
        }

    @router.post("/chat")
    async def chat(body: ChatIn, user=Depends(api.deps.get_current_user)):
        resp = await api.llm.complete(
            user.id, body.prompt, system=body.system, max_tokens=512, provider_kind="openai"
        )
        return {"text": resp["content"], "model": resp["model"]}

    api.routes.mount(router)
    api.log.info("provider_bridge 已加载：OpenAI 兼容协议 + /config + /chat")
