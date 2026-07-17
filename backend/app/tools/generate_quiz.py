"""generate_quiz 工具（plan/02 §8，P2）。

学习方案 -> 按知识点多题型测验题。LLM 生成 QuizSpec，前端交互判分。
- 题型不支持 -> pydantic validator 降级选择题（plan/02 §8 errors）。
- LLM 失败/无 provider -> 降级 default_quiz_from_plan（纯 Python 默认）。

简化 MVP（同 slides/materials 模式）：一次 LLM 调用 -> 规约 -> 端点返回 JSON ->
前端测验弹窗（choice 即时判分；fill/short 展示参考答案自评）。
延后：quizzes/quiz_attempts 表持久化、essay 题型、掌握度联动判分。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agent.registry import Tool

# MVP 支持题型（essay 无法自动判分，降级为 choice）
QUIZ_TYPES = frozenset({"choice", "fill", "short"})

_DEFAULT_TITLE = "学习测验"


class QuizItem(BaseModel):
    """单题。choice 有 options（answer 为正确选项原文）；fill/short 的 answer 为参考答案。"""

    node_id: str = ""
    node_name: str = ""
    type: str = "choice"
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    explanation: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> str:
        """题型不支持 -> 降级选择题（plan/02 §8 errors）。"""
        return v if isinstance(v, str) and v in QUIZ_TYPES else "choice"


class QuizSpec(BaseModel):
    """测验规约：LLM 输出 schema + 端点响应。"""

    title: str = _DEFAULT_TITLE
    quizzes: list[QuizItem]


def default_quiz_from_plan(plan: dict[str, Any]) -> QuizSpec:
    """从 plan 构造默认测验（降级用，纯 Python 确定性）：每节点一道自测简答题。

    无 LLM 无法编造题目与答案，故仅生成「自述要点」式开放题，answer 留空，
    前端展示为自评题（ honest 降级，不编造）。
    """
    intent = plan.get("intent") or {}
    title = f"{intent.get('topic') or '学习方案'}测验"
    items: list[QuizItem] = []

    nodes = (plan.get("knowledge_map") or {}).get("nodes") or []
    for n in nodes[:20]:
        name = n.get("name", "")
        if not name:
            continue
        items.append(
            QuizItem(
                node_id=str(n.get("id", "")),
                node_name=name,
                type="short",
                question=f"请用自己的话简述「{name}」的核心要点（自评）。",
                answer="",
                explanation="（降级默认题：无 LLM 生成参考答案，请对照自学材料自评）",
            )
        )

    if not items:
        # knowledge_map 为空时按阶段出题
        for s in (plan.get("stages") or [])[:10]:
            name = s.get("name", "")
            if name:
                items.append(
                    QuizItem(
                        node_id="",
                        node_name=name,
                        type="short",
                        question=f"请简述阶段「{name}」中学到的要点（自评）。",
                        answer="",
                        explanation="（降级默认题：无 LLM 生成参考答案，请对照自学材料自评）",
                    )
                )
    if not items:
        items.append(QuizItem(question="（方案无知识点信息，无法生成测验）", answer=""))
    return QuizSpec(title=title, quizzes=items)


# --- quiz 题源联动（iter 49）：单元「检查理解」自测题 -> generate_quiz 题源 ---

_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.、)])\s*")


def extract_check_questions(
    unit_contents: list[tuple[str, str]], per_unit: int = 4, total: int = 20
) -> list[dict]:
    """从单元 content_md 提取「### 检查理解」自测题（iter 49，单元题源喂 generate_quiz）。

    取该小节（### 检查理解 到下一个 ### 之间）的非空行，去列表标记（-/* / 1. 等）；
    每单元 ≤per_unit、总计 ≤total。无该小节或无题 -> 跳过该单元。
    返回 [{"unit": 单元名, "questions": [问题原文]}]，供 quiz 工具 prompt 改编为可判分题。
    """
    result: list[dict] = []
    count = 0
    for title, content_md in unit_contents:
        if count >= total:
            break
        questions: list[str] = []
        in_section = False
        for line in (content_md or "").splitlines():
            s = line.strip()
            if s.startswith("###"):
                if in_section:
                    break  # 下一个小节 -> 结束
                in_section = "检查理解" in s
                continue
            if not in_section or not s:
                continue
            q = _LIST_MARKER.sub("", s).strip()
            if q:
                questions.append(q)
            if len(questions) >= per_unit:
                break
        if questions:
            count += len(questions)
            result.append({"unit": title, "questions": questions})
    return result


# --- LLM 工具（iter 37）：plan -> QuizSpec，复用 loop 修复链 ---

PROMPT = """你是 Deep-tutor 的「测验题生成」工具。任务：从学习方案（args.plan）按知识点多题型出题，生成测验规约（QuizSpec JSON）。非交互，直接输出规约。

输入：user message 的 args.plan（学习方案 dict，含 intent{topic,level,goal}、knowledge_map{nodes:[{id,name,difficulty,must_learn}]}、stages[{name,topics[{name}]}]）。可选 args.unit_check_questions（自学单元「检查理解」自测题：[{"unit":"单元名","questions":["问题"]}]，iter 49 题源联动）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"测验标题","quizzes":[{"node_id":"节点id","node_name":"节点名","type":"choice|fill|short","question":"题干","options":["选项A","选项B","选项C","选项D"],"answer":"正确选项原文或参考答案","explanation":"解析"}]}

规则：
- 出题来源 plan.knowledge_map.nodes（must_learn 优先；map 为空时用 stages.topics）；每节点 1-2 题，总 5-20 题。
- 若有 args.unit_check_questions：优先将这些单元自测题改编为可判分题（choice/fill/short，补 answer 与 explanation），尽量覆盖各单元；不足 5 题时按知识点补足。改编题与单元知识点相关时 node_id/node_name 填对应节点，无对应节点可留空。
- 题型混合：choice（单选，options 3-4 个，answer 必须是 options 中正确选项的原文，且仅一个正确）、fill（填空，answer 为缺失的关键词语）、short（简答，answer 为参考要点）。
- 难度贴合节点 difficulty（1-2 概念辨析/记忆，3 应用，4-5 综合）。
- options 中干扰项须合理（常见误解），不凑数。
- explanation 一两句说明为什么。
- 不编造 plan 中没有的知识点；不超出资料范围。

仅返回符合 schema 的 JSON 对象。
"""

# 工具降级默认（LLM 失败时的静态兜底；端点在非 ok 时改用 default_quiz_from_plan(plan)）
_DEFAULT_QUIZ_DATA = {
    "title": _DEFAULT_TITLE,
    "quizzes": [
        {
            "node_id": "",
            "node_name": "",
            "type": "short",
            "question": "（测验题生成失败，已降级）请回顾学习方案并自述要点。",
            "options": [],
            "answer": "",
            "explanation": "",
        }
    ],
}


generate_quiz_tool = Tool(
    name="generate_quiz",
    llm=True,
    skill_name=None,  # 用聚焦 inline_prompt（同 slides/materials spec 生成模式）
    output_model=QuizSpec,
    inline_prompt=PROMPT,
    default_data=_DEFAULT_QUIZ_DATA,
)
