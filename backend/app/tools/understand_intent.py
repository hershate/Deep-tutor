"""understand_intent 工具（plan/02 §2，LLM，Skill=deep-tutor-intent）。

抽取主题/基础/目标/时长；信息不足 -> needs_clarify + 追问（交前端 ChatClarify）。
方法论移植 study-planner Step 1（解析目标、当前基础、学习目标、时间投入）。
step 5 Skill 未接入前用 inline_prompt；接入后由 Skill 覆盖。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.agent.registry import Tool
from app.tools.base import ToolInput

PROMPT = """你是 Deep-tutor 的「学习意图理解」工具。任务：从用户原始描述与解析后的资料中，
抽取学习意图的四个维度，并判断是否需要澄清。

四个维度：
1. topic：学习主题（具体，如「React」「线性代数」）。
2. level：当前基础（如「零基础」「了解 HTML/CSS」「中级」）。
3. goal：学习目标（如「能独立做项目」「应付考试」「转行」）。
4. daily_minutes：每日可投入分钟数（整数）。
5. deadline：截止日期（ISO 8601，无则 null）。
6. start_date：学习起始日期（ISO 8601，用户希望从哪天开始排程；无则 null）。

规则：
- **若 args 含 daily_minutes / deadline / start_date（用户在表单显式指定），直接采用这些值，不要从 raw_text 重新猜测或修改**（这些是权威输入，程序层亦会强制覆盖）。
- 若输入含 answers（用户对澄清问题的回答），**必须**结合 answers 尽可能补全所有缺失维度并返回 needs_clarify=false**——用户已回答过澄清问题，绝不要再就同一维度反复追问。即使某些回答偏模糊，也要给出最合理的推断字段（例如时长模糊就给一个常规默认值），只有 topic（学习主题）完全无法判断时才允许 needs_clarify=true。
- 若**首次**输入（不含 answers）且四个维度中有任一缺失或过于模糊 -> needs_clarify=true，并在 clarify_questions 给出
  针对缺失维度的追问（基础/目标/时长），每条一句、具体可答。
- 若全部明确 -> needs_clarify=false，clarify_questions 为空数组。
- 不要编造：资料/描述未体现的字段填 null，不要猜测。但含 answers 时，对仍模糊的维度做合理推断而非留空（见前条）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{
  "topic": "string",
  "level": "string|null",
  "goal": "string|null",
  "daily_minutes": "int|null",
  "deadline": "ISO date string|null",
  "start_date": "ISO date string|null",
  "needs_clarify": false,
  "clarify_questions": ["string", ...]
}
"""


class IntentData(BaseModel):
    topic: str
    level: str | None = None
    goal: str | None = None
    daily_minutes: int | None = None
    deadline: str | None = None  # ISO 8601 date 字符串
    start_date: str | None = None  # iter 353：学习起始日期（ISO 8601，用户表单指定）
    needs_clarify: bool = False
    clarify_questions: list[str] = Field(default_factory=list)


def _validate_intent(data: dict, inp: ToolInput) -> dict:
    """确定性校验（plan/09 §十/§十六）：

    - deadline 已过去 -> 清空并转澄清（比硬 422 更适合管线：用户可能在叙述历史，
      硬拒会打断生成；追问确认更贴合 plan/09 意图）。非法日期串 -> 清空。
    - daily_minutes 超 1..1440 -> 清空（invalid_range，§十；不阻断生成，按未知时长走）。
    - start_date 非法日期串 -> 清空（iter 353；不校验过去——用户可能补排过去日期）。
    - topic 超 200 字符 -> 截断（invalid_range，§十）。
    - iter 353：args 里的结构化时间字段（daily_minutes/deadline/start_date）为用户表单
      显式指定，authoritative——覆盖 LLM 从 raw_text 的抽取（避免误抽/漏抽）。
    """
    dl = data.get("deadline")
    if dl:
        try:
            data["deadline"] = datetime.fromisoformat(str(dl).strip()).date().isoformat()  # 规范化去时间部分
        except ValueError:
            data["deadline"] = None
        # 过期判定挪至 args 覆盖之后统一执行（iter 371）：此前先对 LLM 抽取值判过期
        # 再被 args 权威值覆盖——过期澄清问题残留 + needs_clarify 不重估（表单填了
        # 合法日期仍被追问「日期已过」）；反向地，args 填过期日期又绕过了过期策略

    dm = data.get("daily_minutes")
    if dm is not None:
        try:
            dm_int = int(dm)
        except (TypeError, ValueError):
            data["daily_minutes"] = None
        else:
            if not 1 <= dm_int <= 1440:
                data["daily_minutes"] = None
            else:
                data["daily_minutes"] = dm_int

    sd = data.get("start_date")
    if sd:
        try:
            datetime.fromisoformat(str(sd).strip()).date()
        except ValueError:
            data["start_date"] = None

    topic = data.get("topic")
    if isinstance(topic, str) and len(topic) > 200:
        data["topic"] = topic[:200]

    # iter 353：用户在首页表单显式指定的结构化时间字段 authoritative——覆盖 LLM 从
    # raw_text 的抽取（daily_minutes/deadline/start_date 由前端直传 args）。
    # inp 可能为 None（旧测试直接调用 _validate_intent(data, None)）；无 args 时跳过覆盖。
    args = (inp.args if inp is not None else {}) or {}
    if args.get("daily_minutes") is not None:
        try:
            dm_arg = int(args["daily_minutes"])
            if 1 <= dm_arg <= 1440:
                data["daily_minutes"] = dm_arg
        except (TypeError, ValueError):
            pass
    if args.get("deadline"):
        try:
            data["deadline"] = datetime.fromisoformat(str(args["deadline"]).strip()).date().isoformat()
        except ValueError:
            pass
    # iter 371：对**最终** deadline 统一校验（无论来源 LLM 抽取或表单 args）
    dl_final = data.get("deadline")
    if dl_final:
        try:
            day = datetime.fromisoformat(str(dl_final).strip()).date()
        except ValueError:
            data["deadline"] = None
        else:
            if day < date.today():
                data["deadline"] = None
                data["needs_clarify"] = True
                data.setdefault("clarify_questions", []).append(
                    f"检测到你提供的截止日期（{day.isoformat()}）已过，请提供新的截止日期，或说明没有截止时间。"
                )
    if args.get("start_date"):
        try:
            datetime.fromisoformat(str(args["start_date"]).strip())
            data["start_date"] = str(args["start_date"]).strip()
        except ValueError:
            pass

    # iter 358 BUG 修复：澄清死循环兜底。澄清续接轮（args.answers 非空 = 用户已回答过
    # 澄清问题）若仍 needs_clarify=true，强制推进——根治「卡在理解学习目标反复追问
    # 不向下推进」。真实 LLM 在含 answers 时仍常保守地返回 needs_clarify=true（回答
    # 稍模糊即判定），而 /clarify 每次都从 understand_intent 重跑、链路无任何轮次上限
    # 或强制推进机制 -> 死循环。此处为不依赖 LLM 自觉的确定性兜底：已回答过即不再
    # 二次追问，仍缺失的维度以中性默认补全（不编造用户具体意图）。
    if args.get("answers") and data.get("needs_clarify"):
        data["needs_clarify"] = False
        data["clarify_questions"] = []
        if not data.get("daily_minutes"):
            data["daily_minutes"] = 60  # 中性默认：每日 1 小时（schedule_plan 依赖）
        if not data.get("level"):
            data["level"] = "未明确"
        if not data.get("goal"):
            topic = (data.get("topic") or "").strip() or "该主题"
            data["goal"] = f"系统学习并掌握 {topic}"
        # deadline/start_date 保持现状（可选字段，None 不阻断生成；过期 deadline 已在上面置 None）
    return data


understand_intent_tool = Tool(
    name="understand_intent",
    llm=True,
    skill_name="deep-tutor-intent",  # step 5 Skill 接入后覆盖 inline_prompt
    output_model=IntentData,
    inline_prompt=PROMPT,
    post_process=_validate_intent,
    # 降级默认：无法理解时触发澄清
    default_data={
        "topic": "",
        "level": None,
        "goal": None,
        "daily_minutes": None,
        "deadline": None,
        "start_date": None,
        "needs_clarify": True,
        "clarify_questions": ["无法理解你的学习需求，请补充：你想学什么主题？当前基础如何？每天能投入多少时间？"],
    },
)
