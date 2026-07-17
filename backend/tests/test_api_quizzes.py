"""quizzes 持久化 + submit 端点测试（P2 iter 42，plan/02 §8 / plan/03 §一）。

POST /plans/{id}/quiz 幂等持久化（refresh 强制重生成、替换式）；
POST /quizzes/{id}/submit 服务器判分 + quiz_attempts 落库。
"""

from __future__ import annotations

import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]

_SPEC = (
    '{"title":"React 测验","quizzes":['
    '{"node_id":"k1","node_name":"JS","type":"choice","question":"声明常量用？",'
    '"options":["var","const"],"answer":"const","explanation":"const 声明常量。"},'
    '{"node_id":"kx","node_name":"未知","type":"short","question":"什么是闭包？",'
    '"options":[],"answer":"携带词法作用域的函数","explanation":""}]}'
)


class CannedProvider(LLMProvider):
    def __init__(self) -> None:
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


class _QuizProvider(LLMProvider):
    def __init__(self, spec: str = _SPEC) -> None:
        self._spec = spec
        self.calls = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.calls += 1
        return _resp(self._spec)


def _make_plan(client, auth_headers, monkeypatch) -> int:
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: CannedProvider())
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def _gen_quiz(client, auth_headers, monkeypatch, plan_id: int, provider: LLMProvider, body: dict | None = None) -> dict:
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: provider)
    r = client.post(f"/api/plans/{plan_id}/quiz", json=body or {}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _quizzes_of(plan_id: int):
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        return db.query(models.Quiz).filter_by(plan_id=plan_id).all()
    finally:
        db.close()


def test_quiz_persisted_and_node_mapped(client, auth_headers, monkeypatch):
    """生成即落库：响应带 DB id；node_id 按 sid 映射（k1 -> DB id，kx 查无 -> None）。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    body = _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider())

    assert body["quiz_count"] == 2
    assert all("id" in q for q in body["quizzes"])

    rows = _quizzes_of(plan_id)
    assert len(rows) == 2
    by_type = {r.type.value: r for r in rows}
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        k1 = db.query(models.KnowledgeNode).filter_by(plan_id=plan_id, sid="k1").one()
    finally:
        db.close()
    assert by_type["choice"].node_id == k1.id  # sid k1 -> DB id
    assert by_type["short"].node_id is None  # kx 查无 -> None（不阻塞出题）
    assert by_type["choice"].payload["answer"] == "const"
    assert by_type["choice"].payload["node_id"] == "k1"  # payload 保留 sid


def test_quiz_idempotent_no_regenerate(client, auth_headers, monkeypatch):
    """已持久化 -> 再 POST 直接返回（免重复 LLM 成本）；provider 失效也不影响。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    provider = _QuizProvider()
    first = _gen_quiz(client, auth_headers, monkeypatch, plan_id, provider)
    assert provider.calls == 1

    from app.api import plans as plans_mod

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    second = r.json()
    assert [q["id"] for q in second["quizzes"]] == [q["id"] for q in first["quizzes"]]
    assert "warning" not in second  # 非降级：返回的是已持久化的 LLM 题


def test_quiz_refresh_regenerates(client, auth_headers, monkeypatch):
    """refresh=true -> 重新生成并替换旧题（attempts 级联删，不撞 FK）。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    first = _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider())
    old_id = first["quizzes"][0]["id"]
    # 旧题留一条 attempt，refresh 时应级联删
    client.post(f"/api/quizzes/{old_id}/submit", json={"user_answer": {"selected": "const"}}, headers=auth_headers)

    new_spec = _SPEC.replace("声明常量用？", "声明变量用？")
    second = _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider(new_spec), body={"refresh": True})
    assert second["quizzes"][0]["question"] == "声明变量用？"  # 已重新生成（替换式）

    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        assert db.query(models.Quiz).filter_by(plan_id=plan_id).count() == 2
        assert db.query(models.QuizAttempt).count() == 0  # 旧 attempts 已级联删
    finally:
        db.close()


def test_quiz_degrade_also_persisted(client, auth_headers, monkeypatch):
    """无 provider -> 降级默认自测题也落库；再 POST 返回已存默认题。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)

    from app.api import plans as plans_mod

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "warning" in r.json()
    assert len(_quizzes_of(plan_id)) >= 1


def test_submit_choice_judged_server_side(client, auth_headers, monkeypatch):
    """choice submit：服务器比对 answer 判对错，attempt 落库。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    body = _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider())
    choice = next(q for q in body["quizzes"] if q["type"] == "choice")

    r = client.post(f"/api/quizzes/{choice['id']}/submit", json={"user_answer": {"selected": "const"}}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"correct": True, "answer": "const", "explanation": "const 声明常量。"}

    r = client.post(f"/api/quizzes/{choice['id']}/submit", json={"user_answer": {"selected": "var"}}, headers=auth_headers)
    assert r.json()["correct"] is False

    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        attempts = db.query(models.QuizAttempt).filter_by(quiz_id=choice["id"]).all()
        assert [a.correct for a in attempts] == [True, False]
        assert attempts[0].user_answer == {"selected": "const"}
    finally:
        db.close()


def test_submit_short_correct_null(client, auth_headers, monkeypatch):
    """short 自评题：correct=None，attempt 仍落库。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    body = _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider())
    short = next(q for q in body["quizzes"] if q["type"] == "short")

    r = client.post(f"/api/quizzes/{short['id']}/submit", json={"user_answer": {"text": "我的自答"}}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["correct"] is None
    assert r.json()["answer"] == "携带词法作用域的函数"


def test_submit_not_found(client, auth_headers):
    r = client.post("/api/quizzes/999999/submit", json={"user_answer": {"selected": "x"}}, headers=auth_headers)
    assert r.status_code == 404
