"""iter 395 回归：知识节点状态筛选下推、事件流身份/序列化、计费表误清空、作答保留。

覆盖本轮落地的修复：

1. **knowledge 状态筛选下推 SQL**（``GET /api/knowledge?status=...``）：此前 status
   在 LIMIT 之后于 Python 侧过滤——节点数 > limit 的重度用户按状态筛选只会看到
   「最新 limit 个节点」里的匹配项（结果严重偏少甚至为空），chip 计数同样失真。
   修复：status 下推到 SQL（limit 之前），stats 改为按全量匹配节点统计。
2. **事件流身份与健壮性**：``user_id=True`` 不再被判给 user 1（bool 是 int 子类）；
   payload 不可 JSON 序列化时不再让整条流崩断；身份重验不再依赖队列空闲
   （此前事件持续不断时永不重验 -> 已吊销用户的流持续收事件）。
3. **计费表误清空**（``PUT /api/settings/pricing``）：输入非空但清洗后为空（字段名
   写错 / 全为非法值）-> 400 拒绝，不再静默清空用户整张表。
4. **作答记录保留**（每题保留最新 N 条）：错题本判定不变（最新一条始终保留）。
5. **错题队列 limit 边界**：0 / 超上限 -> 422（此前夹取成 1）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.db import models
from app.db.base import SessionLocal


# --- 1. knowledge 状态筛选下推 + 全量统计 -----------------------------------


def _seed_nodes(count: int, done: int) -> int:
    """造 count 个知识节点，其中前 done 个进度为 done；返回 plan_id。"""
    db = SessionLocal()
    try:
        user = models.User(name="kuser", username="kuser", password_hash="", is_admin=True)
        db.add(user)
        db.flush()
        sess = models.Session(user_id=user.id, title="k")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id, version=1, content={"intent": {"topic": "知识筛选"}}
        )
        db.add(plan)
        db.flush()
        for i in range(count):
            node = models.KnowledgeNode(
                plan_id=plan.id, sid=f"k{i}", name=f"节点{i}", difficulty=2
            )
            db.add(node)
            db.flush()
            if i < done:
                db.add(
                    models.Progress(
                        plan_id=plan.id,
                        node_id=node.id,
                        status=models.ProgressStatus.done,
                        mastery=1.0,
                    )
                )
        db.commit()
        return plan.id
    finally:
        db.close()


def test_knowledge_status_filter_applied_before_limit(client, auth_headers):
    """状态筛选必须在 limit 之前生效：600 节点 / 300 个 done -> 返回全部 300 个。"""
    _seed_nodes(600, 300)
    r = client.get("/api/knowledge?status=done", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 300, f"应返回全部 300 个 done 节点，实际 {body['total']}"
    assert all(i["status"] == "done" for i in body["items"])


def test_knowledge_stats_count_all_matching_nodes(client, auth_headers):
    """stats 按全量匹配节点统计（此前统计 LIMIT 窗口内的批次 -> chip 计数失真）。"""
    _seed_nodes(600, 300)
    r = client.get("/api/knowledge", headers=auth_headers)
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]
    assert stats["done"] == 300, stats
    assert stats["not_started"] == 300, stats


# --- 2. 事件流：身份判定 / 序列化 / 重验 --------------------------------------


def test_event_frame_ignores_bool_user_id():
    """bool 是 int 子类：user_id=True 此前会被判给 user 1（白拿他人事件）。"""
    from app.api.events import format_frame

    assert format_frame("x", {"user_id": True}, user_id=1) is None
    assert format_frame("x", {"user_id": 1}, user_id=1) is not None


def test_event_frame_survives_non_serializable_payload():
    """插件 payload 含 datetime/自定义对象时不得让整条 SSE 流崩断。"""
    from app.api.events import format_frame

    frame = format_frame("x", {"user_id": 1, "at": datetime(2026, 1, 1)}, user_id=1)
    assert frame is not None and "2026-01-01" in frame


def test_events_reauth_not_gated_on_idle_queue():
    """身份重验不得依赖队列空闲：事件连发时也须按周期重验（否则吊销不生效）。"""
    from app.api import events as events_mod

    async def scenario() -> list[str]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        await queue.put(("x", {"user_id": 1}))
        await queue.put(("x", {"user_id": 1}))
        # heartbeat_s=0 -> 重验截止时间立即到期（第一轮即重验）；旧实现只在
        # queue.get() 超时分支重验，而队列非空时永远走不到该分支 -> 会吐帧。
        gen = events_mod.events_generator(queue, 999_999, heartbeat_s=0, reauth=True)
        frames = []
        try:
            async for frame in gen:
                frames.append(frame)
        except StopAsyncIteration:
            pass
        return frames

    frames = asyncio.run(scenario())
    assert frames == [], f"用户不存在时不应再吐帧（重验未生效）：{frames}"


# --- 3. 计费表误清空 --------------------------------------------------------


def test_pricing_invalid_table_rejected_without_clearing(client, auth_headers):
    """字段名写错（清洗后为空）-> 400 且**不动**用户已有计费表。"""
    from app.agent.cost import MODEL_PRICING_PREF_KEY

    ok = client.put(
        "/api/settings/pricing",
        json={"pricing": {"m1": {"input_per_mtok": 3.0}}},
        headers=auth_headers,
    )
    assert ok.status_code == 200, ok.text
    bad = client.put(
        "/api/settings/pricing",
        json={"pricing": {"m2": {"input_per_mtok_typo": 3.0}}},
        headers=auth_headers,
    )
    assert bad.status_code == 400, bad.text
    cur = client.get("/api/settings/pricing", headers=auth_headers)
    assert cur.status_code == 200, cur.text
    assert "m1" in cur.json()["table"], cur.json()["table"]
    db = SessionLocal()
    try:
        row = db.query(models.UserPref).filter_by(key=MODEL_PRICING_PREF_KEY).first()
        assert row is not None and "m1" in (row.value or "")
    finally:
        db.close()


def test_pricing_explicit_empty_still_clears(client, auth_headers):
    """显式空对象仍是「清空」（与「误清空」区分开）。"""
    client.put(
        "/api/settings/pricing",
        json={"pricing": {"m1": {"input_per_mtok": 3.0}}},
        headers=auth_headers,
    )
    r = client.put("/api/settings/pricing", json={"pricing": {}}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["table"] == {}, r.json()


# --- 4. 作答记录保留 --------------------------------------------------------


def test_prune_quiz_attempts_keeps_latest_per_quiz(monkeypatch):
    """每题保留最新 N 条；**最新一条始终保留** -> 错题本判定不变。"""
    from app.core import maintenance as maint_mod
    from app.core.settings import settings

    monkeypatch.setattr(settings, "quiz_attempt_keep_per_quiz", 10)
    db = SessionLocal()
    try:
        user = models.User(name="q", username="q", password_hash="", is_admin=True)
        db.add(user)
        db.flush()
        sess = models.Session(user_id=user.id, title="q")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "q"}})
        db.add(plan)
        db.flush()
        q1 = models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"answer": "A"})
        q2 = models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"answer": "A"})
        db.add_all([q1, q2])
        db.flush()
        base = datetime.utcnow() - timedelta(days=1)
        # q1：15 条历史 + 最新一条判错（错题本依据）；q2：3 条
        for i in range(15):
            db.add(
                models.QuizAttempt(
                    quiz_id=q1.id,
                    user_answer={"selected": "A"},
                    correct=True,
                    created_at=base + timedelta(minutes=i),
                )
            )
        db.add(
            models.QuizAttempt(
                quiz_id=q1.id, user_answer={"selected": "B"}, correct=False
            )
        )
        for i in range(3):
            db.add(
                models.QuizAttempt(
                    quiz_id=q2.id,
                    user_answer={"selected": "A"},
                    correct=True,
                    created_at=base + timedelta(minutes=i),
                )
            )
        db.commit()
        q1_id, q2_id = q1.id, q2.id
    finally:
        db.close()

    removed = maint_mod.prune_quiz_attempts()
    assert removed == 6, f"应删 15+1 中的 6 条（超出 10 条部分），实际 {removed}"

    db = SessionLocal()
    try:
        kept1 = db.query(models.QuizAttempt).filter_by(quiz_id=q1_id).all()
        kept2 = db.query(models.QuizAttempt).filter_by(quiz_id=q2_id).all()
        assert len(kept1) == 10, len(kept1)
        assert len(kept2) == 3, len(kept2)
        # 最新一条（判错）必须还在 -> 错题本判定不变
        latest = max(kept1, key=lambda a: (a.created_at, a.id))
        assert latest.correct is False
    finally:
        db.close()


def test_prune_quiz_attempts_disabled_by_zero(monkeypatch):
    """keep=0 -> 不裁剪（保留全部历史）。"""
    from app.core import maintenance as maint_mod
    from app.core.settings import settings

    monkeypatch.setattr(settings, "quiz_attempt_keep_per_quiz", 0)
    assert maint_mod.prune_quiz_attempts() == 0


# --- 5. 错题队列 limit 边界 --------------------------------------------------


def test_wrong_queue_limit_bounds(client, auth_headers):
    """limit=0 / 超上限 -> 422（此前被夹取成 1，语义与其它分页端点不一致）。"""
    assert client.get("/api/quizzes/wrong-queue?limit=0", headers=auth_headers).status_code == 422
    assert client.get("/api/quizzes/wrong-queue?limit=201", headers=auth_headers).status_code == 422
    ok = client.get("/api/quizzes/wrong-queue?limit=10", headers=auth_headers)
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("path", ["/api/quizzes/wrong-overview", "/api/quizzes/wrong-queue"])
def test_wrong_endpoints_are_user_isolated(client, auth_headers, path):
    """读端点按用户隔离（他人数据不进结果）——隔离是限流之外的第二道护栏。"""
    r = client.get(path, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("items") == []
