"""iter 380 R23：删除族与在途操作竞态审计（真实端点 + 门控注入实证）。

对全部 DELETE 端点的「删除时在途操作」资源存活假设逐路径验证：
- 生成中删会话（DELETE /sessions）：_finalize 须走「会话已删除」守卫（err.md A12
  rowid 复用防错绑）——不写 session 级 DB、发终态、排程清理；**取消前已执行工具
  烧掉的计费（api_usage，user 键）必须幸存会话删除**（iter 380 修复：旧逻辑整批
  插 ToolCall 撞 session FK 失败回滚，在途已烧 token 全部蒸发——日预算只信
  api_usage 求和，缺账 = 预算绕过通道，违背 iter 243 成本守恒语义）。
  注：_STREAMS 终态后有 300s 保留窗（迟到重连续接，iter 237），收敛判据是流对象
  terminal_id 置位而非从台账消失。
- 对话 run 在途删 thread（DELETE /threads/{id}）：先硬取消（iter 363/369）再级联删；
  _finalize_chat 须走 run=None 分支优雅收口，无 FK 崩溃、无 _RUN_STREAMS 泄漏。
- 生成中删用户（DELETE /users/{id}，admin）：先取消全部 run/编排任务再级联；收敛后
  全库无该用户孤儿行（R9 只测过静止态级联，此处钉在途态——orphan 落账路径须识别
  用户已删并整段跳过，防 api_usage FK 孤儿行）。
- 运行态护栏端点级钉死（此前无端点级测试）：生成中删资料 409、解析中删 kb 文档 409、
  running run 删除 409；DB-running-无流残留（进程重启后形态）删 thread 200 收敛。

其余 DELETE（问答记录=请求内原子持久化无在途行；笔记/凭证/供应商/分享/记忆/打卡
日志=叶子行无在途写面）静态分派 clean。已知良性残留（留档）：生成 quiz 的 HTTP 请求
在途时会话被删 -> quiz 行插入撞 FK 500——计费已在调用完成时落账（user 键），无数据
损坏，语义即「会话已不存在」。

测试基建（iter 380 实证，坑）：**starlette 1.3.1 TestClient.stream() 是完全缓冲
transport**（portal.call 等 ASGI app 整体返回后才让 with 块进入）——「流进行中在主线程
做别的操作」不能用同步 `with client.stream(...)` 实现：
① 无超时兜底的挂起（如 patch 掉的 run_agent_loop 阻塞在 Event.wait）会让 with 永远
进不去（T2 首版即挂死于此）；② 有 LLM 120s 硬超时兜底的挂起要等 2 分钟才进 with
（T1 首版「侥幸」120.35s 通过，且 DELETE 实际发生在任务已收敛之后——测的已不是
「生成中删」）。正确模式＝**后台线程发流请求（挂在 transport 等 app 返回，恰是
「HTTP 请求在途」状态），主线程轮询到位后发 DELETE，终态后 join 线程**。
其余：流式端点必须用带 lifespan 的 fixture client（模块级裸 TestClient 无 portal，
后台任务/SSE 行为失真会挂死）；GatedProvider 脚本耗尽后阻塞在 asyncio.Event，
DELETE 触发的 task.cancel 在该 await 点打断（与生产硬取消同路径）；轮询探活走
fixture client 的同一 portal 事件循环，顺带驱动后台任务推进。
"""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

_C_INTENT = '{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'


def _resp(content: str, tin: int = 100, tout: int = 50) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=tin, output_tokens=tout, model="claude-sonnet-5"))


class GatedProvider(LLMProvider):
    """iter 377 ScriptedProvider 的门控版：脚本耗尽后下一次调用阻塞在 asyncio.Event，
    模拟「第二个工具的 LLM 调用在途」——测试主线程此时发 DELETE，task.cancel 在
    Event.wait 处打断（与生产硬取消同路径）。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.blocked = False

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.calls += 1
        if self.outcomes:
            act, payload = self.outcomes.pop(0)
            if act == "ok":
                return payload
            raise payload
        self.blocked = True
        await asyncio.Event().wait()  # 永不 set：只等 cancel 打断
        raise AssertionError("不应走到这")


def _mk_user(name: str, *, admin: bool = False) -> tuple[int, dict]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x", is_admin=admin)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, {"Authorization": f"Bearer {create_token(u.id, name)}"}
    finally:
        db.close()


def _stream_in_background(
    client: TestClient, method: str, url: str, *, json_body: dict, headers: dict
) -> tuple[threading.Thread, dict]:
    """后台线程发流式请求：线程挂在 TestClient 缓冲 transport 上等 app 返回——
    正是「HTTP 请求在途」状态。box 记录 status_code / error 供事后断言。"""

    box: dict = {}

    def _worker() -> None:
        try:
            with client.stream(method, url, json=json_body, headers=headers) as r:
                box["status"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            box["error"] = repr(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t, box


def _wait_until(client: TestClient, pred, *, timeout: float = 15.0, what: str = "条件") -> None:
    """轮询等待：health 探活走 fixture client 同一 portal 事件循环，顺带驱动后台任务。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.get("/api/health")
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"等待超时: {what}")


def _join(t: threading.Thread, box: dict) -> None:
    """终态后收编后台流线程：app 已返回（_relay 见终态退出）则 with 很快退出。"""
    t.join(timeout=25.0)
    assert not t.is_alive(), "后台流线程未退出（SSE 未收敛——删除未正确终止在途流）"
    assert "error" not in box, box.get("error")
    assert box.get("status") == 200, box


def _settle(client: TestClient) -> None:
    time.sleep(0.2)
    client.get("/api/health")


def _usage(uid: int) -> tuple[int, int, int]:
    db = SessionLocal()
    try:
        rows = db.query(models.ApiUsage).filter_by(user_id=uid).all()
        return len(rows), sum(u.tokens_in for u in rows), sum(u.tokens_out for u in rows)
    finally:
        db.close()


# --- T1：生成中删会话——finalize 守卫 + 计费幸存（iter 380 修复钉子）+ 零残留 ---


def test_session_delete_mid_generation_bills_and_leaves_no_residue(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db = SessionLocal()
    try:
        uid = db.get(models.Session, sid).user_id
    finally:
        db.close()

    prov = GatedProvider([("ok", _resp(_C_INTENT))])  # intent 完成后 knowledge 调用阻塞
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: prov)
    t, box = _stream_in_background(
        client, "POST", "/api/plans/generate", json_body={"session_id": sid, "raw_text": "我想学 Rust"}, headers=auth_headers
    )
    _wait_until(client, lambda: prov.blocked, what="第二工具调用进入在途")
    # intent 已完成但尚未落账（_persist_tool_calls 在 _finalize 统一落）
    assert _usage(uid) == (0, 0, 0)

    # 删除会话（先取消编排任务再级联删）——真正的「生成中删」时序
    r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    # 任务收敛终态（_STREAMS 保留窗 300s 供迟到重连，以流对象 terminal 判收敛）
    _wait_until(
        client,
        lambda: (s := plans_mod._STREAMS.get(sid)) is None or s.terminal_id is not None,
        what="删除后编排任务收敛终态",
    )
    _join(t, box)
    _settle(client)

    db = SessionLocal()
    try:
        assert db.get(models.Session, sid) is None
        assert db.get(models.OrchestratorState, sid) is None
        assert db.query(models.Plan).filter_by(session_id=sid).count() == 0
        assert db.query(models.ToolCall).filter_by(session_id=sid).count() == 0
    finally:
        db.close()
    # 计费幸存（iter 380 修复）：取消前已执行的 intent 调用（100/50 token）落 api_usage
    # ——旧逻辑整批 ToolCall 撞 session FK 回滚，已烧 token 蒸发（预算绕过通道）
    rows, tin, tout = _usage(uid)
    assert rows >= 1 and tin >= 100 and tout >= 50, (rows, tin, tout)


# --- T2：对话 run 在途删 thread——取消 + 级联 + finalize 优雅收口 ---


def test_thread_delete_mid_run_converges_clean(client, monkeypatch):
    uid, h = _mk_user("del_race_t2")
    tid = client.post("/api/threads", json={"title": "t"}, headers=h).json()["id"]

    from app.api import memory as mem_mod
    from app.llm.anthropic_provider import AnthropicProvider

    state: dict = {}

    async def gated_loop(ctx, **kwargs):
        state["blocked"] = True
        await asyncio.Event().wait()  # 等 task.cancel 打断
        raise AssertionError("不应走到这")

    monkeypatch.setattr(mem_mod, "get_provider", lambda *a, **k: AnthropicProvider("k", "m", client=object()))
    monkeypatch.setattr(mem_mod, "run_agent_loop", gated_loop)

    t, box = _stream_in_background(
        client, "POST", f"/api/threads/{tid}/runs", json_body={"message": "hi"}, headers=h
    )
    _wait_until(client, lambda: state.get("blocked"), what="chat run 进入在途")

    r = client.delete(f"/api/threads/{tid}", headers=h)
    assert r.status_code == 200, r.text
    _wait_until(client, lambda: len(mem_mod._RUN_STREAMS) == 0, what="_RUN_STREAMS 清空")
    _join(t, box)
    _settle(client)

    db = SessionLocal()
    try:
        assert db.get(models.Thread, tid) is None
        assert db.query(models.Run).filter_by(thread_id=tid).count() == 0
        assert db.query(models.Message).filter_by(thread_id=tid).count() == 0
        assert db.query(models.Run).filter_by(user_id=uid).count() == 0
    finally:
        db.close()
    assert len(mem_mod._RUN_STREAMS) == 0, "run 流表须清空（无泄漏）"


# --- T3：生成中删用户（admin）——在途取消 + 全库零孤儿（orphan 落账识别用户已删） ---


def test_user_delete_mid_generation_no_orphans(client, monkeypatch):
    from app.api import plans as plans_mod

    victim_uid, _ = _mk_user("del_race_victim")
    admin_uid, ah = _mk_user("del_race_admin", admin=True)
    db = SessionLocal()
    try:
        sess = models.Session(user_id=victim_uid, title="v")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        sid = sess.id
    finally:
        db.close()

    prov = GatedProvider([("ok", _resp(_C_INTENT))])
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: prov)
    vh = {"Authorization": f"Bearer {create_token(victim_uid, 'del_race_victim')}"}
    t, box = _stream_in_background(
        client, "POST", "/api/plans/generate", json_body={"session_id": sid, "raw_text": "我想学 Rust"}, headers=vh
    )
    _wait_until(client, lambda: prov.blocked, what="victim 生成在途")
    assert _usage(victim_uid) == (0, 0, 0)  # 计费未落（在台账，删除时随用户级联清除，属设计）

    r = client.delete(f"/api/users/{victim_uid}", headers=ah)
    assert r.status_code == 200, r.text
    _wait_until(
        client,
        lambda: (s := plans_mod._STREAMS.get(sid)) is None or s.terminal_id is not None,
        what="victim 编排任务收敛终态",
    )
    _join(t, box)
    _settle(client)

    db = SessionLocal()
    try:
        assert db.get(models.User, victim_uid) is None
        # 全库孤儿扫描（R9 静止态级联的在途态复验）：user 键直查 + session 键联查
        for m in (
            models.Session, models.LlmProviderProfile, models.ApiUsage, models.Thread,
            models.Run, models.Note, models.KbDocument, models.CredentialProfile,
            models.UserPref, models.KbChatRecord,
        ):
            assert db.query(m).filter_by(user_id=victim_uid).count() == 0, m.__tablename__
        assert db.query(models.Plan).filter(models.Plan.session_id == sid).count() == 0
        assert db.query(models.ToolCall).filter_by(session_id=sid).count() == 0
        assert db.query(models.OrchestratorState).filter_by(session_id=sid).count() == 0
        # 管理员完好
        assert db.get(models.User, admin_uid) is not None
    finally:
        db.close()


# --- T4：运行态护栏端点级钉死（此前无端点级测试） ---


def test_material_delete_blocked_while_generating_409(client, auth_headers):
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db = SessionLocal()
    try:
        db.add(models.Material(session_id=sid, kind=models.MaterialKind.text, source="s"))
        db.flush()
        db.add(models.OrchestratorState(session_id=sid, status=models.OrchestratorStatus.running, trace_id="t4"))
        db.commit()
        mid = db.query(models.Material).filter_by(session_id=sid).first().id
    finally:
        db.close()
    r = client.delete(f"/api/sessions/{sid}/materials/{mid}", headers=auth_headers)
    assert r.status_code == 409, r.text


def test_kb_doc_delete_blocked_while_parsing_409(client):
    uid, h = _mk_user("del_race_kb")
    db = SessionLocal()
    try:
        doc = models.KbDocument(user_id=uid, title="d", kind=models.MaterialKind.file)
        db.add(doc)
        db.commit()
        db.refresh(doc)
    finally:
        db.close()
    from app.api.kb import _PARSE_LOCKS

    lock = threading.Lock()
    lock.acquire()
    _PARSE_LOCKS[doc.id] = lock
    try:
        r = client.delete(f"/api/kb/documents/{doc.id}", headers=h)
        assert r.status_code == 409, r.text
    finally:
        _PARSE_LOCKS.pop(doc.id, None)
        lock.release()


def test_run_delete_blocked_while_running_409_and_stale_thread_delete_ok(client):
    uid, h = _mk_user("del_race_t4c")
    db = SessionLocal()
    try:
        t = models.Thread(user_id=uid, title="t", kind="chat")
        db.add(t)
        db.flush()
        db.add(models.Run(thread_id=t.id, user_id=uid, status=models.RunStatus.running, model=""))
        db.commit()
        run = db.query(models.Run).filter_by(thread_id=t.id).one()
        tid, rid = t.id, run.id
    finally:
        db.close()
    # running run 拒删（须先取消/等待）；HTTPException 409 被统一错误信封 handler 改写
    r = client.delete(f"/api/runs/{rid}", headers=h)
    assert r.status_code == 409 and "run_running" in r.text, r.text
    # DB-running-无流残留（进程重启后形态）：删 thread 须照样收敛（不因僵尸 running 行卡死）
    r = client.delete(f"/api/threads/{tid}", headers=h)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.get(models.Thread, tid) is None
        assert db.get(models.Run, rid) is None
    finally:
        db.close()
