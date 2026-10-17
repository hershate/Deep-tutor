"""iter 371 R9：多用户高频并发 ASGI 级压测回归（高负载稳定性实证）。

五轮功能审计后，负载面的证据缺口是「实证」而非「读码」：本测试用 httpx
ASGITransport 直连应用，8 个虚拟用户并发执行建会话/改名/列表/笔记/用量等
高频读写组合 + 1 个攻击者客户端持续无效认证（应 401/429，绝不 5xx）。

通过标准：全响应零 5xx、每用户写入条数精确可见（无静默丢失）、结束后再查
库一致（连接池无泄漏迹象）。时长受控（并发回合数小），适合常驻回归套件。
"""

import asyncio

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

_N_USERS = 8
_ROUNDS = 6  # 每用户每资源操作数（受控时长）


def _mk_users() -> list[tuple[int, str]]:
    """建 N 个真实用户并直签 JWT（绕过登录端点的 per-IP 限流，隔离压测变量）。"""
    db = SessionLocal()
    try:
        out = []
        for i in range(_N_USERS):
            u = models.User(name=f"load{i}", username=f"load_{i}", password_hash="x")
            db.add(u)
            db.commit()
            db.refresh(u)
            out.append((u.id, create_token(u.id, u.username)))
        return out
    finally:
        db.close()


async def _user_flow(client: AsyncClient, token: str, uid: int) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    codes: list[int] = []
    for r in range(_ROUNDS):
        # 建会话（threads）
        resp = await client.post("/api/threads", json={"title": f"压测-{uid}-{r}"}, headers=headers)
        codes.append(resp.status_code)
        tid = resp.json()["id"] if resp.status_code == 200 else None
        if tid is not None:
            resp = await client.patch(f"/api/threads/{tid}", json={"title": f"改名-{r}"}, headers=headers)
            codes.append(resp.status_code)
        # 笔记写 + 读
        resp = await client.post(
            "/api/notes", json={"title": f"n{r}", "content": "x" * 100}, headers=headers
        )
        codes.append(resp.status_code)
        # 高频读
        resp = await client.get("/api/threads?limit=20", headers=headers)
        codes.append(resp.status_code)
        resp = await client.get("/api/usage", headers=headers)
        codes.append(resp.status_code)
    bad = [c for c in codes if c >= 500]
    assert not bad, f"用户 {uid} 出现 5xx：{bad}"


async def _attacker(client: AsyncClient) -> None:
    """无效 token 风暴：限流后应 429/401，不得 5xx（认证失败路径的负载口径）。"""
    codes = set()
    for _ in range(40):
        resp = await client.get("/api/usage", headers={"Authorization": "Bearer invalid-token-x"})
        codes.add(resp.status_code)
        assert resp.status_code < 500, resp.status_code
        await asyncio.sleep(0)  # 与其他流交织
    assert codes <= {401, 429}, codes


@pytest.mark.asyncio(loop_scope="function")
async def test_multi_user_burst_no_5xx_and_consistent_counts(monkeypatch):
    # 关闭开放本机模式（测试库默认零配置开放）——否则无效 token 也被放行，
    # 攻击者路径断言（401/429）不成立。deps 在请求期晚导入，patch security 模块级符号即可
    monkeypatch.setattr("app.core.security.passwordless_open", lambda db: False)
    users = _mk_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tasks = [_user_flow(client, tok, uid) for uid, tok in users]
        tasks.append(_attacker(client))
        await asyncio.gather(*tasks)

    # 一致性：每用户 threads 恰好 _ROUNDS 条、notes 恰好 _ROUNDS 条（无静默丢写）
    db = SessionLocal()
    try:
        for uid, _ in users:
            ths = db.query(models.Thread).filter_by(user_id=uid).count()
            assert ths == _ROUNDS, f"user {uid} threads={ths} 应为 {_ROUNDS}"
            nts = db.query(models.Note).filter_by(user_id=uid).count()
            assert nts == _ROUNDS, f"user {uid} notes={nts} 应为 {_ROUNDS}"
    finally:
        db.close()

    # 攻击者不产生任何用户（无效 token 不懒建 open admin 之外的行）
    db = SessionLocal()
    try:
        assert db.query(models.User).count() == _N_USERS
    finally:
        db.close()
