"""kb_answer 工具（iter 216：知识库问答 POST /kb/chat）。

库内文档 -> LLM 仅依据文档回答用户问题（KbAnswerSpec JSON：answer + used_doc_ids 引用）。
- prompt 强约束：仅依据所给文档、不足即明说、不编造、引用标注出处。
- post_process 白名单过滤幻觉引用（used_doc_ids 仅保留上下文实给的 doc_id）。
- 无 default_data：问答没有 honest 的静态降级（不编造答案）——失败由端点映射 502/504。
- 不在 PIPELINE（端点直调，同 generate_quiz）；无 skill_name（inline_prompt 直供）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent.registry import Tool
from app.tools.base import ToolInput


class KbAnswerSpec(BaseModel):
    """问答规约：LLM 输出 schema + 端点响应源。"""

    answer: str
    used_doc_ids: list[int] = Field(default_factory=list)


PROMPT = """你是 Deep-tutor 的「知识库问答」工具。任务：仅依据用户知识库中给定的文档内容，回答用户问题。非交互，直接输出规约。

输入：user message 的 args.question（用户问题）与 args.documents（参考文档列表：[{"doc_id":文档id,"title":"标题","text":"解析文本"}]，text 尾部可能带「…[已截断]」标注表示原文未完）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"answer":"回答正文（中文 Markdown）","used_doc_ids":[实际参考的文档id]}

规则：
- 仅依据 args.documents 中的内容回答；文档里没有的信息不要编造，也不要用外部知识补充。
- 文档内容不足以回答时，在 answer 中明确说明「知识库文档中没有足够信息」，并指出还缺少什么。
- answer 用中文 Markdown 组织（适度使用要点列表/小标题），直接回答问题本身。
- 引用具体文档内容时以「《标题》」标注出处。
- used_doc_ids 只列实际用于回答的 doc_id（必须是 args.documents 中存在的 id）；一个都没用到则返回空列表。

仅返回符合 schema 的 JSON 对象。
"""


def _clip_used_ids(data: dict, inp: ToolInput) -> dict:
    """used_doc_ids 白名单过滤：仅保留上下文实给的 doc_id（防幻觉引用不存在的文档）。"""
    allowed = {
        d["doc_id"]
        for d in (inp.args.get("documents") or [])
        if isinstance(d, dict) and isinstance(d.get("doc_id"), int)
    }
    data["used_doc_ids"] = [
        i for i in (data.get("used_doc_ids") or []) if isinstance(i, int) and i in allowed
    ]
    return data


kb_answer_tool = Tool(
    name="kb_answer",
    llm=True,
    output_model=KbAnswerSpec,
    inline_prompt=PROMPT,
    post_process=_clip_used_ids,
    # 无 default_data：问答失败不编造静态答案，端点按错误码映射 502/504
)
