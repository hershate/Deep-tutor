"""build_knowledge_map 工具（plan/02 §3，LLM，Skill=deep-tutor-knowledge-map）。

拆解知识点与依赖关系。方法论移植 study-planner Step 2（核心前置/核心知识/进阶扩展/实战项目）。
post_process：环检测(DFS)->断成 DAG；悬空边丢弃；节点数超 MAX_NODES 截断+标注。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agent.registry import Tool
from app.tools.base import ToolInput

MAX_NODES = 100  # 09 §二十

PROMPT = """你是 Deep-tutor 的「知识图谱构建」工具。任务：依据学习意图与解析资料，
拆解出知识点的依赖图谱，供后续阶段设计。

要求：
- nodes：知识点列表，每个含 id（短标识，如 k1）、name、difficulty(1-5，按下方评分细则)、
  must_learn(bool)、parent_id（依赖的前置知识点 id，无则 null）。
- edges：依赖关系，from/to 为 node id，relation 简述（如「前置」「支撑」）。
- 覆盖四类：核心前置、核心知识、进阶扩展、实战项目；标注 must_learn。
- 节点数 8-30 个为宜；不要编造资料中完全无关的知识点。
- id 必须唯一；edge 的 from/to 必须引用已存在的 node id。

难度评分细则（difficulty 1-5）：
- 1 常识/已知概念，几乎不需专门学习（如基本术语、日常已接触的工具）。
- 2 入门，短时了解即可上手（如基础语法、简单命令）。
- 3 进阶，需系统学习加练习才能掌握（如核心机制、常用范式）。
- 4 较难，需较多练习与小型项目巩固（如综合应用、调优、排错）。
- 5 高难/综合：通常说明该知识点过宽，应优先拆分（见下），而非保留单个大节点。

知识点拆分（重要）：
- 若一个知识点过于宽泛、或难度本会达到 5（例如「掌握 React 全部生态」「机器学习」这类
  单节点覆盖远超约 2 小时学习量的复合主题），必须拆成多个更小、更聚焦的知识点节点，
  用 edges 表达这些子点之间的先后依赖；拆分后子点 difficulty 通常落在 3-4，且不再保留原巨型节点。
- 自检：逐个复核 difficulty=5 的节点——它是否由多个可独立学习的子点组成？若是，拆分之；
  确属不可分的原子高难点（少见）方可保留 difficulty=5。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"nodes":[{"id":"k1","name":"...","difficulty":2,"must_learn":true,"parent_id":null}],
 "edges":[{"from":"k1","to":"k2","relation":"前置"}]}
"""


class KnowledgeNode(BaseModel):
    id: str
    name: str
    difficulty: int = 1
    must_learn: bool = False
    parent_id: str | None = None

    @field_validator("difficulty", mode="before")
    @classmethod
    def _coerce_difficulty(cls, v):
        # iter 347：宽容解析 difficulty（LLM 偶发返回字符串/None）；范围夹紧由 clean_map 兜底
        if v is None:
            return 1
        try:
            return int(v)
        except (TypeError, ValueError):
            return 1


class KnowledgeEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    relation: str = ""


class KnowledgeMapData(BaseModel):
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge] = []


def clean_map(data: dict[str, Any], _inp: ToolInput) -> dict[str, Any]:
    """后处理：断环、去悬空边、节点截断。"""
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    # iter 245：重复节点 id 去重（保首个）——同 id 多节点时 edges/下游按 id 引用
    # 语义含糊（parent 指向不确定），且 prompt 已要求 id 唯一（违约属 LLM 输出病害）
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for n in nodes:
        nid = n.get("id")
        if nid and nid in seen_ids:
            continue
        if nid:
            seen_ids.add(nid)
        deduped.append(n)
    nodes = deduped

    # iter 347：difficulty 夹紧到 [1,5]（prompt 给了评分细则；LLM 偶发越界/非整数时兜底）
    for n in nodes:
        try:
            d = int(n.get("difficulty") or 1)
        except (TypeError, ValueError):
            d = 1
        n["difficulty"] = max(1, min(5, d))

    valid_ids = {n.get("id") for n in nodes if n.get("id")}
    # 丢弃悬空边（端点不在 nodes）
    edges = [e for e in edges if e.get("from") in valid_ids and e.get("to") in valid_ids]
    # 断环：丢弃会形成环的边 -> DAG
    edges = _break_cycles(edges)

    truncated = False
    if len(nodes) > MAX_NODES:
        nodes = nodes[:MAX_NODES]
        valid_ids = {n.get("id") for n in nodes}
        edges = [e for e in edges if e.get("from") in valid_ids and e.get("to") in valid_ids]
        truncated = True

    data["nodes"] = nodes
    data["edges"] = edges
    if truncated:
        data["_note"] = f"节点数超 {MAX_NODES}，已截断"
    return data


def _break_cycles(edges: list[dict]) -> list[dict]:
    """逐边加入，若 to->from 已可达则丢弃该边（保持 DAG）。"""
    adj: dict[str, list[str]] = {}
    kept: list[dict] = []
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f is None or t is None or f == t:
            continue
        if _has_path(adj, t, f):
            continue  # 加入会成环，丢弃
        adj.setdefault(f, []).append(t)
        kept.append(e)
    return kept


def _has_path(adj: dict[str, list[str]], src: str, dst: str) -> bool:
    stack = [src]
    seen: set[str] = set()
    while stack:
        u = stack.pop()
        if u == dst:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, []))
    return False


build_knowledge_map_tool = Tool(
    name="build_knowledge_map",
    llm=True,
    skill_name="deep-tutor-knowledge-map",
    output_model=KnowledgeMapData,
    inline_prompt=PROMPT,
    post_process=clean_map,
    default_data={"nodes": [], "edges": []},
)
