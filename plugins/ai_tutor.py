"""AI 学习助教（后端插件，复合 llm+routes+tools）。

为学习难点生成通俗解释/记忆类比（复用用户已配 LLM 供应商），并演示经 api.tools.call 组合宿主
核心工具（parse_material 文本分块）构建自定义工作流——插件无需重实现解析即可融入新流程。

实际用途：
- POST /explain：把生涩概念翻译成通俗类比 / 记忆口诀 / 小学生能懂的话（AI 助教）
- POST /breakdown：文本分块预览（演示插件调用核心 parse_material 工具，组合进自定义工作流）
- GET /status：探测当前用户是否已配 LLM 供应商（前端据此显隐 AI 入口）

复合 API（验证扩展点协同）：
- api.llm：complete 生成解释（复用用户供应商，日预算守卫 + api_usage 计量同宿主端点）
- api.tools：call 调用核心 parse_material（组合新工作流，无需重实现）
- api.routes：POST /explain / POST /breakdown / GET /status

稳定性：无供应商时 /explain 优雅降级返回 hint（不 503）；kind 入参白名单；text 截断防超长。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

PLUGIN_META = {
    "id": "ai_tutor",
    "name": "AI 学习助教",
    "version": "0.1.0",
    "description": "AI 解释 + tools.call 组合（llm+routes+tools）",
    "min_api": "1.7",  # llm(1.5) + tools.call(1.7) + routes(1.0)
}

_PROMPTS = {
    "analogy": "用日常生活的类比解释以下概念，200 字以内、通俗生动：\n",
    "mnemonic": "为以下内容编一个易记的口诀或记忆法（顺口溜/联想）：\n",
    "simplify": "用小学生也能听懂的话重新解释以下内容：\n",
}


class ExplainIn(BaseModel):
    text: str
    kind: str = "analogy"


class BreakdownIn(BaseModel):
    text: str


def register(api):
    router = APIRouter()

    @router.post("/explain")
    async def explain(body: ExplainIn, user=Depends(api.deps.get_current_user)):
        if not api.llm.is_configured(user.id):
            return {"configured": False, "hint": "请先在设置页配置 LLM 供应商后再用 AI 解释"}
        prefix = _PROMPTS.get(body.kind, _PROMPTS["analogy"])
        resp = await api.llm.complete(
            user.id,
            prefix + body.text[:2000],
            system="你是耐心友好的学习助教，回答简洁通俗，不编造。",
            max_tokens=400,
        )
        return {"configured": True, "kind": body.kind, "explanation": resp["content"], "model": resp["model"]}

    @router.post("/breakdown")
    async def breakdown(body: BreakdownIn, user=Depends(api.deps.get_current_user)):
        # 演示 tools.call 组合核心 parse_material（Python 工具，不需 provider，稳定可测）
        result = await api.tools.call(
            "parse_material", {"kind": "text", "source": body.text}, user_id=user.id
        )
        data = result.get("data") or {}
        meta = data.get("meta") or {}
        return {
            "status": result.get("status"),
            "chars": meta.get("chars", 0),
            "chunk_count": len(data.get("chunks") or []),
        }

    @router.get("/status")
    def status(user=Depends(api.deps.get_current_user)):
        return {"plugin": api.id, "configured": api.llm.is_configured(user.id)}

    api.routes.mount(router)
    api.log.info("ai_tutor 已加载：AI 解释（/explain）+ tools.call 组合（/breakdown）")
