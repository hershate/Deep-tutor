"""成本归集辅助（plan/01 §三 成本计量 / plan/08 §三 ToolOutput.metadata）。

- usage -> cost（按 app/llm/pricing.py）
- 跨修复重试累加 usage
- 写 api_usage 表（可选，ctx.db 提供时；step 6 完整接入）
"""

from __future__ import annotations

from app.llm.base import LLMUsage
from app.llm.pricing import estimate_cost
from app.tools.base import ToolMetadata


def usage_cost(usage: LLMUsage) -> float:
    return estimate_cost(usage, usage.model)


def accumulate_usage(total: LLMUsage, add: LLMUsage) -> None:
    """把单次调用的 usage 累加到 total（跨修复重试）。"""
    total.input_tokens += add.input_tokens
    total.output_tokens += add.output_tokens
    total.cache_read_input_tokens += add.cache_read_input_tokens
    total.cache_creation_input_tokens += add.cache_creation_input_tokens
    if add.model and not total.model:
        total.model = add.model


def build_metadata(total: LLMUsage, model: str = "") -> ToolMetadata:
    used_model = model or total.model
    return ToolMetadata(
        tokens_in=total.input_tokens,
        tokens_out=total.output_tokens,
        cache_read=total.cache_read_input_tokens,
        cache_write=total.cache_creation_input_tokens,
        cost=estimate_cost(total, used_model),
        model=used_model,
    )


def record_api_usage(db, usage: LLMUsage, provider: str = "anthropic") -> float:
    """写 api_usage 表（若 db 提供），返回 cost。step 6 在编排器中按需调用。"""
    cost = estimate_cost(usage, usage.model)
    if db is not None:
        from app.db.models import ApiUsage

        db.add(
            ApiUsage(
                provider=provider,
                model=usage.model,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                cache_read=usage.cache_read_input_tokens,
                cache_write=usage.cache_creation_input_tokens,
                cost=cost,
            )
        )
        db.commit()
    return cost
