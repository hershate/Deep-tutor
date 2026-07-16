"""模型计价表（成本计量 Pillar 3，plan/01 §三）。

单价单位：USD / 1M tokens。按模型族前缀匹配，未知模型返回 None（estimate_cost 记 0，不编造）。

> ⚠️ 价格随 Anthropic 调整而变动；以下为公开价目（ephemeral 5min cache：
> cache_read ≈ 0.1×input，cache_write ≈ 1.25×input）。**使用前请核对**
> https://www.anthropic.com/pricing 并按需更新。price 字段可在 settings 扩展为可配置。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.base import LLMUsage


@dataclass(frozen=True)
class Pricing:
    """每 1M tokens 单价（USD）。"""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# 按前缀匹配的模型族价目（USD / 1M tokens）。
# 顺序敏感：更具体的前缀放前面。Sonnet 族（3.5/3.7/4/5）长期 $3/$15。
_MODEL_PRICING: list[tuple[str, Pricing]] = [
    ("claude-opus", Pricing(15.0, 75.0, 1.50, 18.75)),
    ("claude-sonnet", Pricing(3.0, 15.0, 0.30, 3.75)),
    ("claude-haiku", Pricing(0.80, 4.0, 0.08, 1.0)),
]


def get_pricing(model: str) -> Pricing | None:
    """按模型名前缀查找价目；未知返回 None。"""
    if not model:
        return None
    name = model.lower()
    for prefix, pricing in _MODEL_PRICING:
        if name.startswith(prefix):
            return pricing
    return None


def estimate_cost(usage: LLMUsage, model: str | None = None) -> float:
    """据 usage 与模型价目估算费用（USD）；未知模型记 0。"""
    p = get_pricing(model or usage.model or "")
    if p is None:
        return 0.0
    cost = (
        usage.input_tokens * p.input_per_mtok
        + usage.output_tokens * p.output_per_mtok
        + usage.cache_read_input_tokens * p.cache_read_per_mtok
        + usage.cache_creation_input_tokens * p.cache_write_per_mtok
    ) / 1_000_000
    return round(cost, 6)
