"""POST /plans/{id}/quiz 端点测试（P2 测验，iter 38）。

先 mock 生成建 plan（CannedProvider 同 test_api_export），再调 quiz 端点。
"""

from __future__ import annotations

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(c):
    return LLMResponse(content=c, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self):
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


class _QuizProvider(LLMProvider):
    """quiz 端点专用 provider（返回 QuizSpec JSON）。"""

    def __init__(self, responses):
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return self._r.pop(0) if self._r else _resp("{}")


def _make_plan(client, auth_headers):
    from app.api import plans as plans_mod

    plans_mod.get_provider = lambda: CannedProvider()
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    import json as _json

    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return _json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_quiz_ok(client, auth_headers, monkeypatch):
    """LLM 合法规约 -> 200 + quizzes + quiz_count。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    spec_json = (
        '{"title":"React 测验","quizzes":['
        '{"node_id":"k1","node_name":"JS","type":"choice","question":"声明常量用？",'
        '"options":["var","let","const","function"],"answer":"const","explanation":"const 声明常量。"},'
        '{"node_id":"k1","node_name":"JS","type":"short","question":"什么是闭包？",'
        '"options":[],"answer":"携带词法作用域的函数","explanation":""}]}'
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda: _QuizProvider([_resp(spec_json)]))
    r = client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "React 测验"
    assert body["quiz_count"] == 2
    assert body["quizzes"][0]["type"] == "choice"
    assert body["quizzes"][0]["answer"] == "const"
    assert body["quizzes"][1]["type"] == "short"
    assert "warning" not in body  # 未降级


def test_quiz_degrade_no_provider(client, auth_headers, monkeypatch):
    """无 LLM provider（无 key）-> 降级 default_quiz_from_plan，仍 200 + warning。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quiz_count"] >= 1
    assert body["quizzes"][0]["type"] == "short"  # 默认自测题
    assert "JS" in body["quizzes"][0]["question"]  # 节点名入题
    assert "warning" in body  # 降级提示


def test_quiz_plan_not_found(client, auth_headers):
    r = client.post("/api/plans/999999/quiz", json={}, headers=auth_headers)
    assert r.status_code == 404
