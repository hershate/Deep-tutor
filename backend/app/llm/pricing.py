"""模型计价表（成本计量 Pillar 3，plan/01 §三）。

iter 356 重构：计费改为「设置页维护的模型计费列表」（模型名 → 各项费用），
不再绑定 LLM 供应商卡片。

- **单价单位**：CNY（人民币）/ 1M tokens。全部计费以人民币计（用量页 / 日预算 / 成本归集）。
- **计费解析** ``resolve_pricing(model, table)``：先按模型名**精确匹配**用户计费表；
  未命中回退 Anthropic 族默认（opus/sonnet/haiku，¥ 参考价）；仍无 -> ``None``
  （``estimate_cost`` 记 0 并告警，err.md B3 不静默）。
- **内置默认模型计费表**（DeepSeek V4-Flash / V4-Pro，¥）作为设置页初始参考，
  用户可改可删可加（其余模型自行添加）。价目据 DeepSeek 公开定价，可能随官方调整
  过时——请按你的实际账单核对后调整。

> 价目来源（核对链接）：DeepSeek https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.llm.base import LLMUsage

log = logging.getLogger(__name__)

# 计费表的 4 个单价字段名（与设置页 / 数据交换一致；只增不改）
PRICING_FIELDS: tuple[str, ...] = (
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_per_mtok",
)


@dataclass(frozen=True)
class Pricing:
    """每 1M tokens 单价（CNY 人民币）。"""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# Anthropic 族默认价目（CNY / 1M tokens，按公开美元价 ×≈7.2 换算的参考价）。
# 族名子串匹配（兼容 dated ID 如 claude-3-5-sonnet-20241022）；顺序敏感：更具体
# 的族放前面（opus 优先防误中）。仅当模型未出现在用户计费表时作兜底。
# 核对：https://www.anthropic.com/pricing（实际美元价，此处为 ¥ 参考换算）
_MODEL_PRICING: list[tuple[str, Pricing]] = [
    ("opus", Pricing(108.0, 540.0, 10.8, 135.0)),
    ("sonnet", Pricing(21.6, 108.0, 2.16, 27.0)),
    ("haiku", Pricing(5.76, 28.8, 0.576, 7.2)),
]


# 内置默认模型计费表（CNY / 1M tokens），设置页初始参考。默认仅 DeepSeek V4 两个
# 模型（用户指定）；其余模型由用户在设置页自行添加。
# 字段映射 DeepSeek 官方价目（¥/1M tokens）：
#   input_per_mtok     = 输入（缓存未命中）；cache_read_per_mtok = 输入（缓存命中）；
#   output_per_mtok    = 输出。
# cache_write：DeepSeek 自动上下文缓存，无单独「缓存写入」计费 -> 保守取 = input（不漏算）。
# 价目据 DeepSeek 公开定价（2026-07 查证），可能随官方调整——请按实际账单核对。
_DEFAULT_MODEL_PRICING: dict[str, Pricing] = {
    "deepseek-v4-flash": Pricing(1.0, 2.0, 0.02, 1.0),  # 命中0.02 / 未命中1 / 输出2
    "deepseek-v4-pro": Pricing(3.0, 6.0, 0.025, 3.0),   # 命中0.025 / 未命中3 / 输出6
}


def get_pricing(model: str) -> Pricing | None:
    """按 Anthropic 模型族名（opus/sonnet/haiku）子串匹配族默认价目；未知返回 None。

    仅作兜底（用户计费表未覆盖的 Anthropic 模型）。国产模型不属此三族 -> 需在用户
    计费表中配置（默认预填主流国产模型，见 default_pricing_table）。
    """
    if not model:
        return None
    name = model.lower()
    for family, pricing in _MODEL_PRICING:
        if family in name:
            return pricing
    return None


def _pricing_from_entry(entry: dict) -> Pricing:
    """从计费表条目（4 字段）构造 Pricing；缺省/非法字段回退 0（不静默漏算）。"""
    if not isinstance(entry, dict):
        return Pricing(0.0, 0.0, 0.0, 0.0)

    def _f(key: str) -> float:
        try:
            return float(entry.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    return Pricing(
        input_per_mtok=_f("input_per_mtok"),
        output_per_mtok=_f("output_per_mtok"),
        cache_read_per_mtok=_f("cache_read_per_mtok"),
        cache_write_per_mtok=_f("cache_write_per_mtok"),
    )


def _table_lookup(model: str, table: dict) -> dict | None:
    """在用户计费表中按模型名精确匹配（大小写不敏感、去空白）。命中返回条目 dict。"""
    ml = (model or "").strip().lower()
    if not ml:
        return None
    for key, val in table.items():
        if isinstance(val, dict) and (key or "").strip().lower() == ml:
            return val
    return None


def resolve_pricing(model: str, table: dict | None = None) -> Pricing | None:
    """iter 356：按模型名解析计费（CNY/1M tokens）。

    解析顺序：
    1. **精确模型名**匹配用户计费表 ``table``（大小写不敏感）；
    2. 未命中 -> Anthropic 族默认（opus/sonnet/haiku 子串匹配）；
    3. 仍无 -> ``None``（调用方记 0 告警）。

    ``table`` 形如 ``{"deepseek-chat": {"input_per_mtok": 1.0, ...}, ...}``。
    """
    if not model:
        return None
    if table and isinstance(table, dict):
        entry = _table_lookup(model, table)
        if entry is not None:
            return _pricing_from_entry(entry)
    return get_pricing(model)


def estimate_cost(
    usage: LLMUsage, model: str | None = None, model_pricing: dict | None = None
) -> float:
    """据 usage 与模型计费估算费用（CNY）；未知模型记 0 并告警（不静默，err.md B3）。

    iter 356：``model_pricing`` 为用户模型计费表（模型名 -> 4 字段），优先精确匹配；
    未命中回退族默认。不传则纯族默认（向后兼容 provider.estimate_cost 等无表调用）。
    """
    used = model or usage.model or ""
    p = resolve_pricing(used, model_pricing)
    if p is None:
        if usage.input_tokens or usage.output_tokens:
            log.warning(
                "未知模型 %r 计价缺失，本次 %d+%d tokens 成本记 0（请在设置页「模型计费」配置）",
                used, usage.input_tokens, usage.output_tokens,
            )
        return 0.0
    return estimate_cost_with_pricing(usage, p)


def estimate_cost_with_pricing(usage: LLMUsage, pricing: Pricing) -> float:
    """按显式 Pricing 估算费用（CNY），不做族/表查找。"""
    cost = (
        usage.input_tokens * pricing.input_per_mtok
        + usage.output_tokens * pricing.output_per_mtok
        + usage.cache_read_input_tokens * pricing.cache_read_per_mtok
        + usage.cache_creation_input_tokens * pricing.cache_write_per_mtok
    ) / 1_000_000
    return round(cost, 6)


def default_pricing_table() -> dict:
    """内置默认国产模型计费表（CNY / 1M tokens），供设置页展示为初始参考。

    键为模型名（精确匹配）；价目据各厂商公开定价（见模块 docstring 核对链接），
    可能随官方调整过时——用户可在设置页按实际账单编辑/增删。
    """
    return {
        model: {
            "input_per_mtok": p.input_per_mtok,
            "output_per_mtok": p.output_per_mtok,
            "cache_read_per_mtok": p.cache_read_per_mtok,
            "cache_write_per_mtok": p.cache_write_per_mtok,
        }
        for model, p in _DEFAULT_MODEL_PRICING.items()
    }
