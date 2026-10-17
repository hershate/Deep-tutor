"""iter 385 R28：真实进程插件热管理 soak（手工运行，不自动收集）。

5 个插件（调度器 / 中间件 / webhook+事件 / LLM Provider+计量 / api.tools.call）×
并发 reload/disable/enable 交错 × 在途请求风暴 + SSE 事件流常开 + webhook 投递守恒 +
api_usage 计量守恒。复验 iter 384 R27 修复的线上语义：

    S1 复验：调度器插件经真实 HTTP reload/enable 后保持 loaded（修复前热路径必 failed）。
    S2 复验：调度任务随 disable 即刻冻结（跨线程取消语义下零任务泄漏）。
    面：中间件热挂/摘在风暴中生效且零 5xx；Provider 计量行数与成功响应精确守恒
       （含 reload 交错期间在途调用）；webhook 投递带 HMAC 签名且稳定期零丢失；
       全局 SSE 事件流不受插件热管理扰动（心跳持续）。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_plugin_hotreload.py
（临时 DATA_DIR/DB_PATH/PLUGINS_DIR，不碰真实 data/；结束后清理）
"""

from __future__ import annotations

import hashlib
import hmac
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ADMIN_PW = "soak-admin-pw-123"
USER_PW = "soak-user-pw-456"
STORM_SECONDS = 18.0
N_WORKERS = 8
WEBHOOK_SECRET = "soak-webhook-secret"
PLUGINS = ["schd", "mwp", "hookp", "provp", "toolp"]

# --- 插件源码（SINK_PORT 由 main 写入时替换） ---

SRC_SCHD = """
from fastapi import APIRouter

PLUGIN_META = {"id": "schd", "name": "SCHD", "version": "1.0.0"}

def register(api):
    # tick 落插件 data_dir 文件而非模块级 dict——disable->enable 会重执行模块（dict 归零），
    # 文件跨模块世代共享，冻结/泄漏检测才有效（泄漏的旧任务会继续写旧 dict，路由不可见）
    path = api.data_dir / "ticks.log"

    def _tick():
        with open(path, "a", encoding="utf-8") as f:
            f.write("x")

    router = APIRouter()

    @router.get("/ticks")
    def ticks():
        try:
            return {"n": path.stat().st_size}
        except OSError:
            return {"n": 0}

    api.routes.mount(router, auth=False)
    api.scheduler.every(0.3, _tick)
"""

SRC_MWP = """
PLUGIN_META = {"id": "mwp", "name": "MW", "version": "1.0.0"}

def register(api):
    async def add_header(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Soak-Mw"] = "1"
        return resp

    api.middleware.register(add_header)
"""

SRC_HOOKP = """
from fastapi import APIRouter

PLUGIN_META = {"id": "hookp", "name": "HOOK", "version": "1.0.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        api.events.emit("hookp.ping", {"t": 1})
        return {"ok": True}

    api.routes.mount(router, auth=False)
    api.webhooks.register("http://127.0.0.1:{SINK_PORT}/hook", events=["hookp.ping"],
                          secret="{WEBHOOK_SECRET}")
"""

SRC_PROVP = """
from fastapi import APIRouter, Depends

PLUGIN_META = {"id": "provp", "name": "PROV", "version": "1.0.0"}

async def _handler(system, user, *, model, max_tokens, api_key="", base_url=""):
    return {"content": "ok", "model": "soak-model", "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5}}

def register(api):
    router = APIRouter()

    @router.get("/complete")
    async def complete(user=Depends(api.deps.get_current_user)):
        out = await api.llm.complete(user.id, "hi", provider_kind="soakprov", max_tokens=16)
        return {"content": out["content"], "model": out["model"],
                "in": out["usage"]["input_tokens"], "out": out["usage"]["output_tokens"]}

    api.routes.mount(router)
    api.providers.register("soakprov", _handler, default_model="soak-model")
"""

SRC_TOOLP = """
from fastapi import APIRouter, Depends

PLUGIN_META = {"id": "toolp", "name": "TOOL", "version": "1.0.0"}

async def _echo(inp, ctx):
    return {"echo": inp.args.get("x")}

def register(api):
    router = APIRouter()

    @router.get("/call")
    async def call_tool(user=Depends(api.deps.get_current_user)):
        return await api.tools.call("soak_echo", {"x": 7}, user_id=user.id)

    api.routes.mount(router)
    api.tools.register_python("soak_echo", _echo, description="soak echo")
"""


def log(msg: str) -> None:
    print(f"[soak] {msg}", flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def spawn(port: int, env: dict, log_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--app-dir",
            str(ROOT),
            "--timeout-graceful-shutdown",
            "20",
        ],
        env=env,
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
    )


def _dump_tail(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    except OSError:
        return f"(日志不可读 {log_path})"


def wait_health(base: str, proc: subprocess.Popen, log_path: Path, timeout: float = 240) -> None:
    dl = time.monotonic() + timeout
    while time.monotonic() < dl:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出 rc={proc.returncode}\n{_dump_tail(log_path)}")
        try:
            if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"health 超时。子进程存活={proc.poll() is None}\n{_dump_tail(log_path)}")


def _start_sink(port: int) -> tuple[ThreadingHTTPServer, dict]:
    """webhook 投递接收器：计数 + HMAC-SHA256 验签（计数加锁——ThreadingHTTPServer
    并发 POST 与主线程读数的 += 竞态会让守恒断言假失败）。"""
    state = {"delivered": 0, "sig_ok": 0, "sig_bad": 0}
    lock = threading.Lock()

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            sig = self.headers.get("X-Webhook-Signature") or ""
            expect = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
            with lock:
                state["delivered"] += 1
                if sig and hmac.compare_digest(sig, expect):
                    state["sig_ok"] += 1
                else:
                    state["sig_bad"] += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args) -> None:  # 静默访问日志
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True, name="webhook-sink").start()
    return srv, state


def _plugin_states(base: str, headers: dict) -> dict:
    r = httpx.get(f"{base}/api/plugins", headers=headers, timeout=10)
    assert r.status_code == 200, r.text
    return {p["id"]: p["state"] for p in r.json()["plugins"]}


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_soak385_")
    sink_port = free_port()
    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(Path(tmp) / "soak.db"),
            "DATA_DIR": str(Path(tmp) / "data"),
            "ADMIN_PASSWORD": ADMIN_PW,
            "ADMIN_USERNAME": "soakadmin",
            "PLUGINS_DIR": str(Path(tmp) / "plugins"),
            "SKILLS_DIR": str(Path(tmp) / "skills"),
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
        }
    )
    pdir = Path(env["PLUGINS_DIR"])
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "schd.py").write_text(SRC_SCHD, encoding="utf-8")
    (pdir / "mwp.py").write_text(SRC_MWP, encoding="utf-8")
    (pdir / "hookp.py").write_text(
        SRC_HOOKP.replace("{SINK_PORT}", str(sink_port)).replace("{WEBHOOK_SECRET}", WEBHOOK_SECRET), encoding="utf-8"
    )
    (pdir / "provp.py").write_text(SRC_PROVP, encoding="utf-8")
    (pdir / "toolp.py").write_text(SRC_TOOLP, encoding="utf-8")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    log(f"启动 uvicorn :{port}（5 插件：{PLUGINS}；webhook sink :{sink_port}）")
    t0 = time.monotonic()
    proc = spawn(port, env, log_file)
    sink, sink_state = _start_sink(sink_port)
    sse_state = {"pings": 0, "eof": False}
    sk: socket.socket | None = None
    try:
        wait_health(base, proc, log_file)
        log(f"健康检查通过（+{time.monotonic() - t0:.1f}s）")

        tok_admin = httpx.post(
            f"{base}/api/auth/login", json={"username": "soakadmin", "password": ADMIN_PW}, timeout=10
        ).json()["token"]
        headers_admin = {"Authorization": f"Bearer {tok_admin}"}
        r = httpx.post(
            f"{base}/api/users",
            json={"username": "storm0", "password": USER_PW, "name": "ST"},
            headers=headers_admin,
            timeout=10,
        )
        assert r.status_code in (200, 201), r.text
        tok_user = httpx.post(
            f"{base}/api/auth/login", json={"username": "storm0", "password": USER_PW}, timeout=10
        ).json()["token"]
        headers_user = {"Authorization": f"Bearer {tok_user}"}

        # 启动即全 loaded（调度器在 lifespan 循环内注册成功——S1 启动侧）
        states = _plugin_states(base, headers_admin)
        assert all(states.get(p) == "loaded" for p in PLUGINS), states
        n0 = httpx.get(f"{base}/api/plugins/schd/ticks", timeout=10).json()["n"]
        time.sleep(1.0)
        n1 = httpx.get(f"{base}/api/plugins/schd/ticks", timeout=10).json()["n"]
        assert n1 > n0, "调度任务未运行"
        assert httpx.get(f"{base}/api/health", timeout=10).headers.get("X-Soak-Mw") == "1"
        log(f"启动态 ✓（ticks {n0}→{n1}，中间件头在）")

        # SSE 事件流常开（风暴与热管理全程心跳不断——插件热操作不扰动全局流）
        sk = socket.create_connection(("127.0.0.1", port), timeout=10)
        sk.sendall(
            (
                f"GET /api/events/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {tok_user}\r\nAccept: text/event-stream\r\n\r\n"
            ).encode()
        )
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            hdr += sk.recv(4096)
        assert b"200" in hdr.split(b"\r\n")[0], hdr[:120]

        def _sse_reader() -> None:
            try:
                sk.settimeout(60)  # type: ignore[union-attr]
                while True:
                    data = sk.recv(4096)  # type: ignore[union-attr]
                    if not data or b"0\r\n\r\n" in data:
                        sse_state["eof"] = True
                        return
                    sse_state["pings"] += data.count(b": ping")
            except OSError:
                sse_state["eof"] = True

        threading.Thread(target=_sse_reader, daemon=True, name="sse-reader").start()

        # --- 风暴 × 热管理交错 ---
        import asyncio
        import random

        stats = {"total": 0, "s5xx": 0, "codes": {}, "completes": 0, "calls_ok": 0, "mw_on": 0, "mw_off": 0}
        stop_at = time.monotonic() + STORM_SECONDS

        async def run() -> None:
            async with httpx.AsyncClient(base_url=base, timeout=15) as ac:

                async def worker(idx: int) -> None:
                    h = headers_user if idx % 2 else {}
                    while time.monotonic() < stop_at:
                        op = random.choice(["ticks", "ping", "complete", "call", "health"])
                        try:
                            if op == "ticks":
                                resp = await ac.get("/api/plugins/schd/ticks", headers=h)
                            elif op == "ping":
                                resp = await ac.get("/api/plugins/hookp/ping", headers=h)
                            elif op == "complete":
                                resp = await ac.get("/api/plugins/provp/complete", headers=h)
                                if resp.status_code == 200:
                                    stats["completes"] += 1
                            elif op == "call":
                                resp = await ac.get("/api/plugins/toolp/call", headers=h)
                                if resp.status_code == 200 and resp.json().get("status") == "ok":
                                    stats["calls_ok"] += 1
                            else:
                                resp = await ac.get("/api/health")
                                if resp.status_code == 200:
                                    if resp.headers.get("X-Soak-Mw") == "1":
                                        stats["mw_on"] += 1
                                    else:
                                        stats["mw_off"] += 1
                        except httpx.HTTPError:
                            stats["codes"]["net_err"] = stats["codes"].get("net_err", 0) + 1
                            continue
                        stats["total"] += 1
                        stats["codes"][resp.status_code] = stats["codes"].get(resp.status_code, 0) + 1
                        if resp.status_code >= 500:
                            stats["s5xx"] += 1

                async def manage(op: str, pid: str) -> None:
                    r = await ac.post(f"/api/plugins/{pid}/{op}", headers=headers_admin)
                    assert r.status_code == 200, f"{op} {pid}: {r.status_code} {r.text}"

                async def states_async() -> dict:
                    r = await ac.get("/api/plugins", headers=headers_admin)
                    return {p["id"]: p["state"] for p in r.json()["plugins"]}

                workers = [asyncio.create_task(worker(i)) for i in range(N_WORKERS)]
                await asyncio.sleep(1.0)

                # 交错 1：全部 reload（S1 复验——修复前 schd 此处必 failed）
                for p in PLUGINS:
                    await manage("reload", p)
                st = await states_async()
                assert all(st.get(p) == "loaded" for p in PLUGINS), st
                await asyncio.sleep(2.0)

                # 交错 2：全部 disable（S2 前半——任务应随卸载冻结；路由 404）
                for p in PLUGINS:
                    await manage("disable", p)
                st = await states_async()
                assert all(st.get(p) == "disabled" for p in PLUGINS), st
                for p in ("schd", "hookp"):
                    r = await ac.get(f"/api/plugins/{p}/ticks" if p == "schd" else f"/api/plugins/{p}/ping")
                    assert r.status_code == 404, (p, r.status_code)
                await asyncio.sleep(2.0)

                # 交错 3：全部 enable（S1 复验启用路径）
                for p in PLUGINS:
                    await manage("enable", p)
                st = await states_async()
                assert all(st.get(p) == "loaded" for p in PLUGINS), st

                await asyncio.wait_for(asyncio.gather(*workers), timeout=STORM_SECONDS + 40)

        asyncio.run(run())
        log(
            f"风暴完成：{stats['total']} 请求，500={stats['codes'].get(500, 0)}，"
            f"503={stats['codes'].get(503, 0)}，码分布={stats['codes']}，"
            f"complete 200={stats['completes']}，tool ok={stats['calls_ok']}，"
            f"中间件头 on/off={stats['mw_on']}/{stats['mw_off']}"
        )
        # 500 零容忍（未捕获异常=缺陷）；503 有界豁免——在途请求挂起在依赖解析 await
        # 上、恢复时 Provider 已随 unload 注销 -> no_llm_provider 信封（设计内优雅降级，
        # 可重试；与 429 频控同类）。未知工具为 404 信封（iter 385 修复，此前 500）
        assert stats["codes"].get(500, 0) == 0, "热管理交错风暴出现 500（未捕获异常）"
        assert stats["codes"].get(503, 0) <= 5, f"503 超出降级豁免界: {stats['codes']}"
        assert stats["mw_on"] > 0 and stats["mw_off"] > 0, "中间件热挂/摘未在风暴中生效"
        assert stats["completes"] > 0 and stats["calls_ok"] > 0

        # SSE 仍活着（热管理全程未扰动全局流）——轮询等首个心跳（流年龄可能恰在
        # 15s 心跳周期边界，立即断言会假失败）
        assert not sse_state["eof"], "SSE 事件流在插件热管理期间被意外断开"
        dl = time.monotonic() + 20.0
        while time.monotonic() < dl and sse_state["pings"] < 1:
            time.sleep(0.3)
            assert not sse_state["eof"], "等待心跳期间流被断开"
        assert sse_state["pings"] >= 1, f"心跳不足: {sse_state}"
        log(f"SSE 全程存活 ✓（心跳 {sse_state['pings']} 次）")

        # 稳定期 webhook 守恒：enable 后连发 10 ping，投递精确 +10 且全部验签通过
        base_delivered = sink_state["delivered"]
        with httpx.Client(base_url=base, timeout=10) as c:
            for _ in range(10):
                r = c.get("/api/plugins/hookp/ping")
                assert r.status_code == 200, r.text
        dl = time.monotonic() + 15
        while time.monotonic() < dl and sink_state["delivered"] < base_delivered + 10:
            time.sleep(0.2)
        assert sink_state["delivered"] == base_delivered + 10, (
            f"webhook 稳定期丢投递：{base_delivered} -> {sink_state['delivered']}"
        )
        assert sink_state["sig_bad"] == 0, sink_state
        log(f"webhook 守恒 ✓（稳定期 10/10 投递，验签 {sink_state['sig_ok']} 全对，风暴期累计 {base_delivered}）")

        # 稳定期计量探针：3 次 complete（计入守恒基数）
        with httpx.Client(base_url=base, timeout=10) as c:
            for _ in range(3):
                r = c.get("/api/plugins/provp/complete", headers=headers_user)
                assert r.status_code == 200, r.text
                stats["completes"] += 1

        # S2 终态复验：disable 期间任务冻结（零泄漏）。路由 404 不可读计数——tick 文件在
        # DATA_DIR/plugins/schd/ticks.log，进程外直读（跨模块世代共享：泄漏的旧任务无论
        # 持有哪一代模块闭包都写同一文件）。100ms 粒度采样：末段必须零增长。
        # 注：所有窗口断言按**实测墙钟**换算周期数（0.3s/周期）——重载机器上 time.sleep
        # 实际耗时可显著超时（时间戳诊断实证服务端节奏恒 0.31s 单任务，固定秒数断言
        # 会把宿主机 sleep 超时误判为服务端多跑）
        ticks_file = Path(env["DATA_DIR"]) / "plugins" / "schd" / "ticks.log"

        def fsize() -> int:
            try:
                return ticks_file.stat().st_size
            except OSError:
                return 0

        t_before = time.monotonic()
        a0 = fsize()
        time.sleep(0.9)
        a1 = fsize()
        elapsed_loaded = time.monotonic() - t_before
        assert a1 > a0, "loaded 态调度任务未运行"
        assert a1 - a0 <= elapsed_loaded / 0.3 + 1, (a1 - a0, elapsed_loaded)

        r = httpx.post(f"{base}/api/plugins/schd/disable", headers=headers_admin, timeout=10)
        assert r.status_code == 200, r.text
        t_frozen = time.monotonic()
        samples = []
        for _ in range(30):  # 3s @100ms
            time.sleep(0.1)
            samples.append(fsize())
        log(f"S2 冻结窗口采样（100ms×30）：{samples}")
        # 取消语义：disable 返回即已同步 cancel（端点在循环线程）。允许的早期增量 =
        # a1 读数与取消生效之间的在途回合（按实测墙钟折算周期数）+ 在途 to_thread
        # straggler +1 余量；其后 2.5s+ 必须完全冻结（泄漏的旧任务会持续写同一文件）
        elapsed_early = time.monotonic() - t_frozen - 2.5
        early_allowed = int(max(elapsed_early, 0) / 0.3) + 2
        early = max(samples[:5]) - a1
        assert early <= early_allowed, f"disable 后早期增量 +{early} 超界 {early_allowed}——取消未生效"
        late = samples[-1] - max(samples[:5])
        assert late == 0, f"冻结窗口后段仍在 tick（+{late}）——旧任务泄漏"
        r = httpx.post(f"{base}/api/plugins/schd/enable", headers=headers_admin, timeout=10)
        assert r.status_code == 200, r.text
        dl = time.monotonic() + 3.0
        while time.monotonic() < dl and fsize() <= samples[-1]:
            time.sleep(0.1)
        assert fsize() > samples[-1], "enable 后调度任务未恢复"
        log("S2 任务冻结/恢复 ✓（冻结末段零增长，恢复后递增）")

        # 日志无未预期异常栈（插件热管理不应产生 traceback）
        tail = log_file.read_text(encoding="utf-8", errors="replace")
        assert "Traceback (most recent call last)" not in tail, _dump_tail(log_file)

        # 优雅关停 + DB 对账
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        try:
            proc.wait(timeout=30)
            log("优雅关停 ✓")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("关停超时")

        con = sqlite3.connect(env["DB_PATH"])
        try:
            rows, tin, tout = con.execute(
                "SELECT COUNT(*), SUM(tokens_in), SUM(tokens_out) FROM api_usage WHERE model='soak-model'"
            ).fetchone()
            log(
                f"计量守恒：complete 200 共 {stats['completes']} 次；api_usage soak-model 行={rows} tokens {tin}/{tout}"
            )
            assert rows == stats["completes"], f"计量行数不守恒: {rows} != {stats['completes']}"
            assert tin == 10 * rows and tout == 5 * rows, (tin, tout, rows)
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
        log("soak_plugin_hotreload 全部通过 ✓")
    finally:
        if sk is not None:
            try:
                sk.close()
            except OSError:
                pass
        sink.shutdown()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
