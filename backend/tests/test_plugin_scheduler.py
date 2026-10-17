"""后端插件 API v1.10.0 api.scheduler 后台任务测试（API.md「§后台任务调度」）。

用 asyncio.Event + asyncio.wait_for(timeout) 做时序断言（非固定 sleep），抗 CI 抖动。
覆盖：every 周期跑 / once 延迟跑 / cancel 停止 / fn 异常隔离（不杀循环）/ reset 取消全部 /
sync fn / 入参校验 / 门面接线。
"""

from __future__ import annotations

import asyncio

import pytest

from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


def _facade() -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id="schedtest", name="schedtest", version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id="schedtest", data_dir="/tmp", status=status)


# --- every ---


def test_every_runs_periodically():
    async def main():
        api = _facade()
        ran = asyncio.Event()
        count = {"n": 0}

        async def fn():
            count["n"] += 1
            if count["n"] >= 2:
                ran.set()  # 跑满 2 次即认为周期生效

        h = api.scheduler.every(0.02, fn)
        try:
            await asyncio.wait_for(ran.wait(), timeout=2.0)
            assert count["n"] >= 2
        finally:
            h.cancel()

    asyncio.run(main())


def test_every_run_immediately():
    async def main():
        api = _facade()
        ran = asyncio.Event()
        await asyncio.sleep(0)  # 让 create_task 生效
        h = api.scheduler.every(60.0, lambda: ran.set(), run_immediately=True)
        try:
            await asyncio.wait_for(ran.wait(), timeout=1.0)  # 立即跑一次（不等 60s）
            assert ran.is_set()
        finally:
            h.cancel()

    asyncio.run(main())


# --- once ---


def test_once_runs_after_delay():
    async def main():
        api = _facade()
        ran = asyncio.Event()
        h = api.scheduler.once(0.02, lambda: ran.set())
        try:
            await asyncio.wait_for(ran.wait(), timeout=1.0)
            assert ran.is_set()
        finally:
            h.cancel()

    asyncio.run(main())


def test_once_cancel_prevents_run():
    async def main():
        api = _facade()
        ran = asyncio.Event()
        h = api.scheduler.once(0.05, lambda: ran.set())
        h.cancel()  # 立即取消，fn 不应跑
        await asyncio.sleep(0.15)
        assert not ran.is_set()

    asyncio.run(main())


# --- cancel / 错误隔离 / reset ---


def test_every_cancel_stops():
    async def main():
        api = _facade()
        count = {"n": 0}
        h = api.scheduler.every(0.02, lambda: count.__setitem__("n", count["n"] + 1))
        await asyncio.sleep(0.07)  # 跑 ~2-3 次
        h.cancel()
        # iter 381：先等一个窗口再采样基线——cancel 打断的是调度循环的等待，已在途的
        # to_thread fn 线程不可中断且必然完成（iter 368 sync-fn 移线程池的固有语义：
        # cancel 停后续轮次，不抹掉已开始的那次）。高负载下该轮可能恰在 cancel 时刻
        # 启动，立即采样会把这次完成误判为「cancel 后仍在跑」（全量套件偶发红的根因）。
        await asyncio.sleep(0.12)
        after = count["n"]
        await asyncio.sleep(0.1)  # 取消后不应再有新轮次开始
        assert count["n"] == after  # 计数稳定

    asyncio.run(main())


def test_fn_exception_isolated():
    """fn 抛异常 -> _safe_run 捕获记日志，循环存活、后续 fn 仍跑（不杀循环）。"""
    from app.plugins import scheduler

    async def main():
        ran = asyncio.Event()
        state = {"first": True}

        async def fn():
            if state["first"]:
                state["first"] = False
                raise RuntimeError("首次炸了")
            ran.set()  # 第二次正常跑 -> 循环存活

        h = scheduler.every(0.02, fn)
        try:
            await asyncio.wait_for(ran.wait(), timeout=2.0)
            assert ran.is_set()  # 异常未杀循环
        finally:
            h.cancel()

    asyncio.run(main())


def test_sync_fn_supported():
    """sync fn（非 async）亦被支持（直接调，须非阻塞）。"""
    from app.plugins import scheduler

    async def main():
        ran = asyncio.Event()
        h = scheduler.every(0.02, lambda: ran.set(), run_immediately=True)
        try:
            await asyncio.wait_for(ran.wait(), timeout=1.0)
            assert ran.is_set()
        finally:
            h.cancel()

    asyncio.run(main())


def test_reset_cancels_all():
    """reset() 取消全部调度任务（reset_plugins 调）。"""
    from app.plugins import scheduler

    async def main():
        a = asyncio.Event()
        b = asyncio.Event()
        scheduler.every(0.02, lambda: a.set(), run_immediately=True)
        scheduler.once(0.02, lambda: b.set())
        await asyncio.sleep(0.05)  # 让任务跑起来
        scheduler.reset()
        # reset 后无悬挂任务（_tasks 清空）
        assert scheduler._tasks == []  # noqa: SLF001

    asyncio.run(main())


# --- 入参校验 + 门面 ---


def test_invalid_intervals():
    api = _facade()

    async def main():
        with pytest.raises(ValueError, match="interval_s"):
            api.scheduler.every(0, lambda: None)
        with pytest.raises(ValueError, match="delay_s"):
            api.scheduler.once(-1, lambda: None)
        with pytest.raises(TypeError):
            api.scheduler.every(1, "not-callable")  # type: ignore[arg-type]

    asyncio.run(main())


def test_facade_exposes_scheduler():
    api = _facade()
    assert callable(api.scheduler.every)
    assert callable(api.scheduler.once)
