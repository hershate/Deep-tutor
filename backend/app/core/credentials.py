"""API Key 凭证档案解析（iter 223：设置页可视化管理全部 Key，多档案卡片切换）。

三类 kind 与消费点：
- KIND_ANTHROPIC（Anthropic 回退主 Key，单活）：llm.get_provider 优先级
  LLM 供应商档案 > 本类 active 档案 > .env ANTHROPIC_API_KEY。
- KIND_BING（Bing 搜索 Key，单活）：search_resources 检索取 active 档案 key，
  无 -> .env BING_API_KEY（再无 -> auto 语义走免 Key 网页版）。
- KIND_AUTH_TOKEN（个人访问令牌 PAT，可多活）：deps 认证——任一 active 令牌
  以档案归属用户身份通过；.env AUTH_TOKEN 始终有效作回退（防锁死）。

解析函数返回 dict（非 ORM 对象），避免会话关闭后 DetachedInstanceError；
DB 读取异常一律回退 None/空列表（同 llm.get_active_profile 模式，不阻断调用方）。
"""

from __future__ import annotations

import secrets
from typing import Any

from app.core.settings import settings

KIND_ANTHROPIC = "anthropic"
KIND_BING = "bing"
KIND_AUTH_TOKEN = "auth_token"
CREDENTIAL_KINDS = (KIND_ANTHROPIC, KIND_BING, KIND_AUTH_TOKEN)
# 单活类：同时至多一个 active（activate 互斥）；auth_token 为多活（任一 active 均有效）
SINGLE_ACTIVE_KINDS = (KIND_ANTHROPIC, KIND_BING)


def _to_dict(p: Any) -> dict:
    return {
        "id": p.id,
        "user_id": p.user_id,
        "kind": p.kind,
        "name": p.name,
        "api_key": p.api_key,
        "is_active": bool(p.is_active),
    }


def _query_active(user_id: int | None, kind: str, db) -> list[dict]:
    from app.db import models

    q = db.query(models.CredentialProfile).filter_by(kind=kind, is_active=True)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return [_to_dict(p) for p in q.order_by(models.CredentialProfile.id).all()]


def list_active_credentials(user_id: int | None, kind: str, db=None) -> list[dict]:
    """active 凭证档案列表（按 id 升序）。user_id 指定 -> 仅该用户（P4 隔离）；
    user_id=None -> 任意 active（无用户上下文的全局回退，同 get_active_profile 语义）。
    db 未传时自开短会话；DB 异常 -> []。
    """
    if db is not None:
        try:
            return _query_active(user_id, kind, db)
        except Exception:  # noqa: BLE001
            return []
    try:
        from app.db.base import SessionLocal

        session = SessionLocal()
        try:
            return _query_active(user_id, kind, session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return []


def get_active_credential(user_id: int | None, kind: str, db=None) -> dict | None:
    """该用户该类的首个 active 档案；无 -> None（单活类至多一条）。"""
    rows = list_active_credentials(user_id, kind, db)
    return rows[0] if rows else None


def resolve_bing_api_key(user_id: int | None = None, db=None) -> str:
    """资源检索实际使用的 Bing Key：active bing 凭证档案 > .env BING_API_KEY。"""
    cred = get_active_credential(user_id, KIND_BING, db)
    if cred is not None:
        return cred["api_key"]
    return settings.bing_api_key


def match_auth_token_credential(db, token: str) -> dict | None:
    """任一 active auth_token 档案与 token 常量时间比对，命中 -> 该档案（含 user_id）。

    逐条 compare_digest（N 小：每用户配额 ≤20，全局行数有界）；无命中 -> None。
    仅在 JWT 与 .env token 均失败后的路径调用（已认证请求零新增查询）。
    """
    if not token:
        return None
    for cred in list_active_credentials(None, KIND_AUTH_TOKEN, db):
        if secrets.compare_digest(token, cred["api_key"]):
            return cred
    return None


def has_active_auth_token_credential(db) -> bool:
    """是否存在任一 active auth_token 档案（passwordless_open 判定用——
    存在即视为已配置认证，开放本机模式关闭，否则令牌形同虚设）。"""
    from app.db import models

    try:
        return (
            db.query(models.CredentialProfile.id)
            .filter_by(kind=KIND_AUTH_TOKEN, is_active=True)
            .first()
            is not None
        )
    except Exception:  # noqa: BLE001
        return False
