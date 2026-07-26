"""search_resources 工具（plan/02 §5，检索 Python + LLM 内容适配性筛选 iter 62）。

按阶段主题检索 Web 资源 + verify_url 校验 -> ✅⚠️❌ 标记。
- 双引擎（iter 222，BING_API_KEY 可选化）：
  bing_api = Bing Search API（付费，有 key 时质量更高）；
  bing_web = Bing 网页版抓取（cn.bing.com，免 Key，国内可达）。
  引擎选择见 app.core.settings.effective_search_engine（auto/bing/bing_web 三值语义）。
- stage_topics：args 显式提供优先，缺省从 upstream.design_stages 派生（err.md B21）。
- 不编造 URL；无结果/引擎不可用 -> 返回空 + 关键词建议，不占位。
- 限额：每阶段 ≤5、总数 ≤40（09 §二十）。
- 内容适配性筛选（iter 62，Skill=deep-tutor-resources；iter 361 强制化）：verify 可达后，一次批量 LLM
  调用判定候选与阶段主题/学习者水平的适配性——不相关丢弃、存疑降 ⚠️、附 note。**强制门**（用户 /goal：
  「强制所有引文都必须经过 AI 筛选，避免给学生引入不良信息」）：未经 AI 核验的候选（筛选失败重试耗尽 /
  响应未覆盖）一律**丢弃**，不再降标保留——宁缺勿滥。
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
from app.tools.verify_url import make_verify_client, verify_url

log = logging.getLogger("deep_tutor.tools.search")

MAX_PER_STAGE = 5  # 无复杂度信号时的默认每阶段上限（plan/09 §二十 基线）
MAX_TOTAL = 40  # 无复杂度信号时的默认全方案上限（基线）
# iter 360（引文数量动态化）：随学习计划复杂度上调——难点阶段/复杂目标需更多参考文献。
# 硬上限保留（成本/认知负荷有界）：每阶段 ≤7、全方案 ≤50。
_HARD_MAX_PER_STAGE = 7
_HARD_MAX_TOTAL = 50
_BASE_TOTAL = 20  # 总参考文献基线（+ 知识点数 → 复杂目标参考文献更多）
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
SEARCH_TIMEOUT = 30.0

# 并发限额（iter 228 并行优化：检索/校验原为全串行网络调用，是生成 pipeline
# 最长的 wall-clock 阶段）：
# - 检索并发 2：bing_web 为网页抓取，并发过高易触发反爬（验证码页 -> 判引擎失败），保守取 2；
# - 校验并发 4：候选 host 分散，verify_url 内部另有 TTL 缓存/robots 缓存去重兜底。
_SEARCH_CONCURRENCY = 2
_VERIFY_CONCURRENCY = 4

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
        # iter 245：显式入参白名单校验——str 会按字符迭代成单字查询、dict 会在查询
        # 推导处抛 TypeError 拖垮整工具、巨型 list 查询数无界（派生路径由 design_stages
        # 的 3-6 阶段 validator 间接封顶，显式路径没有）。非法 -> 忽略并走派生
        if isinstance(explicit, list) and len(explicit) <= 10:
            cleaned: list[list[str]] = []
            for topics in explicit:
                if not isinstance(topics, list):
                    cleaned.append([])
                    continue
                cleaned.append(
                    [t for t in (str(x).strip()[:MAX_QUERY_CHARS] for x in topics[:5]) if t]
                )
            return cleaned
        log.warning("stage_topics 显式入参非法（%s），忽略改派生", type(explicit).__name__)
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


def _ctx_bing_key(ctx: ExecContext) -> str:
    """本次执行实际使用的 Bing Key（iter 223）：ctx 注入的凭证档案有效 key，
    空 -> .env BING_API_KEY（测试/脚本不经 API 层注入时的回退）。"""
    return (getattr(ctx, "bing_api_key", "") or "") or settings.bing_api_key


# --- iter 360：复杂度感知的引文配额（issue 4：参考文献数量随计划复杂度/资料量动态）---


def _complexity_signals(inp: ToolInput) -> tuple[int, list[dict]]:
    """从上游读取复杂度信号：(知识点总数, stages 列表)。

    - 知识点总数（build_knowledge_map.nodes）：目标越宽 -> 拆分出的知识点越多 -> 需更多参考文献。
    - stages（design_stages）：每阶段 difficulty/广度决定该阶段参考数量。
    缺上游（测试/局部调用）-> (0, [])，配额回退基线 MAX_PER_STAGE/MAX_TOTAL。
    """
    km = ((inp.upstream or {}).get("build_knowledge_map") or {}).get("data") or {}
    nodes = km.get("nodes") or []
    stages = (((inp.upstream or {}).get("design_stages") or {}).get("data") or {}).get("stages") or []
    return len(nodes), [s for s in stages if isinstance(s, dict)]


def _stage_difficulty(stage: dict) -> int:
    try:
        return max(1, min(5, int(stage.get("difficulty") or 1)))
    except (TypeError, ValueError):
        return 1


def stage_topic_cap(stage: dict | None) -> int:
    """该阶段检索的主题数上限：广度/难度越高检索越多主题（覆盖面），但封顶 4。

    无 stage（缺上游/测试）-> 3（同旧行为，保兼容）。
    """
    if not stage:
        return 3
    return 4 if _stage_difficulty(stage) >= 4 else 3


def stage_resource_cap(stage: dict | None, stage_index: int, stages: list[dict]) -> int:
    """该阶段资源上限：难度越高 -> 参考文献越多（难点需更详尽参考）；无信号 -> MAX_PER_STAGE。

    difficulty 1->3 / 2->4 / 3->5 / 4->6 / 5->7（封顶 _HARD_MAX_PER_STAGE）。
    """
    if not stage:
        return MAX_PER_STAGE
    cap = 2 + _stage_difficulty(stage)
    return min(cap, _HARD_MAX_PER_STAGE)


def total_resource_cap(total_nodes: int) -> int:
    """全方案资源上限：知识点越多（越复杂）-> 总参考文献越多；封顶 _HARD_MAX_TOTAL。"""
    return min(_BASE_TOTAL + max(0, total_nodes), _HARD_MAX_TOTAL)


async def _run_web_queries(
    queries: list[tuple[int, str, int]], engine: str, bing_key: str
) -> list[list[dict] | None]:
    """并发执行全部检索查询（有界 _SEARCH_CONCURRENCY，iter 228；iter 360 每查询候选数动态）。

    queries 元素为 (stage_index, topic, count)；count 为该查询向搜索引擎请求的候选数
    （难点阶段取更多候选）。返回与 queries 等长对齐的结果列表；单查询失败 -> 对应位 None
    （记 warning，不中断整批，同旧串行 continue 语义）。gather 保持入参顺序。
    """
    sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)

    async def _one(topic: str, count: int) -> list[dict] | None:
        async with sem:
            try:
                return await _search_web(topic, engine, count=count, key=bing_key)
            except Exception as exc:  # noqa: BLE001  单查询检索失败不中断
                log.warning("Web 检索失败 topic=%s: %s", topic[:MAX_QUERY_CHARS], exc)
                return None

    return await asyncio.gather(*(_one(topic, count) for _, topic, count in queries))


async def _verify_candidates(
    items: list[tuple[int, dict]], session_id: str, ctx: ExecContext
) -> dict[str, dict]:
    """并发校验候选 URL（有界 _VERIFY_CONCURRENCY，iter 228），返回 {候选原 url: verify data}。

    - 按 URL 去重：同 URL 只验一次（stage_level 取首个出现阶段；verify_url 进程内
      TTL 缓存进一步兜底——缓存/robots 缓存为普通 dict，并发下同 URL 仍可能各抓
      一次，无害仅浪费，去重已把此窗口压到最小）。
    - 单候选校验异常不中断整批：记 warning 按 invalid 跳过。与旧串行「异常上抛
      致整工具失败、pipeline 中断」的有意偏离（稳定优先：单候选病害不拖垮检索）。
    - R4（iter 231）：全批共享一个 make_verify_client 安全工厂构造的 client
      （连接池复用；follow_redirects=False 的 SSRF 不变量由工厂保证），用毕关闭。
    """
    first_seen: dict[str, int] = {}  # url -> stage_level（保留首个出现阶段，dict 保序）
    for stage_index, cand in items:
        url = cand.get("url", "")
        if not isinstance(url, str):
            # iter 245：畸形候选（上游 API 返回 url 为 list/dict）单条丢弃——
            # 此前 unhashable TypeError 延迟到此抛出，整工具失败丢失全部已得候选
            continue
        if url and url not in first_seen:
            # err.md B19：阶段水平随 stage_index 递进（高级阶段资源不按入门水平校验）
            first_seen[url] = stage_index + 1
    sem = asyncio.Semaphore(_VERIFY_CONCURRENCY)

    async def _one(client: httpx.AsyncClient, url: str, stage_level: int) -> tuple[str, dict]:
        async with sem:
            v_inp = ToolInput(
                tool="verify_url",
                session_id=session_id,
                args={"url": url, "stage_level": stage_level},
            )
            try:
                v_out = await verify_url(v_inp, ctx, client=client)
            except Exception as exc:  # noqa: BLE001  单候选病害不拖垮整批
                log.warning("候选校验异常 url=%s: %s", url, exc)
                return url, {"label": "invalid"}
            return url, (v_out.data or {})

    shared = make_verify_client(ctx)
    try:
        pairs = await asyncio.gather(*(_one(shared, u, lvl) for u, lvl in first_seen.items()))
    finally:
        await shared.aclose()
    return dict(pairs)


async def search_resources(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    stage_topics: list[list[str]] = _derive_stage_topics(inp)
    bing_key = _ctx_bing_key(ctx)
    engine = effective_search_engine(bing_key)

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

    # --- 阶段 1：并发检索（iter 228；iter 360 主题数/候选数随阶段难度动态）---
    total_nodes, stages = _complexity_signals(inp)
    total_cap = total_resource_cap(total_nodes)
    queries: list[tuple[int, str, int]] = []  # (stage_index, topic, 每主题候选数)
    for stage_index, topics in enumerate(stage_topics):
        stage = stages[stage_index] if stage_index < len(stages) else None
        topic_cap = stage_topic_cap(stage)  # 难度高的阶段检索更多主题
        cand_count = min(3 + _stage_difficulty(stage), 8) if stage else 5  # 难点阶段多取候选
        for topic in topics[:topic_cap]:
            queries.append((stage_index, topic, cand_count))
    attempted = len(queries)  # 实际发起的检索数（空主题时不得误报引擎不可用）
    results = await _run_web_queries(queries, engine, bing_key)
    succeeded = sum(1 for r in results if r is not None)

    # --- 阶段 2：并发校验（URL 去重，同 URL 只验一次）---
    items: list[tuple[int, dict]] = [
        (stage_index, cand)
        for (stage_index, _, _), cands in zip(queries, results, strict=True)
        if cands
        for cand in cands
    ]
    verdicts = await _verify_candidates(items, inp.session_id, ctx)

    # --- 阶段 3：确定性选择（iter 360：每阶段/总配额随复杂度动态；顺序语义不变：
    # 按 阶段->主题->候选 原顺序遍历，invalid 换源不占位，每阶段 ≤stage_resource_cap、总 ≤total_cap）---
    resources: list[dict] = []
    per_stage: dict[int, int] = {}
    for (stage_index, _, _), cands in zip(queries, results, strict=True):
        if not cands:
            continue
        stage = stages[stage_index] if stage_index < len(stages) else None
        cap = stage_resource_cap(stage, stage_index, stages)
        for cand in cands:
            if per_stage.get(stage_index, 0) >= cap or len(resources) >= total_cap:
                break
            url = cand.get("url", "")
            if not isinstance(url, str):
                continue  # iter 245：畸形候选（unhashable url）单条丢弃，不拖垮整批
            vdata = verdicts.get(url) or {}
            if vdata.get("label", "invalid") == "invalid":
                continue  # 失效换源
            resources.append(
                {
                    "stage_index": stage_index,
                    "title": cand.get("title", ""),
                    "url": vdata.get("final_url", url),
                    "type": "doc",
                    "verified": vdata.get("label", "uncertain"),
                    "note": "",
                    "snippet": vdata.get("snippet", ""),  # iter 360：正文摘要供筛选判相关性
                }
            )
            per_stage[stage_index] = per_stage.get(stage_index, 0) + 1

    # 检索引擎全面失败（key 失效/欠费/网络/反爬，区别于真空结果）：降级为关键词建议，
    # 不中断 pipeline。iter 245：判定推广到所有引擎——此前仅 bing_web，bing_api 全面
    # 失败会被报成 ok+空结果（与「正常但真没结果」不可区分，排障零信号）
    if attempted > 0 and succeeded == 0:
        keywords = [t for topics in stage_topics for t in topics]
        return ToolOutput(
            tool="search_resources",
            status="repaired",
            data={
                "resources": [],
                "engine": engine,
                "note": f"检索引擎（{engine}）不可用，未获得结果；请人工补充资源",
                "suggested_keywords": keywords[:20],
            },
            errors=[ToolError(code="search_unavailable", message=f"检索引擎（{engine}）全部查询失败，search_resources 降级")],
        )

    # iter 346（F1 兼容）：无候选（检索成功但 0 命中，或全 invalid 换源）-> 不调 LLM 筛选
    # （空候选筛选用 LLM 无意义且徒增调用；与生成流程的 mock 响应序列解耦）
    if not resources:
        return ToolOutput(tool="search_resources", status="ok", data={"resources": [], "engine": engine})
    # err.md B9：筛选调用的 usage 须进入 ToolOutput.metadata —— 否则编排器
    # budget.add(0) 且 _persist_tool_calls 归集为空，这次 LLM 花费对预算/统计失明
    resources, screen_usage, screen_warn = await _screen_resources(resources, inp, ctx)  # 强制 AI 筛选门
    for r in resources:
        r.pop("snippet", None)  # 摘要仅供筛选，不进方案 resources schema
    data: dict = {"resources": resources, "engine": engine}
    if screen_warn:
        # iter 361：筛选失败致候选全部被过滤 -> 告知用户（宁缺勿滥，不静默给空）
        data["note"] = screen_warn
    out = ToolOutput(tool="search_resources", status="ok", data=data)
    if screen_usage is not None:
        from app.agent.cost import build_metadata

        out.metadata = build_metadata(screen_usage)
    return out


# --- LLM 内容适配性筛选（iter 62，plan/02 §5：✅ = HTTP 200 + 内容难度适配该阶段）---

SCREEN_MAX_TOKENS = 2000  # ≤40 候选的判定 JSON 上限足够
# iter 361（强制门）：筛选调用失败/不可解析时重试一次（瞬态网络/超时不至于零化全部候选），
# 仍失败则强制丢弃全部未核验候选（不降标保留）。
_SCREEN_MAX_ATTEMPTS = 2

# inline 回退 prompt（R13：skills/deep-tutor-resources 缺失时用；两者 schema 必须一致）
# iter 360：候选附正文摘要 snippet（verify_url 抓取的前缀纯文本）——据此判内容是否与主题
# *强相关*，不再仅凭标题（标题党/同名不同义/SEO 水文可识别）。
SCREEN_PROMPT = """你是 Deep-tutor 的「资源内容适配性筛选」工具。候选资源已通过可达性校验（HTTP 200），你的任务：判定每个候选与对应学习阶段主题、学习者水平的**内容强相关性**与适配性。

输入（user message JSON）：{"topic":"学习主题","level":"学习者水平","stages":["阶段名",...],"candidates":[{"stage_index":0,"title":"...","url":"...","snippet":"正文摘要（可能为空）"}]}

仅返回 JSON 数组（不要 markdown 代码块、不要解释），每候选一项：
[{"url":"候选 url 原文","suitable":true,"label":"ok|uncertain","note":"一句话说明"}]

规则：
- 优先依据 snippet 正文摘要判断内容是否真正讲该主题；snippet 为空时再退回标题与常识。
- suitable=false：内容与该阶段主题不相关或仅擦边（如主题讲 Python 基础，候选是 Java 框架/导览页/无关资讯）——该候选将被丢弃。只有内容确实覆盖该主题才 true，宁缺勿滥。
- label="ok"：内容相关且难度/深度适配该阶段（初级阶段配入门教程）。
- label="uncertain"：相关但难度/质量不确定（明显过深、内容陈旧、清单水文、snippet 不足以判定等）。
- note：中文一句话（≤40 字），uncertain 时说明原因；ok 时可留空。
- 必须覆盖 candidates 中的每个 url，url 原样返回（不改写、不编造）。
- 拿不准一律 uncertain，不臆断 ok；不得因「标题含关键词」就判相关，须正文佐证。

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
) -> tuple[list[dict], "LLMUsage | None", str | None]:
    """LLM 内容适配性筛选（强制门，用户 /goal：所有引文必须经 AI 筛选）。

    iter 360：候选附正文摘要 snippet，LLM 据正文判「内容强相关性」（不再仅凭标题）。
    iter 361 强制化：有 provider 时筛选为**强制门**——
      - suitable=false（内容不相关）-> 丢弃；
      - 响应未覆盖的 url（LLM 未给出判定）-> 丢弃（未通过 AI 核验）；
      - 筛选调用失败/输出不可解析（重试 _SCREEN_MAX_ATTEMPTS 次后仍失败）-> 丢弃**全部**候选。
    不再像旧 _mark_unscreened 那样降标保留未筛选候选——避免给学生引入未核验/不良信息（宁缺勿滥）。
    返回 (筛选后资源, 本次 LLM usage 或 None, 告警说明或 None)（err.md B9：调用方据 usage 填 metadata；
    告警说明非 None 时调用方应写入 data.note 告知用户为何资源被过滤）。
    无 provider（仅测试/独立调用；真实方案生成经 plans._run_start 的 no_llm_provider 守卫永不至此）
    -> 不筛选，原样返回（兼容 search 逻辑测试，三元素告警为 None）。
    label 只降不升（verify 已 ⚠️ 的候选不因 LLM 说 ok 而升 ✅）。
    """
    provider = ctx.llm_provider
    if provider is None or not resources:
        return resources, None, None

    intent = ((inp.upstream or {}).get("understand_intent") or {}).get("data") or {}
    stages_data = ((inp.upstream or {}).get("design_stages") or {}).get("data") or {}
    stage_names = [str(s.get("name", "")) for s in (stages_data.get("stages") or []) if isinstance(s, dict)]
    payload = {
        "topic": intent.get("topic", ""),
        "level": intent.get("level", "") or "",
        "stages": stage_names,
        "candidates": [
            # iter 245：title/snippet 来自网络不可信内容——截断压缩注入载荷空间（筛选器输出
            # 仅丢/留/降级标记 + 短 note，影响面本就有界）。iter 360：附 snippet 供正文相关性判断
            {
                "stage_index": r["stage_index"],
                "title": str(r.get("title", ""))[:80],
                "url": r["url"],
                "snippet": str(r.get("snippet", ""))[:400],
            }
            for r in resources
        ],
    }
    system = (ctx.skill_registry or {}).get("deep-tutor-resources") or SCREEN_PROMPT
    # iter 361 强制门：重试 _SCREEN_MAX_ATTEMPTS 次（瞬态失败不致零化全部候选）；耗尽仍失败 -> 全丢弃。
    last_usage: LLMUsage | None = None
    verdicts: dict[str, dict] | None = None
    for attempt in range(_SCREEN_MAX_ATTEMPTS):
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
            last_usage = resp.usage
            verdicts = _parse_screen_verdicts(resp.content)
            if verdicts is not None:
                break  # 成功解析，结束重试
        except Exception as exc:  # noqa: BLE001  记录并重试；耗尽后强制丢弃
            log.warning("资源内容适配筛选第 %d/%d 次失败: %s", attempt + 1, _SCREEN_MAX_ATTEMPTS, exc)
            last_usage = None
    if verdicts is None:
        log.warning("资源筛选重试耗尽，强制丢弃全部 %d 候选（避免引入未核验内容）", len(resources))
        return [], last_usage, "AI 相关性筛选失败（已重试），为避免引入未经验证的内容，已过滤全部候选资源"

    screened: list[dict] = []
    for r in resources:
        v = verdicts.get(r["url"])
        if v is None:
            continue  # iter 361：响应未覆盖 -> 未通过 AI 核验，丢弃（强制门）
        if not v["suitable"]:
            continue  # 内容不相关 -> 丢弃
        r2 = dict(r)
        if v["label"] == "uncertain":
            r2["verified"] = "uncertain"  # 只降不升
        if v["note"]:
            r2["note"] = v["note"][:200]
        screened.append(r2)
    return screened, last_usage, None


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


async def _bing_web_search(query: str, count: int = 5, key: str = "") -> list[dict]:
    """免 Key Bing 网页版检索（cn.bing.com 搜索页抓取，国内可达），返回 [{title,url}]。

    key 参数仅为统一引擎签名（免 Key 引擎不使用）。

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


async def _bing_api_search_with_settings(query: str, count: int = 5, key: str = "") -> list[dict]:
    """bing_api 引擎适配：补传有效 Bing Key（iter 223 凭证档案解析值），统一引擎签名。"""
    return await _bing_api_search(query, key or settings.bing_api_key, count=count)


# 引擎注册表：新增引擎 = 实现 `_xxx_search(query, count, key)` + 在此注册一行
# + settings.SEARCH_ENGINES 加值（决策函数 effective_search_engine 同步分支）
_ENGINE_FUNCS = {"bing_api": _bing_api_search_with_settings, "bing_web": _bing_web_search}


async def _search_web(query: str, engine: str, count: int = 5, key: str = "") -> list[dict]:
    """统一检索调度入口（测试 monkeypatch seam）。key 为有效 Bing Key（免 Key 引擎忽略）。"""
    func = _ENGINE_FUNCS.get(engine)
    if func is None:
        raise ValueError(f"未知搜索引擎: {engine}")
    return await func(query[:MAX_QUERY_CHARS], count=count, key=key)


MAX_PER_UNIT = 3  # 单单元补充资料默认上限（iter 48，plan/07 §三 步骤 2）；iter 360 调用方可按单元复杂度动态传入


# iter 360（issue 2a/3）+ iter 361 强制化：自学单元补充资料的相关性筛选——与主路径一致，
# 候选附正文摘要 snippet，LLM 据正文判与单元主题是否强相关；强制门：不相关/未覆盖/筛选失败（重试耗尽）
# 一律丢弃，不再降标保留（_mark_unscreened 已移除）。
UNIT_SCREEN_PROMPT = """你是 Deep-tutor 的「自学单元补充资料筛选」工具。候选已通过可达性校验（HTTP 200）。判定每个候选与自学单元主题的**内容强相关性**。

输入（user message JSON）：{"topic":"单元主题（知识点/单元标题）","candidates":[{"title":"...","url":"...","snippet":"正文摘要（可能为空）"}]}

仅返回 JSON 数组（不要 markdown 代码块、不要解释），每候选一项：
[{"url":"候选 url 原文","suitable":true,"label":"ok|uncertain","note":"一句话说明"}]

规则：
- 优先依据 snippet 正文判断是否真正讲该单元主题；snippet 为空时退回标题与常识。
- suitable=false：内容与单元主题不相关或仅擦边（导览页/无关资讯/同名不同义）——该候选将被丢弃。只有确实覆盖该主题才 true，宁缺勿滥。
- label="ok"：相关且适合自学该单元；label="uncertain"：相关但难度/质量不确定或 snippet 不足判定。
- note：中文一句话（≤40 字），uncertain 时说明原因；ok 时可留空。
- 必须覆盖每个候选 url，url 原样返回（不改写、不编造）；拿不准一律 uncertain。

仅返回符合上述格式的 JSON 数组。
"""


async def _screen_unit_resources(
    resources: list[dict], query: str, ctx: ExecContext
) -> list[dict]:
    """单元补料 LLM 相关性筛选（强制门，iter 361）：不相关丢弃、存疑降 ⚠️、附 note。

    有 provider 时为强制门——suitable=false / 响应未覆盖 / 筛选失败（重试 _SCREEN_MAX_ATTEMPTS 次
    后仍失败）一律丢弃，不再降标保留未筛选候选。无 provider（调用方 search_unit_resources 已在更早处
    短路为 []，此分支仅作防御）-> 原样返回。
    """
    provider = ctx.llm_provider
    if provider is None or not resources:
        return resources
    payload = {
        "topic": query[:MAX_QUERY_CHARS],
        "candidates": [
            {
                "title": str(r.get("title", ""))[:80],
                "url": r["url"],
                "snippet": str(r.get("snippet", ""))[:400],
            }
            for r in resources
        ],
    }
    verdicts: dict[str, dict] | None = None
    for attempt in range(_SCREEN_MAX_ATTEMPTS):
        try:
            resp = await asyncio.wait_for(
                provider.chat(
                    UNIT_SCREEN_PROMPT,
                    json.dumps(payload, ensure_ascii=False),
                    max_tokens=SCREEN_MAX_TOKENS,
                    cache_system=True,
                ),
                timeout=ctx.timeouts.llm,
            )
            verdicts = _parse_screen_verdicts(resp.content)
            if verdicts is not None:
                break
        except Exception as exc:  # noqa: BLE001  记录并重试；耗尽后强制丢弃
            log.warning("单元补料筛选第 %d/%d 次失败: %s", attempt + 1, _SCREEN_MAX_ATTEMPTS, exc)
    if verdicts is None:
        log.warning("单元补料筛选重试耗尽，强制丢弃全部 %d 候选", len(resources))
        return []
    screened: list[dict] = []
    for r in resources:
        v = verdicts.get(r["url"])
        if v is None:
            continue  # iter 361：响应未覆盖 -> 未通过 AI 核验，丢弃（强制门）
        if not v["suitable"]:
            continue  # 不相关 -> 丢弃
        r2 = dict(r)
        if v["label"] == "uncertain":
            r2["verified"] = "uncertain"
        if v["note"]:
            r2["note"] = v["note"][:200]
        screened.append(r2)
    return screened


async def search_unit_resources(query: str, ctx: ExecContext, limit: int = MAX_PER_UNIT) -> list[dict]:
    """单自学单元 WebSearch 补充资料（plan/07 §三 步骤 2「每单元 WebSearch 补充资料」，iter 48）。

    iter 360：verify 可达后经 `_screen_unit_resources` 做 LLM 相关性筛选（不相关丢弃、存疑降 ⚠️），
    与主路径一致；附正文摘要供筛选，返回前剥离。
    iter 361 强制门（用户 /goal）：**无 LLM provider 时直接返回 []**——降级材料路径（用户未配 key）
    真实可达，此时无法做 AI 相关性/安全性筛选，不向学生提供仅 verify 可达性（未筛选）的网络补充资料，
    避免引入不良信息。筛选失败/未覆盖同样丢弃（见 _screen_unit_resources）。
    保留 ≤limit 条 {title,url,verified,note}。limit 由调用方按单元复杂度动态传入（issue 4）。
    引擎不可用（bing 无 key）/ 空 query / 检索失败 / 无结果 -> []（不编造 URL；内容侧由 LLM 标「无外部资料参考」）。
    """
    bing_key = _ctx_bing_key(ctx)
    engine = effective_search_engine(bing_key)
    if engine == "none" or not query.strip():
        return []
    if ctx.llm_provider is None:
        # 强制门：无 provider 则无法 AI 筛选 -> 不提供未核验网络补充资料（避免不良信息）
        return []

    try:
        candidates = await _search_web(query, engine, count=limit, key=bing_key)
    except Exception as exc:  # noqa: BLE001  检索失败不中断单元生成
        log.warning("单元补充资料检索失败 query=%s: %s", query[:MAX_QUERY_CHARS], exc)
        return []
    # iter 228：复用并发校验（stage_level 恒 1，与旧硬编码一致；单候选异常按 invalid 跳过）
    verdicts = await _verify_candidates([(0, c) for c in candidates], ctx.session_id, ctx)
    resources: list[dict] = []
    for cand in candidates:
        vdata = verdicts.get(cand.get("url", "")) or {}
        if vdata.get("label", "invalid") == "invalid":
            continue  # 失效换源（同主流程）
        resources.append(
            {
                "title": cand.get("title", ""),
                "url": vdata.get("final_url", cand.get("url", "")),
                "verified": vdata.get("label", "uncertain"),
                "note": "",
                "snippet": vdata.get("snippet", ""),  # iter 360：正文摘要供筛选
            }
        )
    resources = await _screen_unit_resources(resources, query, ctx)
    for r in resources:
        r.pop("snippet", None)  # 摘要仅供筛选，不持久化
    return resources[:limit]


search_resources_tool = Tool(
    name="search_resources",
    llm=False,
    func=search_resources,
    # 实际读取 understand_intent（资源筛选意图）、design_stages（B21 主题派生 + 阶段难度配额）、
    # build_knowledge_map（iter 360 知识点数 -> 引文数量随复杂度动态）的上游输出；据此声明使
    # orchestrator 能静态校验它不与其他读取它的工具同组并行（见 _PARALLEL_GROUPS 断言）。
    upstream_reads=frozenset({"understand_intent", "design_stages", "build_knowledge_map"}),
)
