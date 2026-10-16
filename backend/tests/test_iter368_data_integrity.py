"""iter 368 批次八（测验/进度/材料数据正确性）回归：

- /quizzes/wrong-queue 截断按「最近判错在前」（最新 attempt id 倒序后再截断）
- choice 空 answer（""）不可判分（correct=None，不再 ""=="" 判对）
- materials._store 解析后重查去重：并发同哈希插入 -> 复用其行（不重复建行）
- _schedule_quiz_background 调度点同步登记 inflight（不等信号量排队）
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.db import models
from app.db.base import SessionLocal


def _seed_quiz(db, plan_id: int, answer: str | None = "B", *, payload_extra: dict | None = None):
    payload = {
        "question": "q",
        "options": ["A", "B"],
        "answer": answer,
        "explanation": "e",
    }
    if payload_extra:
        payload.update(payload_extra)
    q = models.Quiz(plan_id=plan_id, type=models.QuizType.choice, payload=payload)
    db.add(q)
    db.flush()
    return q


def _mk_session_plan(db) -> tuple[int, int]:
    u = models.User(name="u", password_hash="x")
    db.add(u)
    db.flush()
    s = models.Session(user_id=u.id, title="s")
    db.add(s)
    db.flush()
    p = models.Plan(session_id=s.id, version=1)
    db.add(p)
    db.commit()
    return s.id, p.id


def test_wrong_queue_truncation_recent_first(client: TestClient):
    db = SessionLocal()
    try:
        _, plan_id = _mk_session_plan(db)
        quizzes = [_seed_quiz(db, plan_id) for _ in range(3)]
        db.commit()
        # 三题全部判错：attempt id 升序即判错时间升序（q1 最早判错，q3 最近判错）
        for q in quizzes:
            db.add(models.QuizAttempt(quiz_id=q.id, user_answer={"selected": "A"}, correct=False))
        db.commit()
        quiz_ids = [q.id for q in quizzes]
    finally:
        db.close()
    r = client.get("/api/quizzes/wrong-queue?limit=2", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    items = body["items"]
    assert len(items) == 2
    # 截断保留的必须是最近判错的两题（q3、q2），且 q3 在前
    assert items[0]["id"] == quiz_ids[2]
    assert items[1]["id"] == quiz_ids[1]


def test_choice_empty_answer_not_graded_correct(client: TestClient):
    db = SessionLocal()
    try:
        _, plan_id = _mk_session_plan(db)
        q = _seed_quiz(db, plan_id, answer="")
        db.commit()
        qid = q.id
    finally:
        db.close()
    r = client.post(
        f"/api/quizzes/{qid}/submit",
        json={"user_answer": {"selected": ""}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["correct"] is None  # 空答案不可判分（此前 ""=="" 判对）


def test_store_rechecks_dedup_after_parse(client: TestClient, monkeypatch):
    """解析等待期间并发插入同哈希行 -> _store 应复用该行（deduped），不再建第二行。"""
    db = SessionLocal()
    try:
        sid, _ = _mk_session_plan(db)
        db.commit()
    finally:
        db.close()

    from app.api.materials import _hash_source
    from app.tools.base import ToolInput, ToolOutput

    text = "并发去重测试文本"

    async def fake_parse(inp: ToolInput, ctx) -> ToolOutput:
        # 模拟并发者在解析期间插入了同 content_hash 的行
        s = SessionLocal()
        try:
            s.add(
                models.Material(
                    session_id=sid,
                    kind="text",
                    source=text[:200],
                    parsed_chars=42,
                    meta={},
                    content_hash=_hash_source(text),
                )
            )
            s.commit()
        finally:
            s.close()
        return ToolOutput(tool="parse_material", data={"text": text, "meta": {}})

    monkeypatch.setattr("app.api.materials.parse_material", fake_parse)
    r = client.post(
        "/api/materials/text",
        json={"session_id": sid, "text": text},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["meta"].get("deduped") is True  # 复用了并发者插入的行
    db = SessionLocal()
    try:
        rows = db.query(models.Material).filter_by(session_id=sid).all()
        assert len(rows) == 1  # 没有重复建行
    finally:
        db.close()


def test_schedule_quiz_background_registers_inflight_immediately():
    """调度点同步登记：任务还排在 LLM 信号量队列时端点就能看到 inflight（iter 368）。"""
    from app.api import plans as plans_mod

    released = asyncio.Event()

    async def fake_bg(plan_id, session_id, user_id):
        await released.wait()

    monkey_target = plans_mod._generate_quiz_background
    plans_mod._generate_quiz_background = fake_bg
    try:
        async def scenario():
            plans_mod._schedule_quiz_background(4242, 1, 1)
            # 未 await、未走完任何事件循环 tick：登记必须已经生效
            assert 4242 in plans_mod._BG_QUIZ_INFLIGHT
            # 让后台任务跑完（fake_bg 等待 released）
            released.set()
            await asyncio.sleep(0.05)
            assert 4242 not in plans_mod._BG_QUIZ_INFLIGHT

        asyncio.run(scenario())
    finally:
        plans_mod._generate_quiz_background = monkey_target
        plans_mod._BG_QUIZ_INFLIGHT.discard(4242)
