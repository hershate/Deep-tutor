"""长时间低频真实进程 soak（iter 389 R32，手工运行不自动收集）。

补齐 R29 泄漏修复在**时间维度**（而非热重载代数维度）的证据面：低用户频度 ×
分钟级以上时长下监控内存/句柄/asyncio 任务/线程/DB 行数/WAL 尺寸趋势，以及
SSE 长连接心跳存活与重连质量。两相设计分离两类增长：

- 流量相（traffic）：3 用户低频读 + 低频笔记 CRUD 写 —— 捕获「随请求增长」类
- 空闲相（idle）：零流量、仅 SSE 常开 + 采样 —— 捕获「随时间增长」类（真时间维度）

维护环以加速节奏运转（MAINTENANCE_INTERVAL_SECONDS，默认 120s），让 checkpoint/
孤儿扫描/保留裁剪在 soak 窗口内反复执行——时间维度的自愈面也在证据内。

用法（tests/ 目录）：
    python soak_overnight.py                     # 默认 40 分钟（32 流量 + 8 空闲）
    SOAK_MINUTES=480 python soak_overnight.py    # 过夜档（8 小时）
旋钮：SOAK_MINUTES / SOAK_IDLE_MINUTES / SOAK_TRAFFIC_INTERVAL_S / SOAK_WRITE_EVERY /
SOAK_SSE_CHURN_S / SOAK_SAMPLE_S / SOAK_MAINT_INTERVAL_S / SOAK_RSS_MAX_MB

断言（全部针对真实 uvicorn 进程）：
- 零 5xx；SSE 常开双流全程心跳不断（最大间隙 ≤45s）、churn 流 100% 重连成功
- RSS：流量相漂移 ≤ RSS_MAX_MB（默认 48，含 SQLite 页缓存预热）；空闲相斜率 ≤12MB
- 句柄：总漂移 ≤20；空闲相 ≤6。模块数恒稳；线程空闲相漂移 ≤2；任务数空闲相漂移 ≤3
- DB：api_usage/tool_calls/messages/runs/memories/sessions 全程零增长（无 LLM/对话
  流量时这些表不得自增）；notes 收尾净零（长驻笔记删除 + CRUD 平衡）；空闲相全表零增量
- WAL：收尾 ≤4MB 且末段无单调增长（checkpoint 生效）
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用 R28 soak 的进程引导/日志工具（spawn/wait_health/free_port/_dump_tail）
spec = importlib.util.spec_from_file_location("soak385", ROOT / "tests" / "soak_plugin_hotreload.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)

ADMIN_PW = "SoakOvernight!2026"
USER_PW = "NightUser!2026"

SOAK_MINUTES = float(os.environ.get("SOAK_MINUTES", "40"))
IDLE_MINUTES = float(os.environ.get("SOAK_IDLE_MINUTES", "8"))
TRAFFIC_INTERVAL_S = float(os.environ.get("SOAK_TRAFFIC_INTERVAL_S", "3"))
WRITE_EVERY = int(os.environ.get("SOAK_WRITE_EVERY", "12"))  # 每 N 个读周期做一轮写
SSE_CHURN_S = float(os.environ.get("SOAK_SSE_CHURN_S", "300"))
SAMPLE_S = float(os.environ.get("SOAK_SAMPLE_S", "60"))
MAINT_INTERVAL_S = os.environ.get("SOAK_MAINT_INTERVAL_S", "120")
RSS_MAX_MB = float(os.environ.get("SOAK_RSS_MAX_MB", "48"))
IDLE_RSS_MAX_MB = 12.0
IDLE_HND_MAX = 6
TOTAL_HND_MAX = 20
HEARTBEAT_S = 15.0
MAX_PING_GAP_S = 45.0

USERS = ["night0", "night1", "night2"]

WATCH_TABLES = [
    "api_usage", "tool_calls", "messages", "runs", "memories",
    "sessions", "notes", "learning_logs", "threads",
]

# 自检插件：进程内 ctypes 读自身 RSS/句柄 + asyncio 任务/线程计数（async 路由在
# 事件循环上执行，all_tasks() 有效）；/gc 供强制回收对照。R29 patrol 同款骨架。
SRC_DIAG = '''
import asyncio
import ctypes
import ctypes.wintypes
import gc
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter

PLUGIN_META = {"id": "diagp", "name": "DIAG", "version": "1.0.0"}

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    _kernel32.GetCurrentProcess.argtypes = []
    _psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def register(api):
    router = APIRouter()

    @router.get("/metrics")
    async def metrics():
        # async 路由跑在事件循环上——asyncio.all_tasks() 即宿主循环的真实任务数
        out = {
            "plugin_modules": len([m for m in sys.modules if m.startswith("dt_plugin_")]),
            "modules_total": len(sys.modules),
            "os_threads": threading.active_count(),
            "tasks": len(asyncio.all_tasks()),
        }
        if sys.platform == "win32":
            h = _kernel32.GetCurrentProcess()
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                out["rss_bytes"] = pmc.WorkingSetSize
            cnt = ctypes.c_uint32(0)
            if _kernel32.GetProcessHandleCount(h, ctypes.byref(cnt)):
                out["handles"] = cnt.value
        return out

    @router.get("/gc")
    def run_gc():
        # 强制回收对照（诊断用）：回收前后句柄数一并返回
        before = None
        if sys.platform == "win32":
            h = _kernel32.GetCurrentProcess()
            cnt = ctypes.c_uint32(0)
            if _kernel32.GetProcessHandleCount(h, ctypes.byref(cnt)):
                before = cnt.value
        gc.collect()
        after = None
        if sys.platform == "win32":
            h = _kernel32.GetCurrentProcess()
            cnt = ctypes.c_uint32(0)
            if _kernel32.GetProcessHandleCount(h, ctypes.byref(cnt)):
                after = cnt.value
        return {"collected": True, "handles_before": before, "handles_after": after}

    api.routes.mount(router, auth=True)
'''


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sse_connect(port: int, token: str, timeout: float = 10.0) -> socket.socket:
    """建立一条已读完响应头的 SSE 原始连接（返回 socket；非 200 抛断言）。"""
    sk = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sk.sendall(
        (
            f"GET /api/events/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Authorization: Bearer {token}\r\nAccept: text/event-stream\r\n\r\n"
        ).encode()
    )
    sk.settimeout(timeout)
    hdr = b""
    while b"\r\n\r\n" not in hdr:
        chunk = sk.recv(4096)
        if not chunk:
            sk.close()
            raise AssertionError(f"SSE 连接建立即断（响应头不完整）: {hdr[:120]!r}")
        hdr += chunk
    first = hdr.split(b"\r\n")[0]
    assert b"200" in first, f"SSE 连接非 200: {first!r}"
    return sk


class SseWatch(threading.Thread):
    """常开 SSE 心跳看护线程：计数 ping、记录最大心跳间隙、断流标记 EOF。"""

    def __init__(self, port: int, token: str, name: str) -> None:
        super().__init__(daemon=True, name=f"sse-{name}")
        self.port, self.token, self.name = port, token, name
        self.pings = 0
        self.last_ping = None  # wall clock of last ping
        self.max_gap = 0.0
        self.eof = False
        self.started = False
        self._sk: socket.socket | None = None

    def run(self) -> None:
        try:
            self._sk = sse_connect(self.port, self.token)
            self.started = True
            self.last_ping = time.monotonic()
            self._sk.settimeout(60)
            while True:
                data = self._sk.recv(4096)
                if not data or b"0\r\n\r\n" in data:
                    self.eof = True
                    return
                n = data.count(b": ping")
                if n:
                    now = time.monotonic()
                    self.max_gap = max(self.max_gap, now - self.last_ping)
                    self.last_ping = now
                    self.pings += n
        except OSError:
            self.eof = True


def db_snapshot(db_path: str) -> dict:
    """进程外直读 SQLite（WAL 读者并发安全）：关注表行数 + 库/WAL/SHM 尺寸。"""
    out: dict[str, object] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        for t in WATCH_TABLES:
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = -1
    finally:
        con.close()
    p = Path(db_path)
    out["db_bytes"] = p.stat().st_size if p.exists() else -1
    wal = p.with_name(p.name + "-wal")
    out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
    return out


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_soak389_")
    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(Path(tmp) / "soak.db"),
            "DATA_DIR": str(Path(tmp) / "data"),
            "ADMIN_PASSWORD": ADMIN_PW,
            "ADMIN_USERNAME": "soakadmin",
            "PLUGINS_DIR": str(Path(tmp) / "plugins"),
            "SKILLS_DIR": str(Path(tmp) / "skills"),
            "MAINTENANCE_INTERVAL_SECONDS": MAINT_INTERVAL_S,
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
        }
    )
    pdir = Path(env["PLUGINS_DIR"])
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "diagp.py").write_text(SRC_DIAG, encoding="utf-8")

    port = soak.free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    total_s = SOAK_MINUTES * 60
    idle_s = IDLE_MINUTES * 60
    traffic_s = total_s - idle_s
    log(f"启动 uvicorn :{port}（{SOAK_MINUTES:.0f} 分钟 = 流量 {traffic_s/60:.0f} + 空闲 {idle_s/60:.0f}；"
        f"读间隔 {TRAFFIC_INTERVAL_S}s；churn {SSE_CHURN_S:.0f}s；维护 {MAINT_INTERVAL_S}s）")
    t0 = time.monotonic()
    proc = soak.spawn(port, env, log_file)
    samples: list[dict] = []
    stop_flag = threading.Event()
    stats = {"total": 0, "s5xx": 0, "codes": {}, "writes": 0, "churn_ok": 0, "churn_fail": 0}
    watches: list[SseWatch] = []
    try:
        soak.wait_health(base, proc, log_file)
        log(f"健康检查通过（+{time.monotonic() - t0:.1f}s）")

        tok_admin = httpx.post(
            f"{base}/api/auth/login", json={"username": "soakadmin", "password": ADMIN_PW}, timeout=10
        ).json()["token"]
        headers_admin = {"Authorization": f"Bearer {tok_admin}"}
        tokens = {}
        for u in USERS:
            r = httpx.post(
                f"{base}/api/users", json={"username": u, "password": USER_PW, "name": u},
                headers=headers_admin, timeout=10,
            )
            assert r.status_code in (200, 201), r.text
            tokens[u] = httpx.post(
                f"{base}/api/auth/login", json={"username": u, "password": USER_PW}, timeout=10
            ).json()["token"]

        # 全程 DB 基线（长驻笔记创建前——收尾删除后各表应回到此刻）
        pre_notes = db_snapshot(env["DB_PATH"])

        # 长驻笔记（每用户一篇，写周期内反复更新——行数恒定、updated_at 持续推进）
        long_notes: dict[str, dict] = {}
        for u in USERS:
            h = {"Authorization": f"Bearer {tokens[u]}"}
            r = httpx.post(f"{base}/api/notes", json={"title": f"soak-{u}", "content": "v0"}, headers=h, timeout=10)
            assert r.status_code == 201, r.text
            long_notes[u] = r.json()

        # 常开 SSE ×2（u1/u2）
        for u in USERS[:2]:
            w = SseWatch(port, tokens[u], u)
            w.start()
            watches.append(w)
        deadline_conn = time.monotonic() + 15
        while not all(w.started for w in watches) and time.monotonic() < deadline_conn:
            time.sleep(0.2)
        assert all(w.started for w in watches), "常开 SSE 建连失败"
        log("常开 SSE ×2 建连 ✓")

        # --- 流量线程：低频读 + 低频写 ---
        def traffic() -> None:
            reads = [
                ("GET", "/api/plans"), ("GET", "/api/settings"), ("GET", "/api/notes"),
                ("GET", "/api/usage"), ("GET", "/api/plans/today"), ("GET", "/api/health"),
            ]
            cycle = 0
            with httpx.Client(base_url=base, timeout=15) as c:
                while not stop_flag.is_set() and time.monotonic() - t0 < traffic_s:
                    u = USERS[cycle % len(USERS)]
                    h = {"Authorization": f"Bearer {tokens[u]}"}
                    method, path = reads[cycle % len(reads)]
                    try:
                        r = c.request(method, path, headers=h)
                        stats["codes"][r.status_code] = stats["codes"].get(r.status_code, 0) + 1
                        if r.status_code >= 500:
                            stats["s5xx"] += 1
                            log(f"5xx: {method} {path} -> {r.status_code} {r.text[:120]}")
                        stats["total"] += 1
                    except httpx.HTTPError as e:
                        stats["codes"]["net_err"] = stats["codes"].get("net_err", 0) + 1
                        log(f"网络错误: {method} {path}: {e}")
                    cycle += 1
                    if cycle % WRITE_EVERY == 0:
                        _write_cycle(c, u, h, long_notes[u], stats)
                    stop_flag.wait(TRAFFIC_INTERVAL_S)
            log(f"流量相结束（{stats['total']} 请求，写 {stats['writes']} 轮）")

        def _write_cycle(c: httpx.Client, u: str, h: dict, note: dict, st: dict) -> None:
            try:
                # 长驻笔记更新（expected_updated_at 链——顺带复验乐观并发基线在长跑中不出错）
                r = c.put(
                    f"/api/notes/{note['id']}",
                    json={"title": note["title"], "content": f"v{st['writes']}", "expected_updated_at": note["updated_at"]},
                    headers=h, timeout=15,
                )
                if r.status_code == 200:
                    note.update(r.json())
                else:
                    st["codes"][f"PUT_long_{r.status_code}"] = st["codes"].get(f"PUT_long_{r.status_code}", 0) + 1
                # 完整 CRUD churn（净零）
                r = c.post("/api/notes", json={"title": "churn", "content": "x"}, headers=h, timeout=15)
                assert r.status_code == 201, f"churn 建笔记失败: {r.text[:120]}"
                cn = r.json()
                c.put(f"/api/notes/{cn['id']}", json={"title": "churn2", "content": "y"}, headers=h, timeout=15)
                c.delete(f"/api/notes/{cn['id']}", headers=h, timeout=15)
                st["writes"] += 1
            except Exception as e:  # noqa: BLE001 - 记数不中断
                st["codes"]["write_err"] = st["codes"].get("write_err", 0) + 1
                log(f"写周期异常（{u}）: {e}")

        th_traffic = threading.Thread(target=traffic, daemon=True, name="traffic")
        th_traffic.start()

        # --- churn SSE：周期断开重连（u3）——重连质量面 ---
        def churn() -> None:
            while not stop_flag.is_set() and time.monotonic() - t0 < total_s:
                try:
                    sk = sse_connect(port, tokens[USERS[2]])
                    sk.settimeout(MAX_PING_GAP_S)
                    got = b""
                    deadline = time.monotonic() + MAX_PING_GAP_S
                    while b": ping" not in got and time.monotonic() < deadline:
                        got += sk.recv(4096)
                    ok = b": ping" in got
                    sk.close()
                    if ok:
                        stats["churn_ok"] += 1
                    else:
                        stats["churn_fail"] += 1
                        log("churn 重连后未在窗口内收到心跳")
                except Exception as e:  # noqa: BLE001
                    stats["churn_fail"] += 1
                    log(f"churn 重连失败: {e}")
                stop_flag.wait(SSE_CHURN_S)

        th_churn = threading.Thread(target=churn, daemon=True, name="sse-churn")
        th_churn.start()

        # --- 采样器：进程指标 + DB 快照（贯穿流量相与空闲相） ---
        def sample() -> None:
            while not stop_flag.is_set() and time.monotonic() - t0 < total_s:
                try:
                    m = httpx.get(f"{base}/api/plugins/diagp/metrics", headers=headers_admin, timeout=15).json()
                except Exception as e:  # noqa: BLE001
                    log(f"采样失败（metrics）: {e}")
                    m = {}
                s = {"t": round(time.monotonic() - t0, 1), "phase": "traffic" if time.monotonic() - t0 < traffic_s else "idle", **m}
                s.update(db_snapshot(env["DB_PATH"]))
                samples.append(s)
                alive = ", ".join(f"{w.name}={w.pings}p" for w in watches)
                log(f"sample #{len(samples)} [{s['phase']}] rss={s.get('rss_bytes', 0)/1e6:.1f}MB "
                    f"hnd={s.get('handles', '?')} tasks={s.get('tasks', '?')} os_threads={s.get('os_threads', '?')} "
                    f"notes={s.get('notes', '?')} wal={s.get('wal_bytes', 0)/1e6:.2f}MB | {alive}")
                stop_flag.wait(SAMPLE_S)

        th_sample = threading.Thread(target=sample, daemon=True, name="sampler")
        th_sample.start()

        # 等待全程结束
        while time.monotonic() - t0 < total_s:
            time.sleep(5)
            if proc.poll() is not None:
                raise RuntimeError(f"uvicorn 中途退出 rc={proc.returncode}\n{soak._dump_tail(log_file)}")
        stop_flag.set()
        th_traffic.join(timeout=30)
        th_churn.join(timeout=60)
        th_sample.join(timeout=30)
        time.sleep(2)  # 尾部请求落定

        # --- 收尾清理：删长驻笔记（notes 净零断言的前提） ---
        for u in USERS:
            h = {"Authorization": f"Bearer {tokens[u]}"}
            httpx.delete(f"{base}/api/notes/{long_notes[u]['id']}", headers=h, timeout=15)

        final = {"t": round(time.monotonic() - t0, 1), "phase": "final"}
        final.update(httpx.get(f"{base}/api/plugins/diagp/metrics", headers=headers_admin, timeout=15).json())
        final.update(db_snapshot(env["DB_PATH"]))
        samples.append(final)
        (Path(tmp) / "samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"采样完成（{len(samples)} 份，samples.json 已落盘）")

        # ===== 断言 =====
        tr = [s for s in samples if s["phase"] == "traffic"]
        idl = [s for s in samples if s["phase"] == "idle"]
        first, last = samples[0], samples[-1]
        problems: list[str] = []

        if stats["s5xx"]:
            problems.append(f"5xx×{stats['s5xx']}")
        # SSE 常开存活 + 心跳连续
        for w in watches:
            if w.eof:
                problems.append(f"常开 SSE {w.name} 中途断流")
            if w.max_gap > MAX_PING_GAP_S:
                problems.append(f"常开 SSE {w.name} 心跳最大间隙 {w.max_gap:.0f}s > {MAX_PING_GAP_S:.0f}s")
            expect = int(total_s / HEARTBEAT_S) - 2
            if w.pings < expect:
                problems.append(f"常开 SSE {w.name} pings={w.pings} < 预期下限 {expect}")
        if stats["churn_fail"]:
            problems.append(f"churn 重连失败×{stats['churn_fail']}")
        elif stats["churn_ok"] == 0:
            problems.append("churn 重连零样本（时长/churn 周期配置过短）")

        def drift(key: str, seq: list[dict]) -> float:
            vals = [s[key] for s in seq if isinstance(s.get(key), (int, float))]
            return (vals[-1] - vals[0]) if len(vals) >= 2 else 0.0

        rss_total = drift("rss_bytes", [first, *tr, *idl, last])
        if rss_total > RSS_MAX_MB * 1e6:
            problems.append(f"RSS 总漂移 {rss_total/1e6:.1f}MB > {RSS_MAX_MB:.0f}MB")
        if idl:
            rss_idle = drift("rss_bytes", idl)
            if rss_idle > IDLE_RSS_MAX_MB * 1e6:
                problems.append(f"RSS 空闲相漂移 {rss_idle/1e6:.1f}MB > {IDLE_RSS_MAX_MB:.0f}MB（纯时间增长嫌疑）")
            hnd_idle = drift("handles", idl)
            if hnd_idle > IDLE_HND_MAX:
                problems.append(f"句柄空闲相漂移 {hnd_idle:+.0f} > {IDLE_HND_MAX}")
            # 任务数瞬态波动 ±几（churn 连接/维护 to_thread/采样自身请求各占 1-2 个任务），
            # 单点差分误报——中位数斜率断言（前半 vs 后半），只抓持续增长
            def med(key: str, seq: list[dict]) -> float:
                vals = sorted(s[key] for s in seq if isinstance(s.get(key), (int, float)))
                return vals[len(vals) // 2] if vals else 0.0

            if len(idl) >= 4:
                half = len(idl) // 2
                tsk_slope = med("tasks", idl[half:]) - med("tasks", idl[:half])
                if tsk_slope > 4:
                    problems.append(f"asyncio 任务空闲相斜率 {tsk_slope:+.0f}（中位数，持续增长嫌疑）")
            thr_idle = drift("os_threads", idl)
            if abs(thr_idle) > 2:
                problems.append(f"线程空闲相漂移 {thr_idle:+.0f}")
            for t in WATCH_TABLES:
                d = drift(t, idl)
                if d != 0:
                    problems.append(f"空闲相表 {t} 增量 {d:+.0f}（零流量期不得有任何行增长）")
        hnd_total = drift("handles", [first, *tr, *idl, last])
        if hnd_total > TOTAL_HND_MAX:
            problems.append(f"句柄总漂移 {hnd_total:+.0f} > {TOTAL_HND_MAX}")
        if drift("plugin_modules", samples) != 0:
            problems.append("插件模块数发生增长")

        # DB：全程无 LLM/对话流量 -> 这些表必须零增长；notes 收尾净零
        for t in ["api_usage", "tool_calls", "messages", "runs", "memories", "sessions", "learning_logs", "threads"]:
            d = last.get(t, -1) - first.get(t, -1)
            if d != 0:
                problems.append(f"表 {t} 全程增量 {d:+.0f}（无对应流量）")
        notes_delta = last.get("notes", -1) - pre_notes.get("notes", -1)
        if notes_delta != 0:
            problems.append(f"notes 收尾净增量 {notes_delta:+.0f}（长驻已删 + CRUD 平衡应为 0）")

        wal_final = last.get("wal_bytes", 0)
        if wal_final > 4 * 1024 * 1024:
            problems.append(f"WAL 收尾 {wal_final/1e6:.2f}MB > 4MB")
        tail_wal = [s.get("wal_bytes", 0) for s in samples[-5:]]
        if len(tail_wal) == 5 and all(b > a for a, b in zip(tail_wal, tail_wal[1:])):
            problems.append(f"WAL 末 5 采样单调增长 {tail_wal}（checkpoint 未生效嫌疑）")

        log("=== 汇总 ===")
        log(f"请求 {stats['total']}（{stats['codes']}），写周期 {stats['writes']}")
        log(f"SSE 常开: {[ (w.name, w.pings, f'maxgap={w.max_gap:.0f}s') for w in watches ]}；churn ok/fail = {stats['churn_ok']}/{stats['churn_fail']}")
        log(f"RSS {first.get('rss_bytes', 0)/1e6:.1f} -> {last.get('rss_bytes', 0)/1e6:.1f} MB（总 {rss_total/1e6:+.1f}）；"
            f"句柄 {first.get('handles', '?')} -> {last.get('handles', '?')}（总 {hnd_total:+.0f}）")
        log(f"tasks {first.get('tasks', '?')} -> {last.get('tasks', '?')}；os_threads {first.get('os_threads', '?')} -> {last.get('os_threads', '?')}")
        log(f"DB: { {t: (first.get(t), last.get(t)) for t in ['notes', 'api_usage']} }；wal 收尾 {wal_final/1e6:.2f}MB")

        if problems:
            for p in problems:
                log(f"❌ {p}")
            raise SystemExit(1)
        log(f"✅ 全部断言通过（{total_s/60:.0f} 分钟时间维度 soak）")

    finally:
        stop_flag.set()
        if proc.poll() is None:
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        log(f"uvicorn 退出 rc={proc.returncode}；日志 {log_file}；样本 {tmp}\\samples.json")


if __name__ == "__main__":
    main()
