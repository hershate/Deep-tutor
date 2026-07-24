"""成本归集辅助（plan/01 §三 成本计量 / plan/08 §三 ToolOutput.metadata）。

- usage -> cost（按 app/llm/pricing.py）
- 跨修复重试累加 usage
- 写 api_usage 表（可选，ctx.db 提供时；step 6 完整接入）
"""

from __future__ import annotations

from app.llm.base import LLMUsage
from app.llm.pricing import Pricing, estimate_cost, estimate_cost_with_pricing
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


def build_metadata(total: LLMUsage, model: str = "", pricing: Pricing | None = None) -> ToolMetadata:
    used_model = model or total.model
    # iter 354 BUG2：pricing 非空（启用档案合成的每卡定价）-> 直接用其计费（实时累计准确）；
    # 否则回退族默认 estimate_cost。
    cost = estimate_cost_with_pricing(total, pricing) if pricing is not None else estimate_cost(total, used_model)
    return ToolMetadata(
        tokens_in=total.input_tokens,
        tokens_out=total.output_tokens,
        cache_read=total.cache_read_input_tokens,
        cache_write=total.cache_creation_input_tokens,
        cost=cost,
        model=used_model,
    )


def load_pricing_overrides(db, user_id: int | None) -> dict:
    """iter 353：从 UserPref 读 pricing_overrides JSON（设置页可配置价目）。

    无 db/user/行/解析失败 -> {}（用内置默认价目，向后兼容）。
    """
    if not db or not user_id:
        return {}
    try:
        import json

        from app.db.models import UserPref

        row = db.query(UserPref).filter_by(user_id=user_id, key="pricing_overrides").one_or_none()
        if not row or not row.value:
            return {}
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def estimate_cost_from_metadata(
    metadata: ToolMetadata, overrides: dict | None = None, pricing: Pricing | None = None
) -> float:
    """iter 353/354：重算 metadata 的 cost（从 token 数重建 usage）。

    - pricing 非空（iter 354 每卡定价）-> 用显式 Pricing（优先）；
    - 否则按族级 overrides（iter 353，向后兼容）；
    - 两者皆空 -> 族默认 estimate_cost。
    """
    from app.llm.base import LLMUsage

    usage = LLMUsage(
        input_tokens=metadata.tokens_in,
        output_tokens=metadata.tokens_out,
        cache_read_input_tokens=metadata.cache_read,
        cache_creation_input_tokens=metadata.cache_write,
        model=metadata.model,
    )
    if pricing is not None:
        return estimate_cost_with_pricing(usage, pricing)
    return estimate_cost(usage, metadata.model, overrides)


def load_active_profile_pricing(db, user_id: int | None) -> Pricing | None:
    """iter 354 BUG2：读「启用供应商档案」的每卡价目合成 Pricing（替代族级 UserPref 覆盖）。

    定价绑定具体卡片：启用档案的 4 个价目字段（NULL 回退族默认）+ 档案模型决定族。
    无 db/无 user/无启用档案/读失败 -> None（调用方回退 metadata.cost 或 estimate_cost 族默认）。
    """
    if not db or not user_id:
        return None
    try:
        from app.db.models import LlmProviderProfile
        from app.llm.pricing import pricing_for_profile

        p = db.query(LlmProviderProfile).filter_by(user_id=user_id, is_active=True).first()
        if p is None:
            return None
        prices = {
            "input_per_mtok": p.price_input_per_mtok,
            "output_per_mtok": p.price_output_per_mtok,
            "cache_read_per_mtok": p.price_cache_read_per_mtok,
            "cache_write_per_mtok": p.price_cache_write_per_mtok,
        }
        return pricing_for_profile(p.model or "", prices)
    except Exception:  # noqa: BLE001
        return None


def record_metadata_usage(db, metadata: ToolMetadata, user_id: int | None = None, provider: str = "anthropic") -> None:
    """按 ToolOutput.metadata 写 api_usage 行（err.md A6/B16）。

    生成管线的用量由 _persist_tool_calls 归集；quiz/materials/slides 等独立端点
    此前不计量（日预算与用量统计对其失明），统一经此函数补记。
    无 token 消耗（纯降级/无 provider）时不写；写失败仅回滚不抛（计量不阻断业务）。

    iter 354 BUG2：按「启用供应商档案」每卡定价重算 cost（替代族级 UserPref 覆盖）——
    确保 api_usage / 用量页 / 日预算（check_daily_budget 读 api_usage 求和）按用户实际单价。
    """
    if not (metadata.tokens_in or metadata.tokens_out):
        return
    try:
        from app.db.models import ApiUsage

        pricing = load_active_profile_pricing(db, user_id)
        if pricing is not None:
            cost = estimate_cost_with_pricing(
                LLMUsage(
                    input_tokens=metadata.tokens_in,
                    output_tokens=metadata.tokens_out,
                    cache_read_input_tokens=metadata.cache_read,
                    cache_creation_input_tokens=metadata.cache_write,
                    model=metadata.model,
                ),
                pricing,
            )
        else:
            cost = metadata.cost
        db.add(
            ApiUsage(
                user_id=user_id,
                provider=provider,
                model=metadata.model,
                tokens_in=metadata.tokens_in,
                tokens_out=metadata.tokens_out,
                cache_read=metadata.cache_read,
                cache_write=metadata.cache_write,
                cost=cost,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
