"""LLM Provider 抽象层（plan/01 §三、plan/04 P0 LLM 与 Agent 核心）。

- LLMProvider 抽象接口（app/llm/base.py）
- Anthropic 实现（app/llm/anthropic_provider.py）：缓存优先 + 成本计量
- 计价表（app/llm/pricing.py）
- get_provider() 工厂：按 settings 构造（缺失 key 抛错）
"""

from __future__ import annotations

from app.core.credentials import KIND_ANTHROPIC, get_active_credential
from app.core.settings import settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

__all__ = ["LLMProvider", "LLMResponse", "LLMUsage", "AnthropicProvider", "get_provider", "get_active_profile", "get_active_model_pricing", "get_active_max_tokens"]


def get_active_profile(user_id: int | None = None, db=None) -> "dict | None":
    """读 DB 中 is_active 的供应商档案（iter 50 多档案切换）；无 -> None。

    P4 按用户隔离（iter 58）：user_id 指定 -> 仅查该用户的 active 档案；
    user_id=None -> 任意 active 档案（无用户上下文的全局回退，如旧调用/脚本）。
    DB 读取失败（如表不存在/连接异常）-> None 回退 .env，不阻断调用方。
    iter 366：db 传入时复用（避免每次生成开短会话 churn 连接池）；None 则自开。
    """
    own = db is None
    try:
        from app.db import models
        from app.db.base import SessionLocal

        sess = db if db is not None else SessionLocal()
        try:
            q = sess.query(models.LlmProviderProfile).filter_by(is_active=True)
            if user_id is not None:
                q = q.filter_by(user_id=user_id)
            # iter 369：order_by(id) 定序——并发 activate 竞态下可能出现多 active 行，
            # first() 无排序时选择不确定（凭证归属漂移）；按 id 稳定取最早启用的档案
            p = q.order_by(models.LlmProviderProfile.id).first()
            if p is None:
                return None
            return {"name": p.name, "base_url": p.base_url or "", "api_key": p.api_key, "model": p.model or "", "kind": p.kind or ""}
        finally:
            if own:
                sess.close()
    except Exception:  # noqa: BLE001
        # iter 369：回退 .env 全局 key 是凭证/成本归属漂移（用量仍记该用户）——
        # 须留可观测信号，不再完全静默
        import logging

        logging.getLogger("deep_tutor.llm").warning(
            "get_active_profile 读 DB 失败，回退 .env 全局凭证（user_id=%s）", user_id, exc_info=True
        )
        return None


def get_active_model_pricing(user_id: int | None = None, db=None) -> dict:
    """iter 356：读当前用户的「模型计费表」（UserPref{model_pricing}，模型名 -> 4 单价字段）。

    自管 DB 会话，失败/无用户/无配置 -> {}（resolve_pricing 回退族默认）。
    隔离语义同 get_active_profile（user_id 指定 -> 仅该用户）。
    用于生成期注入 ctx.model_pricing（实时累计成本按用户配置单价）。
    iter 366：db 传入时复用。
    """
    own = db is None
    try:
        from app.agent.cost import load_model_pricing
        from app.db.base import SessionLocal

        sess = db if db is not None else SessionLocal()
        try:
            return load_model_pricing(sess, user_id)
        finally:
            if own:
                sess.close()
    except Exception:  # noqa: BLE001
        return {}


def get_active_max_tokens(user_id: int | None = None, db=None) -> int | None:
    """iter 355：读用户的 llm_max_tokens 偏好覆盖（UserPref）；无 db/无覆盖/读失败 -> None。

    返回 None 时调用方回退 settings.llm_max_tokens（默认 100k）。自管 DB 会话，
    隔离语义同 get_active_profile。值存 UserPref key='llm_max_tokens'（repr(int)）。
    iter 366：db 传入时复用。
    """
    own = db is None
    try:
        from app.db import models
        from app.db.base import SessionLocal

        sess = db if db is not None else SessionLocal()
        try:
            q = sess.query(models.UserPref).filter_by(key="llm_max_tokens")
            if user_id is not None:
                q = q.filter_by(user_id=user_id)
            row = q.first()
            if row is None or not row.value:
                return None
            # UserPref.value 存 repr(int)（如 '8000'）；容忍 float repr 与空白
            import ast

            return int(ast.literal_eval(row.value.strip()))
        finally:
            if own:
                sess.close()
    except Exception:  # noqa: BLE001
        return None


def get_provider(user_id: int | None = None, db=None) -> LLMProvider:
    """构造 LLM Provider，三级回退（iter 50/58/223）：

    1. DB active LLM 供应商档案（自定义 base_url/model）；
    2. DB active Anthropic 回退 Key 凭证档案（credential_profiles，官方端点 + 默认模型）；
    3. .env ANTHROPIC_API_KEY。

    P4 按用户隔离（iter 58）：user_id 指定 -> 仅用该用户的档案（他人档案不可见）；
    user_id=None -> 任意 active 档案（无用户上下文回退）。
    档案的 base_url/model 为空时分别回退官方端点 / settings.anthropic_model。
    三者皆无 -> 抛 RuntimeError（功能级必填，见 settings.validate_feature_keys）。
    iter 366：db 传入时复用（避免每次生成开多条短会话 churn 连接池）。
    """
    profile = get_active_profile(user_id, db=db)
    if profile is not None:
        # iter 394：出站目标私网防护（**字面量**判定：纯计算不触网，可在事件循环
        # 线程内同步调用，不会阻塞）。写时校验（api/providers.py）已拦新档案；此处
        # 覆盖历史档案（规则引入前创建）与「写时安全、调用时指向内网」的变更。
        base_url = profile.get("base_url") or ""
        if base_url and not settings.allow_private_llm_base_url:
            from app.core.urlguard import check_outbound_url

            ok, reason = check_outbound_url(base_url, resolve=False)
            if not ok:
                raise RuntimeError(
                    f"供应商档案 base_url 被安全策略拒绝：{reason}"
                    "（如需指向内网模型服务，设 ALLOW_PRIVATE_LLM_BASE_URL=1）"
                )
        # iter 313：档案 kind 选择协议——'anthropic'（默认/NULL/空白）走内置 AnthropicProvider；
        # 插件协议（api.providers 注册的 kind）分发到 PluginProvider（档案 api_key/base_url 作凭证）。
        kind = (profile.get("kind") or "").strip().lower() or "anthropic"
        if kind != "anthropic":
            from app.llm.plugin_provider import PluginProvider

            from app.plugins import providers as plugin_providers

            entry = plugin_providers.find_provider(kind)
            if entry is None:
                raise RuntimeError(
                    f"供应商档案协议 {kind!r} 无对应插件 Provider（未注册或插件未加载）"
                )
            return PluginProvider(
                entry,
                model=profile["model"] or entry.default_model or settings.anthropic_model,
                api_key=profile["api_key"],
                base_url=profile.get("base_url") or "",
            )
        return AnthropicProvider(
            api_key=profile["api_key"],
            model=profile["model"] or settings.anthropic_model,
            base_url=profile["base_url"] or None,
        )
    credential = get_active_credential(user_id, KIND_ANTHROPIC, db=db)
    if credential is not None:
        return AnthropicProvider(
            api_key=credential["api_key"],
            model=settings.anthropic_model,
        )
    if not settings.anthropic_key_configured:
        raise RuntimeError("ANTHROPIC_API_KEY 未配置且无生效供应商/Key 档案，无法构造 LLM Provider")
    return AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
