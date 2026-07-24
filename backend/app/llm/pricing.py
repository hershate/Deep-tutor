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


def _override_pricing(pricing: Pricing, ov: dict) -> Pricing:
    """用用户覆盖字段构造新 Pricing（缺省/非法值回退默认）。iter 353 设置页可配置价目。"""
    try:
        return Pricing(
            input_per_mtok=float(ov.get("input_per_mtok", pricing.input_per_mtok)),
            output_per_mtok=float(ov.get("output_per_mtok", pricing.output_per_mtok)),
            cache_read_per_mtok=float(ov.get("cache_read_per_mtok", pricing.cache_read_per_mtok)),
            cache_write_per_mtok=float(ov.get("cache_write_per_mtok", pricing.cache_write_per_mtok)),
        )
    except (TypeError, ValueError):
        return pricing


def get_pricing(model: str, overrides: dict | None = None) -> Pricing | None:
    """按模型族名（opus/sonnet/haiku）子串匹配价目；未知返回 None。

    iter 353 用户级价目覆盖（设置页可配置）：overrides 形如
    {"sonnet": {"input_per_mtok": 2.0, "output_per_mtok": 10.0, ...}}，
    命中族且该族有覆盖时优先用覆盖单价（缺省字段回退默认）。
    """
    if not model:
        return None
    name = model.lower()
    for family, pricing in _MODEL_PRICING:
        if family in name:
            if overrides and isinstance(overrides.get(family), dict):
                return _override_pricing(pricing, overrides[family])
            return pricing
    return None


def estimate_cost(usage: LLMUsage, model: str | None = None, overrides: dict | None = None) -> float:
    """据 usage 与模型价目估算费用（USD）；未知模型记 0 并告警（不静默，err.md B3）。

    iter 353：overrides 为用户级价目覆盖（设置页可配置），见 get_pricing。
    """
    used = model or usage.model or ""
    p = get_pricing(used, overrides)
    if p is None:
        if usage.input_tokens or usage.output_tokens:
            log.warning(
                "未知模型 %r 计价缺失，本次 %d+%d tokens 成本记 0（请更新 pricing 价目）",
                used, usage.input_tokens, usage.output_tokens,
            )
        return 0.0
    return estimate_cost_with_pricing(usage, p)


def estimate_cost_with_pricing(usage: LLMUsage, pricing: Pricing) -> float:
    """按显式 Pricing 估算费用（USD），不做族查找。

    iter 354 BUG2：每卡定价——ctx.pricing（启用档案合成的 Pricing）直接驱动计费，
    替代族级 UserPref 覆盖。
    """
    cost = (
        usage.input_tokens * pricing.input_per_mtok
        + usage.output_tokens * pricing.output_per_mtok
        + usage.cache_read_input_tokens * pricing.cache_read_per_mtok
        + usage.cache_creation_input_tokens * pricing.cache_write_per_mtok
    ) / 1_000_000
    return round(cost, 6)


# 每卡定价字段键（与 settings.py _VALID_PRICING_FIELDS、Pricing 字段同名）
_PRICING_KEYS = ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok")


def pricing_for_profile(model: str, prices: dict | None) -> Pricing | None:
    """iter 354 BUG2：按「启用供应商档案」价目合成 Pricing（定价绑定具体卡片）。

    - model：档案模型（决定族默认回退，子串匹配 opus/sonnet/haiku）；
    - prices：档案 4 字段（键见 _PRICING_KEYS，值 float 或 None）。
      任一字段为 None -> 该字段回退族默认；全 None / prices 空 -> 直接返回族默认；
      未知模型且档案未设任何价 -> None（estimate_cost 记 0 告警，同旧行为）；
      未知模型但档案设了价 -> 用档案价构造（缺省字段 0）。
    """
    base = get_pricing(model)
    if not prices or not isinstance(prices, dict):
        return base
    if not any(prices.get(k) is not None for k in _PRICING_KEYS):
        return base

    def _val(key: str) -> float:
        v = prices.get(key)
        if v is None:
            return getattr(base, key) if base is not None else 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return getattr(base, key) if base is not None else 0.0

    return Pricing(
        input_per_mtok=_val("input_per_mtok"),
        output_per_mtok=_val("output_per_mtok"),
        cache_read_per_mtok=_val("cache_read_per_mtok"),
        cache_write_per_mtok=_val("cache_write_per_mtok"),
    )


def default_pricing_table() -> dict:
    """默认价目快照（USD / 1M tokens），供设置页展示。iter 353 设置页可配置。

    键为模型族名（opus/sonnet/haiku，子串匹配）；价目据 Anthropic 公开价目，可能随
    官方调整而过时——用户可在设置页按实际账单覆盖（见 get_pricing overrides）。
    核对链接：https://www.anthropic.com/pricing
    """
    return {
        family: {
            "input_per_mtok": p.input_per_mtok,
            "output_per_mtok": p.output_per_mtok,
            "cache_read_per_mtok": p.cache_read_per_mtok,
            "cache_write_per_mtok": p.cache_write_per_mtok,
        }
        for family, p in _MODEL_PRICING
    }
