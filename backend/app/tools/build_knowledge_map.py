"""build_knowledge_map 工具（plan/02 §3，LLM，Skill=deep-tutor-knowledge-map）。

拆解知识点与依赖关系。方法论移植 study-planner Step 2（核心前置/核心知识/进阶扩展/实战项目）。
post_process：环检测(DFS)->断成 DAG；悬空边丢弃；节点数超 MAX_NODES 截断+标注。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent.registry import Tool

MAX_NODES = 100  # 09 §二十

PROMPT = """你是 Deep-tutor 的「知识图谱构建」工具。任务：依据学习意图与解析资料，
拆解出知识点的依赖图谱，供后续阶段设计。

要求：
- nodes：知识点列表，每个含 id（短标识，如 k1）、name、difficulty(1-5)、must_learn(bool)、
  parent_id（依赖的前置知识点 id，无则 null）。
- edges：依赖关系，from/to 为 node id，relation 简述（如「前置」「支撑」）。
- 覆盖四类：核心前置、核心知识、进阶扩展、实战项目；标注 must_learn。
- 节点数 8-30 个为宜；不要编造资料中完全无关的知识点。
- id 必须唯一；edge 的 from/to 必须引用已存在的 node id。

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


class KnowledgeEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    relation: str = ""


class KnowledgeMapData(BaseModel):
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge] = []


def clean_map(data: dict[str, Any]) -> dict[str, Any]:
    """后处理：断环、去悬空边、节点截断。"""
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

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
