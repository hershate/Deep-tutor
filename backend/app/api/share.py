"""协作分享 API（P5）：方案只读分享链接。

- POST   /api/plans/{plan_id}/share  创建（或返回既有）分享链接（仅方案所有者；
                                     既有已过期则轮换 token 重发，见 create_share）
- DELETE /api/plans/{plan_id}/share  撤销链接（仅方案所有者）
- GET    /api/plans/{plan_id}/share  查询单方案分享状态（仅方案所有者）
- GET    /api/shares                 当前用户全部活跃分享链接（iter 258 全局管理面）
- GET    /api/shared/{token}         公开只读访问（无认证；只含方案内容，不含用户/会话/资料）

安全约定：token 为 192bit 随机串（secrets.token_urlsafe(24)），不可枚举；
公开端点永不返回 user/session/material 信息（R35 隐私）。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_plan, require_auth
from app.api.errors import ApiError
from app.api.plans import _plan_topic
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import Plan, ShareLink, User

# 认证轨：挂在 /plans 下（与 plans.py 同前缀，所有权校验 404 防枚举）
router = APIRouter(prefix="/plans", tags=["share"], dependencies=[Depends(require_auth)])
# 认证轨：全局分享管理（iter 258）
manage_router = APIRouter(prefix="/shares", tags=["share"], dependencies=[Depends(require_auth)])
# 公开轨：无认证
public_router = APIRouter(prefix="/shared", tags=["share"])


def _load_content(plan: Plan) -> dict:
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    return content or {}


# iter 372 R15（待追认缺省）：分享链接默认有效期。此前永久有效——「遗忘的公开
# 链接长期暴露」是网络服务面已知风险（/shares 管理面 docstring 即为此而设）。
# created_at 为 naive UTC（server_default func.now()=SQLite CURRENT_TIMESTAMP），
# 与 datetime.utcnow() 同口径比较；SHARE_DEFAULT_EXPIRE_DAYS=0 恢复永久。
def _share_expiry(link: ShareLink) -> datetime | None:
    from app.core.settings import settings

    days = settings.share_default_expire_days
    if days <= 0 or link.created_at is None:
        return None
    return link.created_at + timedelta(days=days)


def _share_expired(link: ShareLink) -> bool:
    from datetime import datetime

    exp = _share_expiry(link)
    return exp is not None and datetime.utcnow() >= exp


def _new_share_link(db: Session, plan_id: int) -> ShareLink:
    """新建分享链接；并发创建撞唯一约束 -> 重读既有行（err.md A16 幂等语义）。"""
    link = ShareLink(plan_id=plan_id, token=secrets.token_urlsafe(24))
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        # err.md A16：并发创建撞唯一约束 -> 重读既有行（幂等语义不变）
        db.rollback()
        link = db.query(ShareLink).filter_by(plan_id=plan_id).one()
    db.refresh(link)
    return link


def _rotate_expired_link(db: Session, link: ShareLink) -> ShareLink | None:
    """过期链接 CAS 轮换 token；返回库里现存的链接行（行已被并发删掉则 None）。

    iter 395：此前是「读-改-写」无条件覆盖——并发双击/多标签页时两方都读到同一条
    过期行，A 写 token_A、B 写 token_B，库里只剩 token_B，A 拿到的链接立刻 404。
    改为条件更新 WHERE id=? AND token=<轮换前的旧 token>：只有一个赢家，输家重读
    该行并返回库里现存的 token（两方拿到同一个可用链接，后到者不会拿到已失效值）。
    """
    link_id = link.id
    # 条件更新：WHERE 带旧 token —— 只有「读到的仍是旧 token」的一方能改成功
    (
        db.query(ShareLink)
        .filter(ShareLink.id == link_id, ShareLink.token == link.token)
        .update(
            {"token": secrets.token_urlsafe(24), "created_at": datetime.utcnow()},
            synchronize_session=False,  # ORM 快照不跟随本会话改写，改为下面显式重读
        )
    )
    db.commit()
    # 无论本方是否改成功，一律重读：库里现存的 token 才是唯一有效的那个
    current = db.query(ShareLink).filter_by(id=link_id).first()
    if current is None:
        return None  # 并发撤销：行已删（调用方按「无链接」重建）
    db.refresh(current)  # 条件更新只改库内值 -> 显式重读当前 token/created_at
    return current


@router.post("/{plan_id}/share", status_code=201)
def create_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    if not limiter.check(f"share_write:{user.id}", 30):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    plan = get_owned_plan(db, plan_id, user)  # 他人方案 404
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is None:
        link = _new_share_link(db, plan.id)
    elif _share_expired(link):
        # iter 373 R16：既有链接已过期时沿用会原样返回一条公开 404 的死链——
        # 前端「分享」按钮触发的正是本路径（get_share 过期即 shared=false）。
        # 轮换 token 并重置 created_at（90 天重新计时）：旧 token 即使已被
        # 复制到别处也继续保持失效，属主拿到的是可用的新链接。
        # iter 395：轮换走 CAS 条件更新（见 _rotate_expired_link）——并发双击时
        # 两方不再互相覆盖 token（否则先到者拿到的链接立刻 404）。
        rotated = _rotate_expired_link(db, link)
        link = rotated if rotated is not None else _new_share_link(db, plan.id)  # 并发撤销 -> 重建
    return {
        "token": link.token,
        "path": f"/shared/{link.token}",
        "created_at": link.created_at.isoformat(),
        "expires_at": _share_expiry(link).isoformat() if _share_expiry(link) else None,
    }


@router.delete("/{plan_id}/share", status_code=204)
def revoke_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    if not limiter.check(f"share_write:{user.id}", 30):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    plan = get_owned_plan(db, plan_id, user)
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is not None:
        db.delete(link)
        db.commit()


@router.get("/{plan_id}/share")
def get_share(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """查询当前方案的分享状态（无链接 -> {"shared": false}）。"""
    plan = get_owned_plan(db, plan_id, user)
    link = db.query(ShareLink).filter_by(plan_id=plan.id).first()
    if link is None:
        return {"shared": False}
    if _share_expired(link):
        return {"shared": False, "expired": True}
    return {
        "shared": True,
        "token": link.token,
        "path": f"/shared/{link.token}",
        "created_at": link.created_at.isoformat(),
        "expires_at": _share_expiry(link).isoformat() if _share_expiry(link) else None,
    }


# 公开读限流（防 token 探测/抓取滥用；192bit token 本身不可枚举，此为纵深防御）
# iter 395：改为双桶——(a) 按链接 token 分桶，保护单个链接不被刷；(b) 按 IP 分桶，
# 作粗粒度滥用护栏。此前只有 IP 桶：经 nginx/反代时 request.client.host 是代理 IP
# -> 全站访问者共用一个桶，30 次/分钟即全站分享页 429（误伤正常用户），而真正的
# 单 IP 探测防护反而失效。token 来自路径参数、不可枚举，按 token 分桶不会互相误伤。
_SHARED_READ_LINK_RATE_PER_MIN = 30  # 单个分享链接
_SHARED_READ_IP_RATE_PER_MIN = 600  # 单 IP（反代后同 IP 多用户共享，阈值放宽）
# 管理面列表读限流（iter 395：/shares 此前无限流）
_SHARES_LIST_RATE_PER_MIN = 30


@manage_router.get("")
def list_shares(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """当前用户全部分享链接（新→旧）——安全审计面：逐方案创建/撤销之外，
    「我到底分享出去了哪些」需要全局视图。iter 373 起逐条带 expired 标记
    （过期链接不再可公开访问，但行仍在——前端标注后可撤销清理）。

    iter 395：补 limit（默认 100、上限 500）与读限流——此前 .all() 无上限全量返回
    该用户全部链接，一次请求即可把整张分享表拖进响应体，且无限流可被反复触发。
    """
    if not limiter.check(f"shares_list:{user.id}", _SHARES_LIST_RATE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    rows = (
        db.query(ShareLink, Plan)
        .join(Plan, ShareLink.plan_id == Plan.id)  # INNER JOIN：方案已删的孤儿链接自动不可见
        .join(models.Session, Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .order_by(ShareLink.created_at.desc(), ShareLink.id.desc())  # 同秒并列按 id 保底确定序
        .limit(limit)
        .all()
    )
    return {
        "shares": [
            {
                "plan_id": plan.id,
                "topic": _plan_topic(plan),
                "token": link.token,
                "path": f"/shared/{link.token}",
                "created_at": link.created_at.isoformat(),
                "expires_at": _share_expiry(link).isoformat() if _share_expiry(link) else None,
                "expired": _share_expired(link),
            }
            for link, plan in rows
        ]
    }


def _sanitize_shared_content(content: dict) -> dict:
    """分享视图裁剪：quizzes 剥离 answer/explanation（前端只渲染题干，
    原文全量返回会让浏览者经 devtools 直接看到答案，破坏测验意义）；
    资源 url 仅保留 http(s)——javascript:/data: 协议链接被点击会在浏览者
    源站上下文执行脚本（跨用户 XSS 面），后端裁剪兜底前端渲染校验。"""
    content = dict(content)
    quizzes = content.get("quizzes")
    if isinstance(quizzes, list):
        content["quizzes"] = [
            {k: v for k, v in q.items() if k not in ("answer", "explanation")}
            if isinstance(q, dict) else q
            for q in quizzes
        ]
    stages = content.get("stages")
    if isinstance(stages, list):
        new_stages = []
        for stage in stages:
            if not isinstance(stage, dict):
                new_stages.append(stage)
                continue
            stage = dict(stage)  # 拷贝后改，不污染 session 内 plan.content 原对象
            resources = stage.get("resources")
            if isinstance(resources, list):
                stage["resources"] = [
                    {**r, "url": ""}
                    if isinstance(r, dict)
                    and not str(r.get("url", "")).strip().lower().startswith(("http://", "https://"))
                    else r
                    for r in resources
                ]
            new_stages.append(stage)
        content["stages"] = new_stages
    # iter 364：顶层 content.resources（search_resources 产出的真实 url 字段）也需 scheme 过滤
    # ——此前仅处理 stages[].resources（design_stages schema 实际无此字段，是死代码），漏顶层
    # resources 致分享页暴露非 http(s) URL（配合恶意备份 javascript: 链接可致跨用户 XSS）
    top_resources = content.get("resources")
    if isinstance(top_resources, list):
        content["resources"] = [
            {**r, "url": ""}
            if isinstance(r, dict)
            and not str(r.get("url", "")).strip().lower().startswith(("http://", "https://"))
            else r
            for r in top_resources
        ]
    return content


@public_router.get("/{token}")
def read_shared(token: str, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else "unknown"
    # iter 395：双桶限流——先按链接 token（保护单个链接不被刷，不同链接互不误伤），
    # 再按 IP（粗粒度滥用护栏，阈值放宽以容纳反代后同 IP 的多用户）。
    if not limiter.check(f"shared_read_link:{token}", _SHARED_READ_LINK_RATE_PER_MIN):
        raise ApiError("rate_limited", "访问过于频繁，请稍后再试", status=429)
    if not limiter.check(f"shared_read_ip:{ip}", _SHARED_READ_IP_RATE_PER_MIN):
        raise ApiError("rate_limited", "访问过于频繁，请稍后再试", status=429)
    link = db.query(ShareLink).filter_by(token=token).first()
    if link is None or _share_expired(link):
        # 过期与不存在同形 404——公开端点不区分二者（不向探测者泄露链接存在性）
        raise ApiError("not_found", "分享链接不存在或已失效")
    plan = db.get(Plan, link.plan_id)
    if plan is None:
        raise ApiError("not_found", "方案已删除")
    content = _sanitize_shared_content(_load_content(plan))
    return {
        # iter 395：主题在 content.intent.topic（plan_assembly.PLAN_SECTIONS 无顶层
        # title/topic），此前读 content.get("title")/content.get("topic") 恒为空 ->
        # 分享页标题恒为「学习方案」。统一走 _plan_topic（与 plans.py 列表/详情同口径），
        # content.get("topic") 兜底早期仅存顶层 topic 的方案。
        "title": _plan_topic(plan) or content.get("topic") or "学习方案",
        "created_at": plan.created_at.isoformat() if plan.created_at else "",
        "content": content,
    }
