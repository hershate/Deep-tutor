"""模型计价表（成本计量 Pillar 3，plan/01 §三）。

单价单位：USD / 1M tokens。按模型族名（opus/sonnet/haiku）子串匹配；未知模型
estimate_cost 记 0 并告警（err.md B3：旧前缀匹配对 claude-3-5-sonnet-20241022 等
dated ID 不命中 -> 成本静默记 0、预算守卫失效）。

> ⚠️ 价格随 Anthropic 调整而变动；以下为公开价目（ephemeral 5min cache：
> cache_read ≈ 0.1×input，cache_write ≈ 1.25×input）。**使用前请核对**
> https://www.anthropic.com/pricing 并按需更新。price 字段可在 settings 扩展为可配置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.llm.base import LLMUsage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pricing:
    """每 1M tokens 单价（USD）。"""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# 模型族价目（USD / 1M tokens），族名子串匹配（兼容 dated ID 如 claude-3-5-sonnet-20241022）。
# 顺序敏感：更具体的族放前面（opus 优先防误中）。Sonnet 族（3.5/3.7/4/5）长期 $3/$15。
_MODEL_PRICING: list[tuple[str, Pricing]] = [
    ("opus", Pricing(15.0, 75.0, 1.50, 18.75)),
    ("sonnet", Pricing(3.0, 15.0, 0.30, 3.75)),
    ("haiku", Pricing(0.80, 4.0, 0.08, 1.0)),
]


def get_pricing(model: str) -> Pricing | None:
    """按模型族名（opus/sonnet/haiku）子串匹配价目；未知返回 None。"""
    if not model:
        return None
    name = model.lower()
    for family, pricing in _MODEL_PRICING:
        if family in name:
            return pricing
    return None


def estimate_cost(usage: LLMUsage, model: str | None = None) -> float:
    """据 usage 与模型价目估算费用（USD）；未知模型记 0 并告警（不静默，err.md B3）。"""
    used = model or usage.model or ""
    p = get_pricing(used)
    if p is None:
        if usage.input_tokens or usage.output_tokens:
            log.warning(
                "未知模型 %r 计价缺失，本次 %d+%d tokens 成本记 0（请更新 pricing 价目）",
                used, usage.input_tokens, usage.output_tokens,
            )
        return 0.0
    cost = (
        usage.input_tokens * p.input_per_mtok
        + usage.output_tokens * p.output_per_mtok
        + usage.cache_read_input_tokens * p.cache_read_per_mtok
        + usage.cache_creation_input_tokens * p.cache_write_per_mtok
    ) / 1_000_000
    return round(cost, 6)
