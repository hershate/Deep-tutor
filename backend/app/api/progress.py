"""进度 API（plan/03 §一 progress，P2 iter 39）。

POST /api/progress           { plan_id, node_id, status, quiz_score? } -> 更新进度（覆盖写；每次提交按一次复习事件累计掌握度，非幂等）
GET  /api/progress/{plan_id} -> 进度总览（nodes + mastery_avg）

掌握度/复习间隔计算在 app/tools/record_progress.py（纯 Python 确定性，plan/02 §9），
本层只做 DB upsert。node_id 为 plan 内容 knowledge_map 的字符串 id（knowledge_nodes.sid）。
plan/02 §9 errors：节点不存在 -> 忽略并返回 warning（不报错）。
next_review 不入库：由 mastery + updated_at（UTC，日粒度）即时推导（SM-2-lite，见
record_progress docstring）；非 UTC 时区在日界最多差 1 天，对 1-14 天复习间隔可接受。
"""

from __future__ import annotations

import time
from datetime import date as dt_date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from app.api.deps import get_current_user, get_db, get_owned_plan, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User
from app.tools.record_progress import compute_mastery, next_review_date

router = APIRouter(prefix="/progress", tags=["progress"], dependencies=[Depends(require_auth)])

# iter 246：打卡限流 + 条数配额——此前是写入面无防的遗漏点，脚本可无限灌行拖垮库
_LOG_RATE_PER_MIN = 60
_MAX_LOGS_PER_USER = 10_000


class ProgressUpdate(BaseModel):
    plan_id: int
    node_id: str = Field(min_length=1, max_length=64)  # knowledge_nodes.sid
    status: models.ProgressStatus
    quiz_score: float | None = Field(default=None, ge=0.0, le=1.0)  # 本次答题正确率 0-1


def _node_view(node: models.KnowledgeNode, row: models.Progress | None) -> dict:
    """节点进度视图：无 progress 行 -> not_started/0.0/无复习日。"""
    if row is None:
        return {
            "node_id": node.sid,
            "name": node.name,
            "status": models.ProgressStatus.not_started.value,
            "mastery": 0.0,
            "next_review": None,
        }
    mastery = round(row.mastery, 3)
    return {
        "node_id": node.sid,
        "name": node.name,
        "status": row.status.value,
        "mastery": mastery,
        "next_review": next_review_date(mastery, today=row.updated_at.date()).isoformat(),
    }


def _apply_progress_update(
    db: Session, row: models.Progress, body: ProgressUpdate
) -> models.Progress | None:
    """iter 367：乐观锁更新——并发提交同节点时后写者按最新值重放计算，防绝对值覆盖丢更新。

    旧实现读-改-写无版本控制：两个标签页同时提交同一节点（线程池真并发），
    后提交者以陈旧 mastery 计算的结果直接覆盖前一次的加权增量。

    iter 394：返回 None 表示「进度行在并发窗口内被级联删除」（方案/节点被删），
    调用方须据此放弃本次更新——此前返回已删行实例，调用方随后 db.refresh(row)
    触发 SQLAlchemy InvalidRequestError（500）。

    iter 394（二）：主键先取为普通 int（row_id）——行被并发删除后，本会话内的 ORM
    实例已过期，再访问 row.id 会触发 ORM 刷新 -> ObjectDeletedError（500）。
    同理，提交后的 db.refresh 也可能撞上行已删除 -> 捕获后返回 None。
    """
    fresh = row
    row_id = row.id  # 主键快照：后续一律用它查库，不再触碰可能已过期的 ORM 实例
    for _ in range(5):
        old_mastery = fresh.mastery
        old_status = fresh.status
        mastery = compute_mastery(old_mastery, body.status.value, body.quiz_score)
        matched = (
            db.query(models.Progress)
            .filter(
                models.Progress.id == row_id,
                models.Progress.mastery == old_mastery,
                # iter 394：CAS 谓词补 status——此前只比 mastery，而「status 变、mastery 不变」
                # 的更新（status ∈ {not_started, learning} 且无 quiz_score -> compute_mastery
                # 返回 old）**不改变 mastery**，谓词因此无法察觉并发者改过 status，
                # 属「乐观锁只锁了一半行」。补上 status 后并发 status 变更会被检出并重放。
                models.Progress.status == old_status,
            )
            .update({"status": body.status, "mastery": mastery}, synchronize_session=False)
        )
        db.commit()
        if matched:
            try:
                db.refresh(fresh)
            except ObjectDeletedError:
                return None  # 行在提交后被级联删除
            return fresh
        db.rollback()  # 失配：并发者已改写，重读最新值重放
        again = db.query(models.Progress).filter_by(id=row_id).one_or_none()
        if again is None:
            # iter 394：行被级联删除——返回 None 交调用方处理。此前返回已删行实例，
            # 调用方随后 db.refresh(row) 触发 SQLAlchemy InvalidRequestError（500）。
            return None  # 下次请求走插入路径
        fresh = again
    # 极端争用退化：无条件写保请求成功（丢更新窗口已从常态缩至连续 5 次失配后）。
    # iter 368：one() -> one_or_none——行被级联删除时 NoResultFound 会把 200 变 500
    last = db.query(models.Progress).filter_by(id=row_id).one_or_none()
    if last is None:
        return None  # iter 394：同上行已删除——不返回失效实例（见上）
    last.status = body.status
    last.mastery = compute_mastery(last.mastery, body.status.value, body.quiz_score)
    db.commit()
    try:
        db.refresh(last)
    except ObjectDeletedError:
        return None  # iter 394：提交后行被级联删除
    return last


@router.post("")
def update_progress(
    body: ProgressUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """更新单节点进度（覆盖写）：掌握度加权 + 间隔重复（record_progress）。

    iter 368 口径修正：**非幂等**——每次提交都按一次复习事件累计掌握度（compute_mastery
    为加权增量）；网络重试/双击重放会重复累计。防重由前端负责（QuizModal 按 quizzes 重置、
    Review 卡片单次提交），服务端以 60/min 限流兜底。
    """
    if not limiter.check(f"update_progress:{user.id}", 60):
        raise ApiError("rate_limited", "更新过于频繁，请稍后再试", status=429)
    get_owned_plan(db, body.plan_id, user)  # P4 隔离：他人方案 404
    node = (
        # iter 394：one_or_none -> first（同 sid 重复行不再抛 MultipleResultsFound 变 500）。
        # schema 未对 (plan_id, sid) 建唯一约束——同 sid 重复节点确实可能出现（LLM 生成的
        # knowledge_map 出现重复 id，或节点缺 id 时 sid="" 多行；core/backup.py 的导入去重
        # 已按「同 sid 取首条」处理）。此处取 id 最小的一条（与备份去重口径一致，结果确定）。
        db.query(models.KnowledgeNode)
        .filter_by(plan_id=body.plan_id, sid=body.node_id)
        .order_by(models.KnowledgeNode.id)
        .first()
    )
    if node is None:
        # plan/02 §9 errors：节点不存在 -> 忽略并记录 warning
        return {"updated": False, "warning": f"node_not_found: {body.node_id}"}

    row = (
        db.query(models.Progress)
        .filter_by(plan_id=body.plan_id, node_id=node.id)
        .one_or_none()
    )
    if row is not None:
        row = _apply_progress_update(db, row, body)
        if row is None:
            # iter 394：进度行在并发窗口内被级联删除——放弃本次更新（下次请求走插入
            # 路径）。此前返回已删行实例，调用方 db.refresh(row) 抛 InvalidRequestError
            # （500）；且此处不应再 refresh 一个不存在的行。
            return {"updated": False, "warning": "progress_row_deleted"}
    else:
        row = models.Progress(
            plan_id=body.plan_id, node_id=node.id, status=body.status,
            mastery=compute_mastery(0.0, body.status.value, body.quiz_score),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # err.md A11：并发请求先插了同 (plan_id, node_id) 行（唯一约束兜底）-> 转为更新。
            # iter 368：one() -> one_or_none——并发者插入后旋即级联删除时不再 500
            db.rollback()
            again = (
                db.query(models.Progress)
                .filter_by(plan_id=body.plan_id, node_id=node.id)
                .one_or_none()
            )
            if again is None:
                # 并发行已消失：按插入重放（row 已回滚为瞬时态，可重新 add）
                db.add(row)
                db.commit()
                try:
                    db.refresh(row)
                except ObjectDeletedError:
                    # iter 394：插入后行即被级联删除 -> 放弃本次更新
                    return {"updated": False, "warning": "progress_row_deleted"}
            else:
                row = _apply_progress_update(db, again, body)
                if row is None:
                    # iter 394：同上（行在 CAS 窗口内被级联删除）
                    return {"updated": False, "warning": "progress_row_deleted"}
    try:
        db.refresh(row)
    except ObjectDeletedError:
        # iter 394：更新提交后行被级联删除 -> 放弃本次更新（此前抛 InvalidRequestError -> 500）
        return {"updated": False, "warning": "progress_row_deleted"}
    mastery = row.mastery
    # 后端插件事件（API.md「后端插件 API」§事件清单）：监听器异常隔离，不影响响应
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "progress_updated",
        {
            "user_id": user.id,
            "plan_id": body.plan_id,
            "node_id": node.sid,
            "status": row.status.value,
            "mastery": round(mastery, 3),
        },
    )
    return {
        "updated": True,
        "node_id": node.sid,
        "status": row.status.value,
        "mastery": round(mastery, 3),
        "next_review": next_review_date(mastery, today=row.updated_at.date()).isoformat(),
    }


@router.get("/logs")
def list_day_logs(
    date_: dt_date = Query(alias="date"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """某日跨方案学习日志（iter 268：热力图当日明细——点击格子看「那天学了什么」）。

    此前日志仅按方案隔离可查（/logs/{plan_id}），全局时间视图（热力图）与
    按方案日志列表之间无桥梁；本端点按日聚合当前用户全部方案的打卡。
    date 为 ISO 日期（FastAPI 解析，非法 422）；结果按 id 倒序（后打的在前）。
    注意：必须注册在 /{plan_id} 之前——/logs 与 /{plan_id} 同为两段路径，
    注册顺序决定匹配（否则 "logs" 被当 plan_id 解析报 422，开发期实查暴露）。
    """
    from app.api.plans import _plan_topic  # 方案主题（content_path 文件回读同款口径）

    rows = (
        db.query(models.LearningLog, models.Plan)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id, models.LearningLog.date == date_)
        .order_by(models.LearningLog.id.desc())
        .limit(100)  # 防御上界（正常一日打卡数条；配额见 add_learning_log）
        .all()
    )
    return {
        "date": date_.isoformat(),
        "items": [
            {
                "id": log.id,
                "plan_id": log.plan_id,
                "topic": _plan_topic(plan),
                "minutes": log.minutes,
                "note": log.note,
            }
            for log, plan in rows
        ],
    }


@router.get("/{plan_id}")
def get_progress(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """进度总览（plan/03 §一）：全部 knowledge_nodes 合并 progress 行 + mastery_avg。"""
    get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    nodes = db.query(models.KnowledgeNode).filter_by(plan_id=plan_id).all()
    rows = {r.node_id: r for r in db.query(models.Progress).filter_by(plan_id=plan_id).all()}
    views = [_node_view(n, rows.get(n.id)) for n in nodes]
    mastery_avg = round(sum(v["mastery"] for v in views) / len(views), 3) if views else 0.0
    return {"plan_id": plan_id, "nodes": views, "mastery_avg": mastery_avg}


# --- 全局今日待复习（iter 119）：跨方案聚合到期节点，Wiki 页顶部提醒 ---
# iter 281：抽 _due_items 共享 due 计算，供 /review/due（轻量徽标/列表）与
# /review/queue（翻卡复习，富化笔记+难度）复用——避免两处到期筛选逻辑分叉
# （此前 review_due 内联，新增复习页若另写一份会随 SM-2 口径演进而失同步）。


def _due_items(db: Session, user: User) -> list[dict]:
    """跨方案到期复习节点（next_review <= 今天），按 (next_review, mastery) 升序（最逾期在前）。

    返回富化字典（含 node_pk/difficulty/must_learn 等投影专用字段）；**不切片**——
    调用方按 limit 自行截断并据 len(全量) 报 total（保留 /review/due 既有 total 语义：
    total 为到期总数，items 为截断后的前 N 条）。
    next_review 同 _node_view：mastery + updated_at 即时推导（SM-2-lite），不入库。
    """
    from app.api.plans import _load_plan_content

    rows = (
        db.query(models.Progress, models.KnowledgeNode)
        .join(models.KnowledgeNode, models.Progress.node_id == models.KnowledgeNode.id)
        .join(models.Plan, models.Progress.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .all()
    )
    topics: dict[int, str] = {}
    today = dt_date.today()
    items: list[dict] = []
    for prog, node in rows:
        due = next_review_date(round(prog.mastery, 3), today=prog.updated_at.date())
        if due > today:
            continue
        if node.plan_id not in topics:
            plan = db.get(models.Plan, node.plan_id)
            content = _load_plan_content(plan) if plan is not None else {}
            topics[node.plan_id] = str(
                (content.get("intent") or {}).get("topic") or content.get("title") or ""
            )
        items.append(
            {
                "plan_id": node.plan_id,
                "plan_topic": topics[node.plan_id],
                "node_id": node.sid,
                "node_pk": node.id,  # review_queue 批量查笔记用（数字主键，不入 /review/due 契约）
                "name": node.name,
                "status": prog.status.value,
                "mastery": round(prog.mastery, 3),
                "next_review": due.isoformat(),
                "difficulty": node.difficulty,
                "must_learn": bool(node.must_learn),
            }
        )
    items.sort(key=lambda i: (i["next_review"], i["mastery"]))
    return items


# /review/due 既有契约字段（不含 node_pk/difficulty/must_learn——review_queue 专用富化字段）
_REVIEW_DUE_FIELDS = ("plan_id", "plan_topic", "node_id", "name", "status", "mastery", "next_review")


@router.get("/review/due")
def review_due(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """当前用户全部方案的到期复习节点（next_review <= 今天），按复习日升序（最逾期在前）。

    next_review 同 _node_view 语义：mastery + updated_at 即时推导（SM-2-lite），不入库。
    """
    items = _due_items(db, user)
    capped = items[: max(1, min(limit, 500))]
    return {"items": [{k: it[k] for k in _REVIEW_DUE_FIELDS} for it in capped], "total": len(items)}


@router.get("/review/queue")
def review_queue(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """复习队列（iter 281，/review 翻卡复习页数据源）：到期节点 + 本人节点笔记原文 + 难度/必学。

    due 计算与 GET /review/due 同源（_due_items）；本端点额外**一次请求批量 join** 笔记原文，
    使翻卡背面有可刷新内容（有笔记展示笔记、无笔记前端降级为自评提示），避免逐卡 N+1 拉笔记。
    只读、按用户隔离（_due_items 已 join Session.user_id）、limit 上界 500（与 /review/due 同口径）。
    """
    items = _due_items(db, user)
    capped = items[: max(1, min(limit, 500))]
    note_map: dict[int, str] = {}
    if capped:
        note_map = {
            row.node_id: row.content
            for row in db.query(models.NodeNote)
            .filter(
                models.NodeNote.user_id == user.id,
                models.NodeNote.node_id.in_([it["node_pk"] for it in capped]),
            )
            .all()
            if row.content
        }
    out = [
        {
            "plan_id": it["plan_id"],
            "plan_topic": it["plan_topic"],
            "node_id": it["node_id"],
            "name": it["name"],
            "status": it["status"],
            "mastery": it["mastery"],
            "next_review": it["next_review"],
            "difficulty": it["difficulty"],
            "must_learn": it["must_learn"],
            "note": note_map.get(it["node_pk"], ""),
        }
        for it in capped
    ]
    return {"items": out, "total": len(items)}


# --- 全局学习统计（iter 122）：跨方案累计/本周分钟数 + 连续打卡天数 ---
# iter 139：/stats/calendar 近 N 日按日分钟数（Wiki 打卡热力图，无打卡日补 0）


@router.get("/stats/summary")
def stats_summary(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """学习统计：累计/近 7 日分钟数、连续打卡天数（streak）。

    streak 语义：有打卡的日期倒序连成链，链尾须为今天或昨天（今天还没打卡不清零）。
    日界按服务器本地日（与 LearningLogIn.date 默认值同口径）。
    iter 369：SQL 聚合 + 仅取 distinct 日期（此前全量载入该用户 LearningLog ORM 行
    到 Python 求和，行数随打卡线性增长——同 err.md A19 的 usage 修法）。
    """
    from datetime import timedelta

    base_q = (
        db.query(models.LearningLog)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
    )
    total_minutes, total_logs = db.query(
        func.coalesce(func.sum(models.LearningLog.minutes), 0),
        func.count(models.LearningLog.id),
    ).select_from(models.LearningLog).join(
        models.Plan, models.LearningLog.plan_id == models.Plan.id
    ).join(
        models.Session, models.Plan.session_id == models.Session.id
    ).filter(models.Session.user_id == user.id).one()

    today = dt_date.today()
    week_start = today - timedelta(days=6)  # 含今天共 7 天
    week_minutes = (
        db.query(func.coalesce(func.sum(models.LearningLog.minutes), 0))
        .select_from(models.LearningLog)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id, models.LearningLog.date >= week_start)
        .scalar()
        or 0
    )

    # streak 只需去重日期序列（几十行），不载 minutes/note 等列
    day_rows = (
        base_q.with_entities(models.LearningLog.date)
        .group_by(models.LearningLog.date)
        .all()
    )
    days = sorted({r[0] for r in day_rows}, reverse=True)
    streak = 0
    if days and days[0] >= today - timedelta(days=1):
        expected = days[0]
        for d in days:
            if d != expected:
                break
            streak += 1
            expected -= timedelta(days=1)

    return {
        "total_minutes": int(total_minutes or 0),
        "week_minutes": int(week_minutes),
        "streak_days": streak,
        "total_logs": int(total_logs),
    }


@router.get("/stats/calendar")
def stats_calendar(
    days: int = Query(default=84, ge=7, le=366),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """学习打卡日历（iter 139，Wiki 热力图）：近 N 日按日学习分钟数，无打卡日补 0。

    SQL 聚合（同日跨方案多条打卡求和）；日界按服务器本地日（与 stats/summary 同口径）。
    """
    from datetime import timedelta

    start = dt_date.today() - timedelta(days=days - 1)
    rows = (
        db.query(
            models.LearningLog.date,
            func.coalesce(func.sum(models.LearningLog.minutes), 0),
        )
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id, models.LearningLog.date >= start)
        .group_by(models.LearningLog.date)
        .all()
    )
    by_date = {r[0]: int(r[1]) for r in rows}
    items = [
        {"date": (start + timedelta(days=i)).isoformat(), "minutes": by_date.get(start + timedelta(days=i), 0)}
        for i in range(days)
    ]
    return {"items": items}


# --- 学习时长按方案分布（iter 304）：答「时间花在哪个方案上 / 哪个被冷落」 ---

# iter 369：方案主题缓存（plan_id -> (过期时间, topic)）——/stats/by-plan 此前对聚合
# 结果逐 plan 调 _load_plan_content（溢出方案每次从磁盘读 ≤512KB JSON + parse，多方案
# 用户一次 stats 触发上百次大 JSON 读，只为取 intent.topic）。TTL 5 分钟（重生成后
# 短暂陈旧可接受，本端点为分析视图）；FIFO 上界防无界增长。
_PLAN_TOPIC_TTL = 300.0
_PLAN_TOPIC_CACHE_MAX = 512
_plan_topic_cache: dict[int, tuple[float, str]] = {}


def _plan_topic_cached(db: Session, pid: int) -> str:
    from app.api.plans import _load_plan_content

    hit = _plan_topic_cache.get(pid)
    now = time.monotonic()
    if hit is not None and now <= hit[0]:
        return hit[1]
    plan = db.get(models.Plan, pid)
    content = _load_plan_content(plan) if plan is not None else {}
    topic = str((content.get("intent") or {}).get("topic") or content.get("title") or "")
    if len(_plan_topic_cache) >= _PLAN_TOPIC_CACHE_MAX:
        _plan_topic_cache.pop(next(iter(_plan_topic_cache)), None)
    _plan_topic_cache[pid] = (now + _PLAN_TOPIC_TTL, topic)
    return topic


@router.get("/stats/by-plan")
def stats_by_plan(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """当前用户全部 LearningLog 按 plan 聚合分钟数（分钟降序）+ 末次打卡日。

    与 stats_summary（总量）/ stats_calendar（按日）互补——本端点按方案拆分，
    供多方案用户看时间分布、发现被冷落的方案。只读、按用户隔离、SQL 聚合（同 stats 口径）。
    """
    rows = (
        db.query(
            models.LearningLog.plan_id,
            func.coalesce(func.sum(models.LearningLog.minutes), 0),
            func.max(models.LearningLog.date),
        )
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .group_by(models.LearningLog.plan_id)
        .all()
    )
    items = [
        {
            "plan_id": pid,
            "topic": _plan_topic_cached(db, pid),
            "minutes": int(m),
            "last_date": str(d) if d is not None else None,
        }
        for pid, m, d in rows
    ]
    items.sort(key=lambda i: -i["minutes"])
    return {"items": items}


# --- learning_logs 打卡（plan/01 §四 learning_logs，P2 iter 45；学习中心页用）---


class LearningLogIn(BaseModel):
    plan_id: int
    minutes: int = Field(ge=1, le=1440)  # 学习分钟数（err.md D14 对齐前端：0 分钟无意义且污染统计）
    note: str | None = Field(default=None, max_length=2000)
    date: dt_date | None = None  # 默认今天（本地单用户，服务器本地日即用户本地日）


@router.post("/logs")
def add_learning_log(
    body: LearningLogIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """打卡：记一条学习日志（plan_id, date, minutes, note）。

    date 缺省今天；显式传过去日期即补卡（iter 277 前端放开）。未来日期拒绝——
    会污染热力图/streak 统计（今天还没到的「已打卡」），且无任何正当用途。
    """
    if not limiter.check(f"learning_log:{user.id}", _LOG_RATE_PER_MIN):
        raise ApiError("rate_limited", "打卡过于频繁，请稍后再试", status=429)
    if body.date is not None and body.date > dt_date.today():
        raise ApiError("invalid_date", "打卡日期不能是未来")
    # iter 364：补卡下限——防 100 年前等极端过去日期污染热力图/streak 统计
    from datetime import timedelta as _td

    if body.date is not None and body.date < dt_date.today() - _td(days=365):
        raise ApiError("invalid_date", "打卡日期不能早于一年前")
    get_owned_plan(db, body.plan_id, user)  # P4 隔离：他人方案 404
    total_logs = (
        db.query(func.count(models.LearningLog.id))
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .scalar()
    )
    if (total_logs or 0) >= _MAX_LOGS_PER_USER:
        raise ApiError("quota_exceeded", f"打卡记录已达上限 {_MAX_LOGS_PER_USER} 条", status=429)
    row = models.LearningLog(
        plan_id=body.plan_id,
        date=body.date or dt_date.today(),
        minutes=body.minutes,
        note=body.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # 后端插件事件（API.md §8.5）：学习打卡（监听器异常隔离，不影响响应）
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "learning_log_added",
        {
            "user_id": user.id,
            "plan_id": body.plan_id,
            "log_id": row.id,
            "date": row.date.isoformat(),
            "minutes": row.minutes,
            "note": row.note,
        },
    )
    return {"id": row.id, "date": row.date.isoformat(), "minutes": row.minutes, "note": row.note}


@router.get("/logs/{plan_id}")
def list_learning_logs(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """学习日志列表（日期倒序，上限 100 条）+ 累计分钟数。"""
    get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    rows = (
        db.query(models.LearningLog)
        .filter_by(plan_id=plan_id)
        .order_by(models.LearningLog.date.desc(), models.LearningLog.id.desc())
        .limit(100)
        .all()
    )
    total = (
        db.query(func.coalesce(func.sum(models.LearningLog.minutes), 0))
        .filter_by(plan_id=plan_id)
        .scalar()
    )
    return {
        "logs": [
            {"id": r.id, "date": r.date.isoformat(), "minutes": r.minutes, "note": r.note}
            for r in rows
        ],
        "total_minutes": int(total or 0),
    }


@router.delete("/logs/{log_id}")
def delete_learning_log(
    log_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """删除一条学习日志（iter 150：打错卡可撤回）。

    P4 隔离：经 Plan->Session 校验归属，他人日志 404（不泄露存在性）。
    """
    row = (
        db.query(models.LearningLog)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.LearningLog.id == log_id, models.Session.user_id == user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError("not_found", "学习日志不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": log_id}
