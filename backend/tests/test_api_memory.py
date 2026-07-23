"""记忆系统 REST API 集成测试（iter 345）—— threads/messages/memory/runs CRUD + Agent SSE（mock provider）。"""

from __future__ import annotations

from app.db import models
from app.db.base import SessionLocal
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import AgenticStreamChunk, LLMUsage


class _FakeChatProvider(AnthropicProvider):
    """AnthropicProvider 子类（过 isinstance 检查），覆写 stream_agentic 为脚本。"""

    def __init__(self, turns):  # turns: list[list[AgenticStreamChunk]]
        self._turns = list(turns)
        self._model = "fake-model"
        self._client = None

    async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
        for c in self._turns.pop(0):
            yield c


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                import json

                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


# --- threads CRUD ---


def test_threads_crud(client, auth_headers):
    r = client.post("/api/threads", json={"title": "学 React", "kind": "chat"}, headers=auth_headers)
    assert r.status_code == 200
    tid = r.json()["id"]
    assert r.json()["title"] == "学 React"

    r = client.get("/api/threads", headers=auth_headers)
    assert r.status_code == 200
    assert any(t["id"] == tid for t in r.json()["items"])

    r = client.patch(f"/api/threads/{tid}", json={"title": "改标题"}, headers=auth_headers)
    assert r.json()["title"] == "改标题"

    r = client.delete(f"/api/threads/{tid}", headers=auth_headers)
    assert r.json()["ok"] is True
    assert client.get(f"/api/threads/{tid}", headers=auth_headers).status_code == 404


def test_thread_cross_user_404(client, auth_headers):
    # 开放模式下当前用户在首次认证时懒创建——先触发并取其 id，再造属主为另一用户的 thread
    cur_id = client.get("/api/auth/me", headers=auth_headers).json()["id"]
    db = SessionLocal()
    other_user = models.User(name="other", username=f"other_{cur_id}", password_hash="x")
    db.add(other_user)
    db.commit()
    db.refresh(other_user)
    assert other_user.id != cur_id  # 确保不同用户
    other = models.Thread(user_id=other_user.id, title="他人的")
    db.add(other)
    db.commit()
    db.refresh(other)
    oid = other.id
    db.close()
    assert client.get(f"/api/threads/{oid}", headers=auth_headers).status_code == 404


# --- memory CRUD ---


def test_memory_thread_and_user_crud(client, auth_headers):
    t = client.post("/api/threads", json={"title": "t"}, headers=auth_headers).json()
    # thread-scoped
    r = client.post(f"/api/threads/{t['id']}/memory", json={"content": "用户偏好简洁", "kind": "fact"}, headers=auth_headers)
    mid = r.json()["id"]
    assert client.get(f"/api/threads/{t['id']}/memory", headers=auth_headers).json()["items"][0]["content"] == "用户偏好简洁"
    # user-scoped
    client.post("/api/memory", json={"content": "全局要点"}, headers=auth_headers)
    assert len(client.get("/api/memory", headers=auth_headers).json()["items"]) >= 1
    # delete
    assert client.delete(f"/api/threads/{t['id']}/memory/{mid}", headers=auth_headers).json()["ok"] is True


# --- messages ---


def test_messages_list_and_delete(client, auth_headers):
    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    db = SessionLocal()
    db.add_all([
        models.Message(thread_id=t["id"], role=models.MessageRole.user, content=[{"type": "text", "text": "你好"}]),
        models.Message(thread_id=t["id"], role=models.MessageRole.assistant, content=[{"type": "text", "text": "你好！"}]),
    ])
    db.commit()
    db.close()
    msgs = client.get(f"/api/threads/{t['id']}/messages", headers=auth_headers).json()["items"]
    assert len(msgs) == 2 and msgs[0]["role"] == "user"
    mid = msgs[0]["id"]
    assert client.delete(f"/api/threads/{t['id']}/messages/{mid}", headers=auth_headers).status_code == 200
    assert len(client.get(f"/api/threads/{t['id']}/messages", headers=auth_headers).json()["items"]) == 1


# --- Agent SSE run（mock provider）---


def test_run_sse_text_response(client, auth_headers, monkeypatch):
    provider = _FakeChatProvider(
        [[AgenticStreamChunk(text="你好，我是助手"), AgenticStreamChunk(usage=LLMUsage(input_tokens=5, output_tokens=3, model="fake-model"), stop_reason="end_turn")]]
    )
    monkeypatch.setattr("app.api.memory.get_provider", lambda uid: provider)
    t = client.post("/api/threads", json={"title": "对话"}, headers=auth_headers).json()
    r = client.post(
        f"/api/threads/{t['id']}/runs",
        json={"message": "hi"},
        headers={**auth_headers, "Accept": "text/event-stream"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    names = [e for e, _ in events]
    assert "run_start" in names
    assert "text_delta" in names
    assert "message_end" in names
    assert names[-1] == "done"
    # 消息持久化：user + assistant
    db = SessionLocal()
    msgs = db.query(models.Message).filter_by(thread_id=t["id"]).order_by(models.Message.id).all()
    db.close()
    assert len(msgs) == 2
    assert msgs[0].role == models.MessageRole.user and msgs[1].role == models.MessageRole.assistant
    # run 落库为 done
    db = SessionLocal()
    run = db.query(models.Run).filter_by(thread_id=t["id"]).first()
    assert run.status.value == "done"
    db.close()


def test_run_concurrent_409(client, auth_headers, monkeypatch):
    # 让 provider 永不结束（保持 running），触发并发 409
    provider = _FakeChatProvider(
        [[AgenticStreamChunk(text="思考中"), AgenticStreamChunk(usage=LLMUsage(model="fake-model"), stop_reason="end_turn")]]
    )
    monkeypatch.setattr("app.api.memory.get_provider", lambda uid: provider)
    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    # 先造一个 running run 占位
    db = SessionLocal()
    db.add(models.Run(thread_id=t["id"], user_id=1, status=models.RunStatus.running))
    db.commit()
    db.close()
    r = client.post(f"/api/threads/{t['id']}/runs", json={"message": "x"}, headers={**auth_headers, "Accept": "text/event-stream"})
    assert r.status_code == 409


def test_run_no_provider_error(client, auth_headers, monkeypatch):
    def boom(uid):
        raise RuntimeError("no key")

    monkeypatch.setattr("app.api.memory.get_provider", boom)
    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    r = client.post(f"/api/threads/{t['id']}/runs", json={"message": "x"}, headers={**auth_headers, "Accept": "text/event-stream"})
    events = _parse_sse(r.text)
    assert any(e == "error" and d.get("code") == "no_llm_provider" for e, d in events)
