"""search_resources 工具（plan/02 §5，检索 Python + LLM 内容适配性筛选 iter 62）。

按阶段主题检索 Web 资源 + verify_url 校验 -> ✅⚠️❌ 标记。
- 双引擎（iter 222，BING_API_KEY 可选化）：
  bing_api = Bing Search API（付费，有 key 时质量更高）；
  bing_web = Bing 网页版抓取（cn.bing.com，免 Key，国内可达）。
  引擎选择见 app.core.settings.effective_search_engine（auto/bing/bing_web 三值语义）。
- stage_topics：args 显式提供优先，缺省从 upstream.design_stages 派生（err.md B21）。
- 不编造 URL；无结果/引擎不可用 -> 返回空 + 关键词建议，不占位。
- 限额：每阶段 ≤5、总数 ≤40（09 §二十）。
- 内容适配性筛选（iter 62，Skill=deep-tutor-resources）：verify 可达后，一次批量 LLM 调用
  判定候选与阶段主题/学习者水平的适配性——不相关丢弃、存疑降 ⚠️、附 note；
  无 provider / LLM 或解析失败 -> 跳过筛选（行为同可达即 ok，稳定降级）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from html import unescape
from urllib.parse import urlparse

import httpx

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.core.settings import effective_search_engine, settings
from app.llm.base import LLMUsage
from app.tools.base import ToolError, ToolInput, ToolOutput
from app.tools.verify_url import verify_url

log = logging.getLogger("deep_tutor.tools.search")

MAX_PER_STAGE = 5
MAX_TOTAL = 40
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
SEARCH_TIMEOUT = 30.0

# --- Bing 网页版抓取（免 Key 兜底引擎，iter 222）---
BING_WEB_ENDPOINT = "https://cn.bing.com/search"
BING_WEB_MKT = "zh-CN"
BING_WEB_HEADERS = {  # 模拟 Chrome，防反爬（实测可返回正常结果页）
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
BING_WEB_TIMEOUT = 15.0  # 网页搜索应快速失败（防多查询累积逼近 parse 总超时）
BING_WEB_MAX_BYTES = 2 * 1024 * 1024  # 响应上限：防 OOM + 收敛正则输入规模
MAX_QUERY_CHARS = 150  # 查询串截断（主题来自 LLM 输出，防病态长度）
_BING_WEB_ITEM_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _derive_stage_topics(inp: ToolInput) -> list[list[str]]:
    """stage_topics：args 显式提供优先；缺省时从 upstream.design_stages 派生（err.md B21）。

    B21：生产环境 args_by_tool 的全部构造点（plans.py generate/clarify/regenerate）只给
    understand_intent 传参，stage_topics 恒为空 -> 主 pipeline 检索空转、plan.resources 恒空。
    缺省时改从 design_stages 输出的 stages[].topics[].name 派生（阶段内去重保序）。
    """
    explicit = inp.args.get("stage_topics")
    if explicit:
        return explicit
    stages_data = ((inp.upstream or {}).get("design_stages") or {}).get("data") or {}
    derived: list[list[str]] = []
    for s in stages_data.get("stages") or []:
        if not isinstance(s, dict):
            continue
        names: list[str] = []
        for t in s.get("topics") or []:
            if isinstance(t, dict):
                name = str(t.get("name", "")).strip()
                if name and name not in names:
                    names.append(name)
        derived.append(names)
    return derived


async def search_resources(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    stage_topics: list[list[str]] = _derive_stage_topics(inp)
    engine = effective_search_engine()

    if engine == "none":
        # SEARCH_ENGINE=bing 且未配置 key（兼容旧行为）：不编造 URL，返回空 + 关键词建议
        keywords = [t for topics in stage_topics for t in topics]
        return ToolOutput(
            tool="search_resources",
            status="repaired",
            data={
                "resources": [],
                "engine": "none",
                "note": "SEARCH_ENGINE=bing 但未配置 BING_API_KEY，未执行检索；改用 SEARCH_ENGINE=auto 可免 Key 网页检索",
                "suggested_keywords": keywords[:20],
            },
            errors=[ToolError(code="no_search_key", message="SEARCH_ENGINE=bing 但未配置 BING_API_KEY，search_resources 不可用")],
        )

    resources: list[dict] = []
    attempted = 0  # 实际发起的检索数（空主题时不得误报引擎不可用）
    succeeded = 0  # 引擎层面成功的检索数（区别于「成功但真空结果」）
    for stage_index, topics in enumerate(stage_topics):
        for topic in topics[:3]:  # 每阶段取前 3 个主题检索
            if len(resources) >= MAX_TOTAL:
                break
            attempted += 1
            try:
                candidates = await _search_web(topic, engine)
            except Exception as exc:  # noqa: BLE001  单查询检索失败不中断
                log.warning("Web 检索失败 topic=%s: %s", topic[:MAX_QUERY_CHARS], exc)
                continue
            succeeded += 1
            for cand in candidates:
                if sum(1 for r in resources if r["stage_index"] == stage_index) >= MAX_PER_STAGE:
                    break
                if len(resources) >= MAX_TOTAL:
                    break
                v_inp = ToolInput(
                    tool="verify_url",
                    session_id=inp.session_id,
                    # err.md B19：阶段水平随 stage_index 递进（此前硬编码 1，
                    # 高级阶段资源按入门水平校验，verify 难度判定失真）
                    args={"url": cand.get("url", ""), "stage_level": stage_index + 1},
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

    # 网页引擎全面失败（网络/反爬，区别于真空结果）：降级为关键词建议，不中断 pipeline
    if engine == "bing_web" and attempted > 0 and succeeded == 0:
        keywords = [t for topics in stage_topics for t in topics]
        return ToolOutput(
            tool="search_resources",
            status="repaired",
            data={
                "resources": [],
                "engine": engine,
                "note": "Bing 网页检索不可用，未执行检索；请人工补充资源",
                "suggested_keywords": keywords[:20],
            },
            errors=[ToolError(code="search_unavailable", message="Bing 网页检索失败（网络/反爬），search_resources 降级")],
        )

    # err.md B9：筛选调用的 usage 须进入 ToolOutput.metadata —— 否则编排器
    # budget.add(0) 且 _persist_tool_calls 归集为空，这次 LLM 花费对预算/统计失明
    resources, screen_usage = await _screen_resources(resources, inp, ctx)  # LLM 内容适配性筛选（iter 62）
    out = ToolOutput(tool="search_resources", status="ok", data={"resources": resources, "engine": engine})
    if screen_usage is not None:
        from app.agent.cost import build_metadata

        out.metadata = build_metadata(screen_usage)
    return out


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


async def _screen_resources(
    resources: list[dict], inp: ToolInput, ctx: ExecContext
) -> tuple[list[dict], "LLMUsage | None"]:
    """LLM 内容适配性筛选（一次批量调用，成本有界）：不相关丢弃、存疑降 ⚠️、附 note。

    返回 (筛选后资源, 本次 LLM usage 或 None)（err.md B9：调用方据 usage 填 metadata）。
    降级（稳定优先）：无 provider / 无候选 / LLM 异常 / 输出不可解析 -> 原样返回
    （行为同筛选前：verify 可达即保留原标记）。LLM 响应未覆盖的 url 保持原判定。
    label 只降不升（verify 已 ⚠️ 的候选不因 LLM 说 ok 而升 ✅）。
    """
    provider = ctx.llm_provider
    if provider is None or not resources:
        return resources, None

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
        # err.md B8：筛选调用此前无超时，provider 挂起会无限阻塞整个 pipeline
        resp = await asyncio.wait_for(
            provider.chat(
                system,
                json.dumps(payload, ensure_ascii=False),
                max_tokens=SCREEN_MAX_TOKENS,
                cache_system=True,
            ),
            timeout=ctx.timeouts.llm,
        )
        verdicts = _parse_screen_verdicts(resp.content)
    except Exception as exc:  # noqa: BLE001  筛选失败不阻塞：保持原判定
        log.warning("资源内容适配筛选失败，跳过筛选: %s", exc)
        return resources, None
    if verdicts is None:
        log.warning("资源内容适配筛选输出不可解析，跳过筛选")
        return resources, resp.usage

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
    return screened, resp.usage


async def _bing_api_search(query: str, api_key: str, count: int = 5) -> list[dict]:
    """调用 Bing Web Search API（付费，有 key 时质量更高），返回 [{title, url}]。"""
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "count": count}
    async with httpx.AsyncClient(timeout=httpx.Timeout(SEARCH_TIMEOUT)) as client:
        resp = await client.get(BING_ENDPOINT, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
    web_pages = (data.get("webPages") or {}).get("value") or []
    return [{"title": w.get("name", ""), "url": w.get("url", "")} for w in web_pages]


def _parse_bing_web_html(html_text: str, count: int) -> list[dict]:
    """纯函数：Bing 搜索页 HTML -> [{title,url}]（≤count 条）；永不上抛，解析 0 条返回 []。

    主路径为 _BING_WEB_ITEM_RE 单条正则（不引入第二解析路径：双倍维护成本且
    Bing 改版时两路径大概率同失效）——改版时只需更新正则与对应罐头测试。
    防御过滤：仅 http(s) 直链、剔除 bing.com/ck/a 跳转壳、URL 去重保序。
    """
    results: list[dict] = []
    seen: set[str] = set()
    for url, raw_title in _BING_WEB_ITEM_RE.findall(html_text or ""):
        if not url.startswith(("http://", "https://")):
            continue  # javascript:/相对链接等
        if "bing.com/ck/a" in url:
            continue  # 跳转壳（实测为直链，改版兜底）
        if url in seen:
            continue
        seen.add(url)
        title = unescape(_TAG_RE.sub("", raw_title)).strip()[:200]
        results.append({"title": title, "url": url})
        if len(results) >= count:
            break
    return results


async def _bing_web_search(query: str, count: int = 5) -> list[dict]:
    """免 Key Bing 网页版检索（cn.bing.com 搜索页抓取，国内可达），返回 [{title,url}]。

    安全：endpoint 为模块常量、query 走 params 编码（无 SSRF 面）；follow_redirects
    容忍区域跳转，但终态 host 非 bing.com -> 抛（防重定向劫持到外站）；2MB 流式上限
    防 OOM/病态响应。失败（非 2xx/非 HTML/无 b_results 容器疑似反爬页/重定向外站）
    -> 抛异常由调用方按引擎失败处理；真空结果（页面正常但无结果）-> 返回 []（严格区分）。
    """
    params = {"q": query, "count": count, "mkt": BING_WEB_MKT}
    async with httpx.AsyncClient(timeout=httpx.Timeout(BING_WEB_TIMEOUT), follow_redirects=True) as client:
        async with client.stream("GET", BING_WEB_ENDPOINT, headers=BING_WEB_HEADERS, params=params) as resp:
            resp.raise_for_status()
            host = urlparse(str(resp.url)).hostname or ""
            if not host.endswith("bing.com"):
                raise ValueError(f"重定向到非 Bing 域: {host}")
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type:
                raise ValueError(f"非 HTML 响应: {content_type}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > BING_WEB_MAX_BYTES:
                    raise ValueError(f"响应超过 {BING_WEB_MAX_BYTES} 字节上限")
                chunks.append(chunk)
    html_text = b"".join(chunks).decode("utf-8", errors="replace")
    if "b_results" not in html_text or "b_captcha" in html_text:
        raise ValueError("疑似反爬/验证码页（无 b_results 结果容器）")
    return _parse_bing_web_html(html_text, count)


async def _bing_api_search_with_settings(query: str, count: int = 5) -> list[dict]:
    """bing_api 引擎适配：补传 settings.bing_api_key，统一引擎签名。"""
    return await _bing_api_search(query, settings.bing_api_key, count=count)


# 引擎注册表：新增引擎 = 实现 `_xxx_search(query, count)` + 在此注册一行
# + settings.SEARCH_ENGINES 加值（决策函数 effective_search_engine 同步分支）
_ENGINE_FUNCS = {"bing_api": _bing_api_search_with_settings, "bing_web": _bing_web_search}


async def _search_web(query: str, engine: str, count: int = 5) -> list[dict]:
    """统一检索调度入口（测试 monkeypatch seam）。"""
    func = _ENGINE_FUNCS.get(engine)
    if func is None:
        raise ValueError(f"未知搜索引擎: {engine}")
    return await func(query[:MAX_QUERY_CHARS], count=count)


MAX_PER_UNIT = 3  # 单单元补充资料上限（iter 48，plan/07 §三 步骤 2）


async def search_unit_resources(query: str, ctx: ExecContext, limit: int = MAX_PER_UNIT) -> list[dict]:
    """单自学单元 WebSearch 补充资料（plan/07 §三 步骤 2「每单元 WebSearch 补充资料」，iter 48）。

    经 `_search_web` 调度生效引擎（iter 222：免 Key 网页版兜底）+ `verify_url` 逐个校验，
    丢弃 invalid，保留 ≤limit 条 {title,url,verified,note}。
    引擎不可用（bing 无 key）/ 空 query / 检索失败 / 无结果 -> []（不编造 URL；内容侧由 LLM 标「无外部资料参考」）。
    """
    engine = effective_search_engine()
    if engine == "none" or not query.strip():
        return []
    try:
        candidates = await _search_web(query, engine, count=limit)
    except Exception as exc:  # noqa: BLE001  检索失败不中断单元生成
        log.warning("单元补充资料检索失败 query=%s: %s", query[:MAX_QUERY_CHARS], exc)
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
