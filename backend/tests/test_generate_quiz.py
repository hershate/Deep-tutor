"""generate_quiz 工具测试（iter 37，P2）。"""

from __future__ import annotations

import asyncio

from app.agent import ExecContext, ToolRegistry, run_tool
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.generate_quiz import (
    QuizItem,
    default_quiz_from_plan,
    ensure_quiz_coverage,
    extract_check_questions,
    generate_quiz_tool,
)


def test_default_quiz_from_plan_nodes():
    """默认降级：knowledge_map 每节点一道自测简答题，不编造答案。"""
    plan = {
        "intent": {"topic": "React"},
        "knowledge_map": {
            "nodes": [
                {"id": "k1", "name": "JS", "difficulty": 1},
                {"id": "k2", "name": "组件", "difficulty": 3},
            ],
            "edges": [],
        },
    }
    spec = default_quiz_from_plan(plan)
    assert "React" in spec.title
    assert len(spec.quizzes) == 2
    assert spec.quizzes[0].node_name == "JS"
    assert spec.quizzes[0].type == "short"
    assert spec.quizzes[0].answer == ""  # 不编造参考答案


def test_default_quiz_empty_plan_fallback():
    """空 plan -> 回退单题占位，不崩。"""
    spec = default_quiz_from_plan({})
    assert len(spec.quizzes) >= 1


def test_quiz_item_invalid_type_coerced():
    """题型不支持 -> 降级：有合法 options+answer 的降 choice（可判分），否则降 short（iter 365 C6）。"""
    # 有合法 options+answer 的不支持题型 -> choice
    assert QuizItem(question="q", type="essay", options=["a", "b"], answer="a").type == "choice"
    assert QuizItem(question="q", type="weird", options=["a", "b"], answer="a").type == "choice"
    # 无 options/answer 的不支持题型 -> short（不可判分的 choice 无意义，防前端无法判分）
    assert QuizItem(question="q", type="essay").type == "short"
    assert QuizItem(question="q", type=None).type == "short"  # type: ignore[arg-type]
    # 合法 type 不变
    assert QuizItem(question="q", type="fill").type == "fill"
    # 合法 choice（options+answer 齐全）不变
    assert QuizItem(question="q", type="choice", options=["a", "b"], answer="a").type == "choice"


def test_quiz_item_choice_with_bad_options_degrades_to_short():
    """iter 365 C6：choice 题 options<2 / answer 不在 options / 无 answer -> 降 short（防无法判分/误判）。"""
    assert QuizItem(question="q", type="choice", options=[], answer="a").type == "short"  # 空 options
    assert QuizItem(question="q", type="choice", options=["only"], answer="only").type == "short"  # <2
    assert QuizItem(question="q", type="choice", options=["a", "b"], answer="c").type == "short"  # answer 不在
    assert QuizItem(question="q", type="choice", options=["a", "b"]).type == "short"  # 无 answer


# --- LLM 工具：plan -> QuizSpec，复用 run_tool 修复链 ---


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        return self._r.pop(0) if self._r else LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))


def _ctx(provider: LLMProvider) -> ExecContext:
    reg = ToolRegistry()
    reg.register(generate_quiz_tool)
    return ExecContext(session_id="s", trace_id="t", registry=reg, llm_provider=provider)


def test_tool_registered_in_default_registry():
    reg = default_registry()
    assert reg.has("generate_quiz")
    assert reg.get("generate_quiz").llm is True


def test_tool_valid_spec():
    """LLM 返回合法 QuizSpec JSON -> run_tool ok + data=spec。"""
    spec_json = (
        '{"title":"React 测验","quizzes":['
        '{"node_id":"k1","node_name":"JS","type":"choice","question":"JS 中声明常量用？",'
        '"options":["var","let","const","function"],"answer":"const","explanation":"const 声明常量。"},'
        '{"node_id":"k2","node_name":"组件","type":"short","question":"什么是函数组件？",'
        '"options":[],"answer":"返回 JSX 的函数","explanation":""}]}'
    )
    provider = _ScriptedProvider([LLMResponse(content=spec_json, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool(
            "generate_quiz",
            ToolInput(tool="generate_quiz", session_id="s", args={"plan": {"intent": {"topic": "React"}}}),
            _ctx(provider),
        )
    )
    assert out.status == "ok"
    assert out.data["title"] == "React 测验"
    assert len(out.data["quizzes"]) == 2
    assert out.data["quizzes"][0]["answer"] == "const"
    assert out.data["quizzes"][1]["type"] == "short"


def test_tool_invalid_type_in_llm_output_coerced():
    """LLM 输出不支持题型（essay）无 options -> 降 short（可判分性，iter 365 C6），仍 ok 不触发修复。"""
    spec_json = '{"title":"t","quizzes":[{"question":"q","type":"essay","answer":"a"}]}'
    provider = _ScriptedProvider([LLMResponse(content=spec_json, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool(
            "generate_quiz",
            ToolInput(tool="generate_quiz", session_id="s", args={"plan": {}}),
            _ctx(provider),
        )
    )
    assert out.status == "ok"
    assert out.data["quizzes"][0]["type"] == "short"  # essay 无 options -> short（非 choice）


def test_tool_invalid_degrades_to_default():
    """LLM 持续返回非法 JSON -> 修复重试耗尽 -> 降级 repaired + 默认规约。"""
    provider = _ScriptedProvider(
        [LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5"))] * 5
    )
    out = asyncio.run(
        run_tool(
            "generate_quiz",
            ToolInput(tool="generate_quiz", session_id="s", args={"plan": {}}),
            _ctx(provider),
        )
    )
    assert out.status == "repaired"
    assert out.data["title"] == "学习测验"  # 默认降级规约
    assert len(out.data["quizzes"]) >= 1


# --- iter 49：extract_check_questions（单元「检查理解」-> quiz 题源）---


def test_extract_check_questions_basic():
    """提取 ### 检查理解 小节题目（去列表标记；止于下一个 ###）。"""
    md = (
        "### 概述\n\n内容。\n\n### 检查理解\n\n"
        "1. 什么是闭包？\n2. let 与 var 的区别？\n- 箭头函数 this 指向？\n\n"
        "### 总结\n\n要点。这不是题。\n"
    )
    out = extract_check_questions([("JS 基础", md)])
    assert out == [{"unit": "JS 基础", "questions": ["什么是闭包？", "let 与 var 的区别？", "箭头函数 this 指向？"]}]


def test_extract_check_questions_no_section_or_empty():
    """无小节 / 小节无题 / 空内容 -> 跳过该单元。"""
    assert extract_check_questions([("a", "### 概述\n\n无小节。")]) == []
    assert extract_check_questions([("a", "### 检查理解\n\n\n### 总结\n\nx")]) == []
    assert extract_check_questions([("a", "")]) == []
    assert extract_check_questions([]) == []


def test_extract_check_questions_caps():
    """每单元 ≤4、总计 ≤20。"""
    md = "### 检查理解\n\n" + "\n".join(f"{i}. 问题{i}？" for i in range(1, 8))
    out = extract_check_questions([("u1", md)])
    assert len(out[0]["questions"]) == 4
    units = [(f"u{i}", md) for i in range(6)]  # 6 单元 × 4 = 24 -> 截 20
    out = extract_check_questions(units)
    assert sum(len(u["questions"]) for u in out) == 20


# --- iter 349：ensure_quiz_coverage（完整性——每知识点必有题）---


def test_ensure_quiz_coverage_fills_missing_nodes():
    """LLM 漏掉部分节点 -> post_process 补自评题，保证每节点至少一题（根治缺题）。"""
    plan = {"knowledge_map": {"nodes": [
        {"id": "k1", "name": "JS"},
        {"id": "k2", "name": "组件"},
        {"id": "k3", "name": "Hooks"},
    ]}}
    data = {"title": "t", "quizzes": [  # LLM 只出了 k1、k2，漏 k3
        {"node_id": "k1", "node_name": "JS", "type": "choice", "question": "q1", "options": ["a", "b"], "answer": "a"},
        {"node_id": "k2", "node_name": "组件", "type": "choice", "question": "q2", "options": ["a", "b"], "answer": "a"},
    ]}
    out = ensure_quiz_coverage(data, ToolInput(tool="generate_quiz", session_id="s", args={"plan": plan}))
    by_node = {q["node_id"] for q in out["quizzes"]}
    assert by_node == {"k1", "k2", "k3"}  # k3 被补上
    filled = [q for q in out["quizzes"] if q["node_id"] == "k3"]
    assert filled[0]["type"] == "short"  # 补的是自评题
    assert "Hooks" in filled[0]["question"]


def test_ensure_quiz_coverage_no_op_when_complete():
    """LLM 已覆盖全部节点 -> 不追加（无重复）。"""
    plan = {"knowledge_map": {"nodes": [{"id": "k1", "name": "JS"}, {"id": "k2", "name": "组件"}]}}
    data = {"quizzes": [
        {"node_id": "k1", "type": "choice", "question": "q1", "answer": "a"},
        {"node_id": "k2", "type": "choice", "question": "q2", "answer": "a"},
    ]}
    out = ensure_quiz_coverage(data, ToolInput(tool="generate_quiz", session_id="s", args={"plan": plan}))
    assert len(out["quizzes"]) == 2  # 无追加


def test_ensure_quiz_coverage_via_run_tool():
    """端到端：run_tool generate_quiz 走 post_process——LLM 漏节点时自动补全。"""
    plan = {"intent": {"topic": "T"}, "knowledge_map": {"nodes": [
        {"id": "k1", "name": "A"}, {"id": "k2", "name": "B"}, {"id": "k3", "name": "C"}]}}
    # LLM 只返回 k1 一题
    spec_json = '{"title":"t","quizzes":[{"node_id":"k1","node_name":"A","type":"choice","question":"q","options":["a"],"answer":"a"}]}'
    provider = _ScriptedProvider([LLMResponse(content=spec_json, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool("generate_quiz", ToolInput(tool="generate_quiz", session_id="s", args={"plan": plan}), _ctx(provider))
    )
    assert out.status == "ok"
    by_node = {q["node_id"] for q in out.data["quizzes"]}
    assert {"k1", "k2", "k3"} <= by_node  # k2/k3 被 post_process 补上
