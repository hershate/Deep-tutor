"""search_resources 工具（plan/02 §5，检索 Python + 筛选留 step 5 LLM）。

按阶段主题检索 Web 资源 + verify_url 校验 -> ✅⚠️❌ 标记。
- Bing Search API（BING_API_KEY，httpx 调用）-> 对每个候选调用 verify_url。
- 不编造 URL；无结果/无 key -> 返回空 + 关键词建议，不占位。
- 限额：每阶段 ≤5、总数 ≤40（09 §二十）。
- 内容适配性筛选（Skill=deep-tutor-resources）留 step 5：当前 verify_url 仅可达性，可达即 ok。
"""

from __future__ import annotations

import logging

import httpx

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.core.settings import settings
from app.tools.base import ToolError, ToolInput, ToolOutput
from app.tools.verify_url import verify_url

log = logging.getLogger("deep_tutor.tools.search")

MAX_PER_STAGE = 5
MAX_TOTAL = 40
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
SEARCH_TIMEOUT = 30.0


async def search_resources(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    stage_topics: list[list[str]] = inp.args.get("stage_topics") or []

    if not settings.bing_api_key:
        # 不编造 URL：返回空 + 关键词建议
        keywords = [t for topics in stage_topics for t in topics]
        return ToolOutput(
            tool="search_resources",
            status="repaired",
            data={"resources": [], "note": "未配置 BING_API_KEY，未执行检索；请人工补充资源", "suggested_keywords": keywords[:20]},
            errors=[ToolError(code="no_search_key", message="未配置 BING_API_KEY，search_resources 不可用")],
        )

    resources: list[dict] = []
    for stage_index, topics in enumerate(stage_topics):
        for topic in topics[:3]:  # 每阶段取前 3 个主题检索
            if len(resources) >= MAX_TOTAL:
                break
            try:
                candidates = await _bing_search(topic, settings.bing_api_key)
            except Exception as exc:  # noqa: BLE001  检索失败不中断
                log.warning("Bing 检索失败 topic=%s: %s", topic, exc)
                continue
            for cand in candidates:
                if sum(1 for r in resources if r["stage_index"] == stage_index) >= MAX_PER_STAGE:
                    break
                if len(resources) >= MAX_TOTAL:
                    break
                v_inp = ToolInput(
                    tool="verify_url",
                    session_id=inp.session_id,
                    args={"url": cand.get("url", ""), "stage_level": 1},
                )
                v_out = await verify_url(v_inp, ctx)
                vdata = v_out.data or {}
                if vdata.get("label") == "invalid":
                    continue  # 失效换源
                resources.append(
                    {
                        "stage_index": stage_index,
                        "title": cand.get("title", ""),
                        "url": vdata.get("final_url", cand.get("url", "")),
                        "type": "doc",
                        "verified": vdata.get("label", "uncertain"),
                        "note": "",
                    }
                )

    return ToolOutput(tool="search_resources", status="ok", data={"resources": resources})


async def _bing_search(query: str, api_key: str, count: int = 5) -> list[dict]:
    """调用 Bing Web Search API，返回 [{title, url}]。"""
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "count": count}
    async with httpx.AsyncClient(timeout=httpx.Timeout(SEARCH_TIMEOUT)) as client:
        resp = await client.get(BING_ENDPOINT, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
    web_pages = (data.get("webPages") or {}).get("value") or []
    return [{"title": w.get("name", ""), "url": w.get("url", "")} for w in web_pages]


MAX_PER_UNIT = 3  # 单单元补充资料上限（iter 48，plan/07 §三 步骤 2）


async def search_unit_resources(query: str, ctx: ExecContext, limit: int = MAX_PER_UNIT) -> list[dict]:
    """单自学单元 WebSearch 补充资料（plan/07 §三 步骤 2「每单元 WebSearch 补充资料」，iter 48）。

    复用 `_bing_search` + `verify_url`（同 search_resources 主流程）：Bing 候选 -> 逐个校验，
    丢弃 invalid，保留 ≤limit 条 {title,url,verified,note}。
    无 BING_API_KEY / 空 query / 检索失败 / 无结果 -> []（不编造 URL；内容侧由 LLM 标「无外部资料参考」）。
    """
    if not settings.bing_api_key or not query.strip():
        return []
    try:
        candidates = await _bing_search(query, settings.bing_api_key, count=limit)
    except Exception as exc:  # noqa: BLE001  检索失败不中断单元生成
        log.warning("单元补充资料检索失败 query=%s: %s", query, exc)
        return []
    resources: list[dict] = []
    for cand in candidates:
        if len(resources) >= limit:
            break
        v_inp = ToolInput(
            tool="verify_url",
            session_id=ctx.session_id,
            args={"url": cand.get("url", ""), "stage_level": 1},
        )
        try:
            v_out = await verify_url(v_inp, ctx)
        except Exception as exc:  # noqa: BLE001  单候选校验失败跳过
            log.warning("单元补充资料校验失败 url=%s: %s", cand.get("url", ""), exc)
            continue
        vdata = v_out.data or {}
        if vdata.get("label") == "invalid":
            continue  # 失效换源（同主流程）
        resources.append(
            {
                "title": cand.get("title", ""),
                "url": vdata.get("final_url", cand.get("url", "")),
                "verified": vdata.get("label", "uncertain"),
                "note": "",
            }
        )
    return resources


search_resources_tool = Tool(name="search_resources", llm=False, func=search_resources)
