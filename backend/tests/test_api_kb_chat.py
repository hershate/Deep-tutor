"""知识库问答测试（iter 216：POST /kb/chat + kb_answer 工具）。"""

from __future__ import annotations

import json

from app.db import models
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

BASE = "/api/kb"


def _db():
    from app.db.base import SessionLocal

    return SessionLocal()


def _mk_text_doc(client, headers, text="知识库笔记", title=None) -> dict:
    payload = {"text": text}
    if title is not None:
        payload["title"] = title
    r = client.post(f"{BASE}/documents/text", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _first_user_id() -> int:
    db = _db()
    try:
        return db.query(models.User).first().id
    finally:
        db.close()


class _ChatProvider(LLMProvider):
    """问答专用罐头 provider：记录 prompt，返回固定 JSON。"""

    def __init__(self, content: str):
        self._content = content
        self.seen_system: str | None = None
        self.seen_user: str | None = None

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.seen_system = system
        self.seen_user = user
        return LLMResponse(
            content=self._content,
            usage=LLMUsage(input_tokens=10, output_tokens=5, model="claude-sonnet-5"),
        )


def _patch_provider(monkeypatch, provider):
    from app.api import kb as kb_mod

    monkeypatch.setattr(kb_mod, "get_provider", lambda *a, **k: provider)


# --- 主流程：200 + 引用映射 + 计量 ---


def test_chat_ok_with_citations(client, auth_headers, monkeypatch):
    d1 = _mk_text_doc(client, auth_headers, "Python 是一种解释型语言", title="Python 入门")
    _mk_text_doc(client, auth_headers, "列表是可变序列", title="数据结构")
    provider = _ChatProvider(
        json.dumps({"answer": "根据《Python 入门》，Python 是解释型语言。", "used_doc_ids": [d1["id"]]})
    )
    _patch_provider(monkeypatch, provider)

    r = client.post(f"{BASE}/chat", json={"question": "Python 是什么？"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "解释型语言" in body["answer"]
    assert body["citations"] == [{"doc_id": d1["id"], "title": "Python 入门"}]
    assert body["omitted_titles"] == []
    assert body["context_docs"] == 2
    # 缺省上下文 = 全部库内文档（2 篇），问题与文档注入 prompt
    assert "Python 是什么？" in provider.seen_user
    assert "Python 是一种解释型语言" in provider.seen_user
    assert "列表是可变序列" in provider.seen_user
    # api_usage 记账（err.md A6）
    db = _db()
    try:
        usages = db.query(models.ApiUsage).all()
        assert len(usages) == 1
        assert usages[0].tokens_in == 10 and usages[0].tokens_out == 5
        assert usages[0].user_id == _first_user_id()
    finally:
        db.close()


def test_chat_specified_document_ids(client, auth_headers, monkeypatch):
    d1 = _mk_text_doc(client, auth_headers, "第一篇", title="A")
    _mk_text_doc(client, auth_headers, "第二篇", title="B")
    provider = _ChatProvider(json.dumps({"answer": "仅参考 A。", "used_doc_ids": [d1["id"]]}))
    _patch_provider(monkeypatch, provider)

    r = client.post(
        f"{BASE}/chat", json={"question": "q", "document_ids": [d1["id"]]}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["context_docs"] == 1
    assert "第一篇" in provider.seen_user and "第二篇" not in provider.seen_user


def test_chat_hallucinated_doc_ids_clipped(client, auth_headers, monkeypatch):
    """LLM 幻觉引用（未注入的 doc_id）-> post_process 白名单过滤，不进 citations。

    注：非 int 值（"str-id" 等）在 pydantic 规约校验层即被拒（触发修复重试），
    白名单过滤兜的是「int 但不在上下文」的幻觉（非 int 过滤见 _clip_used_ids 单测）。
    """
    d1 = _mk_text_doc(client, auth_headers, "真实文档", title="真")
    provider = _ChatProvider(
        json.dumps({"answer": "答", "used_doc_ids": [d1["id"], 999999, -1]})
    )
    _patch_provider(monkeypatch, provider)

    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["citations"] == [{"doc_id": d1["id"], "title": "真"}]


def test_chat_truncation_marker_injected(client, auth_headers, monkeypatch):
    """超长文档端点侧预截断：prompt 中带「…[已截断]」标注（非 loop 静默截断）。"""
    _mk_text_doc(client, auth_headers, "长" * 25_000, title="长文档")
    provider = _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []}))
    _patch_provider(monkeypatch, provider)

    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 200
    assert "…[已截断]" in provider.seen_user
    # 预截断在 loop 字段级 20k 阈值内（不被 loop 二次截断）
    payload = json.loads(provider.seen_user)
    doc_text = payload["args"]["documents"][0]["text"]
    assert len(doc_text) == 19_900 + len("…[已截断]")


def test_chat_omitted_when_total_budget_exhausted(client, auth_headers, monkeypatch):
    """总量预算耗尽的篇目 -> omitted_titles 回传（非静默丢弃）。

    缺省上下文按 id desc（新→旧）注入：第二篇先入（19k ≤ 30k），第一篇被预算耗尽。
    """
    from app.api import kb as kb_mod

    monkeypatch.setattr(kb_mod, "_CHAT_TOTAL_CHARS", 30_000)
    _mk_text_doc(client, auth_headers, "一" * 19_000, title="第一篇")
    _mk_text_doc(client, auth_headers, "二" * 19_000, title="第二篇")
    provider = _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []}))
    _patch_provider(monkeypatch, provider)

    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["context_docs"] == 1
    assert body["omitted_titles"] == ["第一篇"]
    assert "二" * 100 in provider.seen_user
    assert "一" * 100 not in provider.seen_user


# --- 错误映射 ---


def test_chat_no_llm_provider_503(client, auth_headers, monkeypatch):
    from app.api import kb as kb_mod

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(kb_mod, "get_provider", _raise)
    _mk_text_doc(client, auth_headers, "有文档")
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "no_llm_provider"


def test_chat_budget_exceeded_402(client, auth_headers, monkeypatch):
    from datetime import datetime, timezone

    _mk_text_doc(client, auth_headers, "有文档")  # 触发默认用户懒创建
    uid = _first_user_id()
    db = _db()
    try:
        db.add(
            models.ApiUsage(
                user_id=uid, model="m", tokens_in=1, tokens_out=1, cost=99.0,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        db.commit()
    finally:
        db.close()
    provider = _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []}))
    _patch_provider(monkeypatch, provider)
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "budget_exceeded"
    assert provider.seen_user is None  # 守卫在 LLM 调用前拦截


def test_chat_llm_failure_502(client, auth_headers, monkeypatch):
    """schema 重试耗尽（无 default_data）-> 502 笼统消息（R35 不回显上游细节）。"""
    _mk_text_doc(client, auth_headers, "有文档")
    provider = _ChatProvider("这不是 JSON")  # 每次校验失败，4 次尝试耗尽
    _patch_provider(monkeypatch, provider)
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "bad_gateway"


def test_chat_upstream_timeout_504(client, auth_headers, monkeypatch):
    import asyncio

    _mk_text_doc(client, auth_headers, "有文档")

    class _TimeoutProvider(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            raise asyncio.TimeoutError()

    _patch_provider(monkeypatch, _TimeoutProvider())
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "upstream_timeout"


# --- 校验与三件套 ---


def test_chat_empty_question_422(client, auth_headers, monkeypatch):
    _mk_text_doc(client, auth_headers, "有文档")
    provider = _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []}))
    _patch_provider(monkeypatch, provider)
    r = client.post(f"{BASE}/chat", json={"question": "   "}, headers=auth_headers)
    assert r.status_code == 422
    assert provider.seen_user is None


def test_chat_no_docs_422(client, auth_headers, monkeypatch):
    provider = _ChatProvider(json.dumps({"answer": "答", "used_doc_ids": []}))
    _patch_provider(monkeypatch, provider)
    r = client.post(f"{BASE}/chat", json={"question": "q"}, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_content"
    assert provider.seen_user is None


def test_chat_other_user_doc_404(client, auth_headers, monkeypatch):
    from app.core.security import hash_password

    d = _mk_text_doc(client, auth_headers, "私有文档")
    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    hb = {
        "Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'bob', 'password': 'pw1234'}).json()['token']}"
    }
    r = client.post(f"{BASE}/chat", json={"question": "q", "document_ids": [d["id"]]}, headers=hb)
    assert r.status_code == 404


def test_chat_unauthenticated_401(client):
    assert client.post(f"{BASE}/chat", json={"question": "q"}).status_code == 401


# --- 工具单元测试 ---


def test_clip_used_ids_unit():
    from app.tools.base import ToolInput
    from app.tools.kb_answer import _clip_used_ids

    inp = ToolInput(
        tool="kb_answer",
        session_id="kb-1",
        args={"question": "q", "documents": [{"doc_id": 1, "title": "a", "text": "t"}, {"doc_id": 2}]},
    )
    data = {"answer": "x", "used_doc_ids": [1, 2, 3, "1", None]}
    out = _clip_used_ids(data, inp)
    assert out["used_doc_ids"] == [1, 2]  # 3 不在白名单；"1"/None 非 int 被滤

    # documents 缺省/为空 -> 全滤
    inp2 = ToolInput(tool="kb_answer", session_id="kb-1", args={"question": "q"})
    assert _clip_used_ids({"answer": "x", "used_doc_ids": [1]}, inp2)["used_doc_ids"] == []


def test_kb_answer_registered_not_in_pipeline():
    """kb_answer 在 default_registry（端点可直调）但不在编排 PIPELINE（不污染生成链）。"""
    from app.agent import PIPELINE
    from app.tools import default_registry

    assert default_registry().has("kb_answer")
    assert "kb_answer" not in PIPELINE
