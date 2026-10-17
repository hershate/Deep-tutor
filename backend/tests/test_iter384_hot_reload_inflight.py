"""R27（iter 384）：插件热重载 × 线程/在途语义回归。

S1：热管理端点（reload/disable/enable）原为 sync def——FastAPI 跑 anyio 线程池，
    register 内 api.scheduler.every/once 的 get_running_loop() 必抛 RuntimeError ->
    调度器插件启动（lifespan 内有循环）成功、热重载/热启用永远 failed（行为不一致）。
    修复：端点 async def（register/回滚回事件循环线程，与启动同语义）。
S2：卸载回滚的 TaskHandle.cancel 在线程池线程调 task.cancel()——asyncio Task 非线程
    安全（debug 模式 _check_thread 抛 RuntimeError，被回滚的异常隔离吞掉 -> 任务泄漏、
    已卸载插件的旧代码永续后台跑）。修复：scheduler._cancel_task 经 call_soon_threadsafe。
附带：every/once 在无循环线程（插件 sync 路由）回退 bind_loop 绑定的宿主主循环。

时序断言用轮询 _wait_until（非固定 sleep），抗 CI 抖动。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

# 调度器插件：周期任务递增模块级计数，路由暴露计数，register 注册 every（S1 触发面）
SCHED_PLUGIN = """
from fastapi import APIRouter

PLUGIN_META = {"id": "sch", "name": "SCH", "version": "0.1.0"}

TICKS = {"n": 0}
HANDLE = {"h": None}

def _tick():
    TICKS["n"] += 1

def register(api):
    router = APIRouter()

    @router.get("/ticks")
    def ticks():
        return {"n": TICKS["n"]}

    api.routes.mount(router, auth=False)
    HANDLE["h"] = api.scheduler.every(0.05, _tick)
"""


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_admin() -> None:
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()


# --- S1 端到端：调度器插件的完整热生命周期（启动/reload/disable/enable） ---


def test_scheduler_plugin_hot_reload_endpoints(tmp_path, monkeypatch):
    """S1 回归：修复前 reload/enable 端点对调度器插件必返 failed（no running event loop）。"""
    _make_admin()
    (tmp_path / "sch.py").write_text(SCHED_PLUGIN, encoding="utf-8")
    from app.core.settings import settings

    monkeypatch.setattr(settings, "plugins_dir", str(tmp_path))
    from app.main import create_app

    app = create_app()
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as tc:
        # 启动加载（lifespan 在 portal 循环上）：register 内 every 成功，任务真在跑
        assert _wait_until(lambda: tc.get("/api/plugins/sch/ticks").json()["n"] >= 2)
        # 抓住插件模块对象（disable 后 sys.modules 逐出，但对象引用保活可查状态）
        mod = sys.modules["dt_plugin_sch"]
        h_old = mod.HANDLE["h"]

        # 热重载：修复前 sync def 端点在线程池跑 register -> RuntimeError -> failed
        r = tc.post("/api/plugins/sch/reload", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["plugin"]["state"] == "loaded"

        # 旧任务已被回滚取消（S2 端点路径：取消在循环线程上同步完成）
        assert _wait_until(lambda: h_old._task.done())  # noqa: SLF001
        frozen = mod.TICKS["n"]
        time.sleep(0.15)
        assert mod.TICKS["n"] == frozen  # 旧代码不再跑

        # 新任务在跑（重载后模块重导入，计数从 0 重新累计）
        assert _wait_until(lambda: tc.get("/api/plugins/sch/ticks").json()["n"] >= 1)

        # disable：任务取消 + 路由卸载
        mod2 = sys.modules["dt_plugin_sch"]
        h2 = mod2.HANDLE["h"]
        assert tc.post("/api/plugins/sch/disable", headers=headers).status_code == 200
        assert _wait_until(lambda: h2._task.done())  # noqa: SLF001
        frozen2 = mod2.TICKS["n"]
        time.sleep(0.15)
        assert mod2.TICKS["n"] == frozen2  # 计数冻结
        assert tc.get("/api/plugins/sch/ticks").status_code == 404  # 路由已卸载

        # enable：恢复（修复前同样必 failed）
        r = tc.post("/api/plugins/sch/enable", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["plugin"]["state"] == "loaded"
        assert _wait_until(lambda: tc.get("/api/plugins/sch/ticks").json()["n"] >= 1)


# --- S2 单元：线程池线程 cancel 句柄（debug 模式钉死旧实现的红） ---


def test_cancel_from_foreign_thread_cancels():
    """跨线程 cancel：旧实现 task.cancel() 在 debug 模式抛 RuntimeError（非线程安全
    操作）且任务不取消；新实现经 call_soon_threadsafe 取消必达。"""
    from app.plugins import scheduler

    async def main():
        ran = asyncio.Event()
        # iter 386：时序裕量放宽——debug 模式 asyncio 开销大且全量套件高负载下，0.02s
        # 取消对 0.05s 首跳仅 30ms 裕量（全量偶发红）；0.5s 首跳留 0.45s 取消窗口
        h = scheduler.every(0.5, lambda: ran.set())  # 首跑在 t=0.5
        await asyncio.sleep(0.05)  # 任务处于 sleep 等待（_fut_waiter 非 None）
        err: dict = {}

        def _cancel():
            try:
                h.cancel()
            except Exception as exc:  # noqa: BLE001
                err["exc"] = exc

        t = threading.Thread(target=_cancel)
        t.start()
        t.join()
        assert "exc" not in err, f"跨线程 cancel 抛异常: {err.get('exc')!r}"

        # 取消已投递到循环——异步轮询等待任务终止（同步 sleep 会堵死循环，回调永不到达）
        async def _await_done() -> bool:
            deadline = time.monotonic() + 5.0
            while not h._task.done() and time.monotonic() < deadline:  # noqa: SLF001
                await asyncio.sleep(0.01)
            return h._task.done()  # noqa: SLF001

        assert await _await_done()
        assert h._task.cancelled() or h._task.done()  # noqa: SLF001
        assert not ran.is_set()  # 0.5 间隔、t=0.05 取消 -> fn 从未到点

    asyncio.run(main(), debug=True)  # debug=True：跨线程不安全操作必炸（旧实现红）


# --- every/once 无循环线程回退绑定循环（插件 sync 路由自行注册调度的面） ---


def test_every_offloop_falls_back_to_bound_loop():
    from app.plugins import scheduler

    async def main():
        scheduler.bind_loop(asyncio.get_running_loop())
        try:
            ran = asyncio.Event()
            holder: dict = {}

            def _register():
                try:
                    holder["h"] = scheduler.every(0.05, lambda: ran.set(), run_immediately=True)
                except Exception as exc:  # noqa: BLE001
                    holder["error"] = exc

            t = threading.Thread(target=_register)
            t.start()
            # join 移线程池：协程内直接 join 会堵死事件循环，call_soon_threadsafe
            # 的创建回调无法执行 -> 握手 10s 超时（死锁假红）
            await asyncio.to_thread(t.join)
            assert "error" not in holder, f"线程池上下文注册失败: {holder.get('error')!r}"
            # 任务真实跑在绑定循环上
            await asyncio.wait_for(ran.wait(), timeout=2.0)
        finally:
            scheduler.bind_loop(None)

    asyncio.run(main())


def test_every_offloop_without_bound_raises_clear_error():
    """无循环且未绑定 -> 明确报错（与旧的裸 no running event loop 相比可诊断）。"""
    from app.plugins import scheduler

    scheduler.bind_loop(None)
    with pytest.raises(RuntimeError, match="未绑定"):
        scheduler.every(1.0, lambda: None)
