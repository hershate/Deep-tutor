"""Skill 加载器测试（plan/05 §三）。

纯函数 load_skills + 临时目录，不触模块缓存；覆盖 frontmatter/无 frontmatter/
回退/空正文/缺目录/无 SKILL.md/重名/剥 frontmatter/BOM/CRLF。
另含真实 deep-tutor-intent Skill 的加载与 loop 取用集成测试。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent import ExecContext, ToolRegistry, run_tool
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.skills.loader import load_skills
from app.tools.base import ToolInput
from app.tools.understand_intent import understand_intent_tool

_PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _write_skill(root, name: str, content: str) -> None:
    """在 root/<name>/SKILL.md 写入 content。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")


def test_load_skills_frontmatter(tmp_path):
    _write_skill(
        tmp_path, "deep-tutor-x", "---\nname: deep-tutor-x\ndescription: d\n---\n# Purpose\n做意图理解\n"
    )
    reg = load_skills(tmp_path)
    assert "deep-tutor-x" in reg
    assert reg["deep-tutor-x"].startswith("# Purpose")
    assert "做意图理解" in reg["deep-tutor-x"]


def test_load_skills_no_frontmatter(tmp_path):
    _write_skill(tmp_path, "deep-tutor-y", "# Purpose\n只正文\n")
    reg = load_skills(tmp_path)
    assert "deep-tutor-y" in reg  # 键=目录名
    assert reg["deep-tutor-y"].startswith("# Purpose")


def test_load_skills_name_fallback_to_dir(tmp_path):
    # frontmatter 无 name: -> 键回退目录名
    _write_skill(tmp_path, "deep-tutor-z", "---\ndescription: d\n---\n# Purpose\n正文\n")
    reg = load_skills(tmp_path)
    assert list(reg.keys()) == ["deep-tutor-z"]


def test_load_skills_empty_body_skipped(tmp_path):
    _write_skill(tmp_path, "empty", "---\nname: empty\n---\n")
    assert load_skills(tmp_path) == {}


def test_load_skills_missing_dir(tmp_path):
    assert load_skills(tmp_path / "nope") == {}


def test_load_skills_dir_without_skill_md_skipped(tmp_path):
    d = tmp_path / "only-readme"
    d.mkdir()
    (d / "README.md").write_text("readme", encoding="utf-8")
    assert load_skills(tmp_path) == {}


def test_load_skills_duplicate_name_last_wins(tmp_path):
    _write_skill(tmp_path, "a-skill", "---\nname: dup\n---\n# A\n")
    _write_skill(tmp_path, "b-skill", "---\nname: dup\n---\n# B\n")
    reg = load_skills(tmp_path)
    assert list(reg.keys()) == ["dup"]
    assert reg["dup"].startswith("# B")  # sorted: a-skill < b-skill -> 后者覆盖


def test_load_skills_strips_frontmatter(tmp_path):
    _write_skill(tmp_path, "s", "---\nname: s\n---\n# Purpose\n正文\n")
    body = load_skills(tmp_path)["s"]
    assert not body.startswith("---")
    assert "name: s" not in body


def test_load_skills_bom(tmp_path):
    _write_skill(tmp_path, "bom-x", "﻿---\nname: bom-x\n---\n# Purpose\n带 BOM\n")
    reg = load_skills(tmp_path)
    assert "bom-x" in reg
    assert "带 BOM" in reg["bom-x"]


def test_load_skills_crlf(tmp_path):
    _write_skill(
        tmp_path, "crlf-x", "---\r\nname: crlf-x\r\ndescription: d\r\n---\r\n# Purpose\r\nCRLF 正文\r\n"
    )
    reg = load_skills(tmp_path)
    assert "crlf-x" in reg
    assert "CRLF 正文" in reg["crlf-x"]
    assert "\r" not in reg["crlf-x"]


# --- 真实 deep-tutor-intent Skill 集成 ---


def test_real_deep_tutor_intent_skill_loads():
    """真实 skills/deep-tutor-intent/SKILL.md 可加载：键=deep-tutor-intent，正文非空，已剥 frontmatter。"""
    reg = load_skills(_PROJECT_SKILLS)
    assert "deep-tutor-intent" in reg
    body = reg["deep-tutor-intent"]
    assert body.strip()
    assert not body.startswith("---")  # frontmatter 已剥
    assert "name: deep-tutor-intent" not in body  # frontmatter 内容不在正文


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        self.calls.append({"system": system, "cache_system": cache_system})
        return self._responses.pop(0) if self._responses else LLMResponse(
            content="{}", usage=LLMUsage(model=model or "claude-sonnet-5")
        )


def test_real_intent_skill_used_as_system_prompt():
    """真实 Skill 加载后，loop 用 Skill 正文作 system prompt（覆盖 inline_prompt）。"""
    reg = load_skills(_PROJECT_SKILLS)
    skill_body = reg["deep-tutor-intent"]

    provider = _ScriptedProvider(
        [LLMResponse(content='{"topic":"React","needs_clarify":false}', usage=LLMUsage(model="claude-sonnet-5"))]
    )
    tools = ToolRegistry()
    tools.register(understand_intent_tool)
    ctx = ExecContext(
        session_id="sess_test",
        trace_id="trace_test",
        registry=tools,
        llm_provider=provider,
        skill_registry=reg,
    )
    out = asyncio.run(run_tool("understand_intent", ToolInput(tool="understand_intent", session_id="sess_test"), ctx))
    assert out.status == "ok"
    assert out.data["topic"] == "React"
    # Skill 正文作 system prompt（非 inline_prompt）
    assert provider.calls[0]["system"] == skill_body
    assert provider.calls[0]["system"] != understand_intent_tool.inline_prompt
    assert provider.calls[0]["cache_system"] is True
