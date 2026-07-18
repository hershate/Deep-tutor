"""Anki 卡片包导出构建器（P5，plan/README 输出形式 5「基于知识点」）。

卡片来源：方案的测验题（content.quizzes，generate_quiz 按知识点出题）——
题面即知识点校验，含 answer/explanation，是现成的高质量卡片材料：
- 正面：题干（choice 题附选项列表）；
- 背面：答案 + 解析（有解析时）。

产出 .apkg（genanki，双击即可导入 Anki）。无测验题 -> ValueError（API 层转 no_quizzes）。
文本全部 HTML 转义（Anki 卡片内容按 HTML 渲染，防注入/防样式破坏）。
"""

from __future__ import annotations

import html
import tempfile
from pathlib import Path

import genanki

_MODEL_ID = 2026071801

_MODEL = genanki.Model(
    _MODEL_ID,
    "Deep-tutor 知识点卡片",
    fields=[{"name": "Front"}, {"name": "Back"}],
    templates=[
        {
            "name": "卡片",
            "qfmt": "<div class='front'>{{Front}}</div>",
            "afmt": "{{FrontSide}}<hr id='answer'><div class='back'>{{Back}}</div>",
        }
    ],
    css=".card{font-family:sans-serif;font-size:18px;line-height:1.6}.front b{color:#2563eb}"
    ".back{color:#334155}.opts{margin-top:8px}.exp{margin-top:8px;color:#64748b;font-size:15px}",
)


def _deck_id(plan_id: int) -> int:
    # 由 plan_id 派生稳定 deck id（同一方案重复导出进同一牌组；Anki 按 id 去重更新）
    return 2026071800 + plan_id % 100000


def _card_front(quiz: dict) -> str:
    q = html.escape(str(quiz.get("question", "")).strip())
    parts = [f"<b>Q</b>：{q}"]
    options = quiz.get("options") or []
    if options:
        items = "".join(f"<div>{html.escape(str(o))}</div>" for o in options)
        parts.append(f"<div class='opts'>{items}</div>")
    return "".join(parts)


def _card_back(quiz: dict) -> str:
    answer = html.escape(str(quiz.get("answer", "")).strip())
    parts = [f"<b>A</b>：{answer}"]
    explanation = str(quiz.get("explanation", "") or "").strip()
    if explanation:
        parts.append(f"<div class='exp'>{html.escape(explanation)}</div>")
    return "".join(parts)


def build_apkg(plan_id: int, content: dict) -> tuple[bytes, int]:
    """生成 .apkg 字节。返回 (apkg_bytes, 卡片数)。无有效测验题抛 ValueError。"""
    quizzes = [q for q in (content.get("quizzes") or []) if isinstance(q, dict)]
    quizzes = [q for q in quizzes if str(q.get("question", "")).strip() and str(q.get("answer", "")).strip()]
    if not quizzes:
        raise ValueError("方案暂无测验题（quizzes 为空），请先生成测验再导出 Anki")

    title = str(content.get("title") or content.get("topic") or "学习计划")
    deck = genanki.Deck(_deck_id(plan_id), f"Deep-tutor::{title}")
    for q in quizzes:
        deck.add_note(genanki.Note(model=_MODEL, fields=[_card_front(q), _card_back(q)]))

    with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        genanki.Package(deck).write_to_file(str(tmp_path))
        return tmp_path.read_bytes(), len(quizzes)
    finally:
        tmp_path.unlink(missing_ok=True)
