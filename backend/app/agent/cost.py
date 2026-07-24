"""成本归集辅助（plan/01 §三 成本计量 / plan/08 §三 ToolOutput.metadata）。

- usage -> cost（按 app/llm/pricing.py 的模型计费表）
- 跨修复重试累加 usage
- 写 api_usage 表（可选，ctx.db 提供时；step 6 完整接入）

iter 356：计费改为「设置页维护的模型计费表」（模型名 -> 4 项费用，¥/1M tokens），
取代旧的「每卡定价」与「族级覆盖」。build_metadata / record_metadata_usage 均按
当前用户的模型计费表 resolve_pricing（精确模型名 -> 族默认 -> None 记 0）。
"""

from __future__ import annotations

from app.llm.base import LLMUsage
from app.llm.pricing import estimate_cost, estimate_cost_with_pricing, resolve_pricing
from app.tools.base import ToolMetadata

# UserPref 中存模型计费表的 key（JSON：模型名 -> 4 单价字段）
MODEL_PRICING_PREF_KEY = "model_pricing"


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


def build_metadata(
    total: LLMUsage, model: str = "", model_pricing: dict | None = None
) -> ToolMetadata:
    """usage -> ToolMetadata（含 cost）。

    iter 356：``model_pricing`` 为当前用户的模型计费表（模型名 -> 4 字段）；
    resolve_pricing 按实际模型精确匹配 -> 族默认 -> None（记 0 告警）。
    不传（None）/ 空 -> 纯族默认（向后兼容单参/双参调用，如插件 provider）。
    """
    used_model = model or total.model
    cost = estimate_cost(total, used_model, model_pricing)
    return ToolMetadata(
        tokens_in=total.input_tokens,
        tokens_out=total.output_tokens,
        cache_read=total.cache_read_input_tokens,
        cache_write=total.cache_creation_input_tokens,
        cost=cost,
        model=used_model,
    )


def load_model_pricing(db, user_id: int | None) -> dict:
    """iter 356：从 UserPref 读模型计费表 JSON（设置页维护，模型名 -> 4 单价字段）。

    无 db/user/行/解析失败 -> {}（resolve_pricing 回退族默认，向后兼容）。
    """
    if not db or not user_id:
        return {}
    try:
        import json

        from app.db.models import UserPref

        row = db.query(UserPref).filter_by(user_id=user_id, key=MODEL_PRICING_PREF_KEY).one_or_none()
        if not row or not row.value:
            return {}
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def estimate_cost_from_metadata(
    metadata: ToolMetadata, model_pricing: dict | None = None
) -> float:
    """iter 356：重算 metadata 的 cost（从 token 数重建 usage），按用户模型计费表。

    model_pricing 非空 -> 精确模型名匹配 -> 族默认；空 -> 族默认。
    """
    usage = LLMUsage(
        input_tokens=metadata.tokens_in,
        output_tokens=metadata.tokens_out,
        cache_read_input_tokens=metadata.cache_read,
        cache_creation_input_tokens=metadata.cache_write,
        model=metadata.model,
    )
    return estimate_cost(usage, metadata.model, model_pricing)


def record_metadata_usage(db, metadata: ToolMetadata, user_id: int | None = None, provider: str = "anthropic") -> None:
    """按 ToolOutput.metadata 写 api_usage 行（err.md A6/B16）。

    生成管线的用量由 _persist_tool_calls 归集；quiz/materials/slides 等独立端点
    此前不计量（日预算与用量统计对其失明），统一经此函数补记。
    无 token 消耗（纯降级/无 provider）时不写；写失败仅回滚不抛（计量不阻断业务）。

    iter 356：按当前用户的「模型计费表」重算 cost（resolve_pricing：精确模型名 ->
    族默认）——确保 api_usage / 用量页 / 日预算（check_daily_budget 读 api_usage 求和）
    按用户配置的实际单价。
    """
    if not (metadata.tokens_in or metadata.tokens_out):
        return
    try:
        from app.db.models import ApiUsage

        table = load_model_pricing(db, user_id)
        pricing = resolve_pricing(metadata.model or "", table)
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
