"""search_resources 工具（plan/02 §5，检索 Python + LLM 内容适配性筛选 iter 62）。

按阶段主题检索 Web 资源 + verify_url 校验 -> ✅⚠️❌ 标记。
- Bing Search API（BING_API_KEY，httpx 调用）-> 对每个候选调用 verify_url。
- 不编造 URL；无结果/无 key -> 返回空 + 关键词建议，不占位。
- 限额：每阶段 ≤5、总数 ≤40（09 §二十）。
- 内容适配性筛选（iter 62，Skill=deep-tutor-resources）：verify 可达后，一次批量 LLM 调用
  判定候选与阶段主题/学习者水平的适配性——不相关丢弃、存疑降 ⚠️、附 note；
  无 provider / LLM 或解析失败 -> 跳过筛选（行为同可达即 ok，稳定降级）。
"""

from __future__ import annotations

import json
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

    resources = await _screen_resources(resources, inp, ctx)  # LLM 内容适配性筛选（iter 62）
    return ToolOutput(tool="search_resources", status="ok", data={"resources": resources})


# --- LLM 内容适配性筛选（iter 62，plan/02 §5：✅ = HTTP 200 + 内容难度适配该阶段）---

SCREEN_MAX_TOKENS = 2000  # ≤40 候选的判定 JSON 上限足够

# inline 回退 prompt（R13：skills/deep-tutor-resources 缺失时用；两者 schema 必须一致）
SCREEN_PROMPT = """你是 Deep-tutor 的「资源内容适配性筛选」工具。候选资源已通过可达性校验（HTTP 200），你的任务：判定每个候选与对应学习阶段主题、学习者水平的内容适配性。

输入（user message JSON）：{"topic":"学习主题","level":"学习者水平","stages":["阶段名",...],"candidates":[{"stage_index":0,"title":"...","url":"..."}]}

仅返回 JSON 数组（不要 markdown 代码块、不要解释），每候选一项：
[{"url":"候选 url 原文","suitable":true,"label":"ok|uncertain","note":"一句话说明"}]

规则：
- suitable=false：内容与该阶段主题不相关（如主题讲 Python 基础，候选是 Java 框架）——该候选将被丢弃。相关即 true，宁宽勿漏。
- label="ok"：内容相关且难度/深度适配该阶段（初级阶段配入门教程）。
- label="uncertain"：相关但难度/质量不确定（明显过深、内容陈旧、清单水文等）。
- note：中文一句话（≤40 字），uncertain 时说明原因；ok 时可留空。
- 必须覆盖 candidates 中的每个 url，url 原样返回（不改写、不编造）。
- 不访问 URL 正文，仅依据标题与常识判断；拿不准一律 uncertain，不臆断 ok。

仅返回符合上述格式的 JSON 数组。
"""


def _parse_screen_verdicts(text: str) -> dict[str, dict] | None:
    """解析筛选 LLM 输出 -> {url: {suitable,label,note}}；整体结构非法 -> None（跳过筛选）。

    容忍 markdown 代码块包裹；单项非法跳过该项（不影响其余）。
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(s[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    verdicts: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        suitable = item.get("suitable")
        if not isinstance(url, str) or not url or not isinstance(suitable, bool):
            continue
        label = item.get("label") if item.get("label") in ("ok", "uncertain") else "uncertain"
        note = item.get("note") if isinstance(item.get("note"), str) else ""
        verdicts[url] = {"suitable": suitable, "label": label, "note": note}
    return verdicts


async def _screen_resources(resources: list[dict], inp: ToolInput, ctx: ExecContext) -> list[dict]:
    """LLM 内容适配性筛选（一次批量调用，成本有界）：不相关丢弃、存疑降 ⚠️、附 note。

    降级（稳定优先）：无 provider / 无候选 / LLM 异常 / 输出不可解析 -> 原样返回
    （行为同筛选前：verify 可达即保留原标记）。LLM 响应未覆盖的 url 保持原判定。
    label 只降不升（verify 已 ⚠️ 的候选不因 LLM 说 ok 而升 ✅）。
    """
    provider = ctx.llm_provider
    if provider is None or not resources:
        return resources

    intent = ((inp.upstream or {}).get("understand_intent") or {}).get("data") or {}
    stages_data = ((inp.upstream or {}).get("design_stages") or {}).get("data") or {}
    stage_names = [str(s.get("name", "")) for s in (stages_data.get("stages") or []) if isinstance(s, dict)]
    payload = {
        "topic": intent.get("topic", ""),
        "level": intent.get("level", "") or "",
        "stages": stage_names,
        "candidates": [
            {"stage_index": r["stage_index"], "title": r["title"], "url": r["url"]} for r in resources
        ],
    }
    system = (ctx.skill_registry or {}).get("deep-tutor-resources") or SCREEN_PROMPT
    try:
        resp = await provider.chat(
            system,
            json.dumps(payload, ensure_ascii=False),
            max_tokens=SCREEN_MAX_TOKENS,
            cache_system=True,
        )
        verdicts = _parse_screen_verdicts(resp.content)
    except Exception as exc:  # noqa: BLE001  筛选失败不阻塞：保持原判定
        log.warning("资源内容适配筛选失败，跳过筛选: %s", exc)
        return resources
    if verdicts is None:
        log.warning("资源内容适配筛选输出不可解析，跳过筛选")
        return resources

    screened: list[dict] = []
    for r in resources:
        v = verdicts.get(r["url"])
        if v is None:
            screened.append(r)  # 响应未覆盖 -> 保持原判定
            continue
        if not v["suitable"]:
            continue  # 内容不相关 -> 丢弃
        r2 = dict(r)
        if v["label"] == "uncertain":
            r2["verified"] = "uncertain"  # 只降不升
        if v["note"]:
            r2["note"] = v["note"][:200]
        screened.append(r2)
    return screened


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
