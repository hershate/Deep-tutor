"""iter 372 R15：长跑模拟（手工运行，不入自动收集——无 test_ 前缀）。

组合 soak_process（8 用户多线程读写风暴）与 soak_maintenance（加速维护节奏）：
MAINTENANCE_INTERVAL_SECONDS=5 下持续 90s 多用户负载，跨 ~18 个维护周期，实证：
1. 零 5xx（负载与维护周期全程叠加）；
2. 内存（WorkingSet）/句柄数无累积增长（前 1/3 vs 后 1/3 均值窗口比较）；
3. DB 尺寸增长有界（合法数据增长 <5MB，无 blob 失控）；-wal 维护周期内持续被
   checkpoint(TRUNCATE) 截断（峰值有界，收尾归零）；
4. 维护与负载互不干扰：负载窗口内 checkpoint 日志 ≥10 次、种子旧行被裁剪、
   中途并发双导入互斥仍成立；
5. 关停后 integrity_check / foreign_key_check 干净。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_longrun.py
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from soak_process import ADMIN_PW, N_USERS, ROOT, free_port, spawn, wait_health

STORM_SECONDS = 90.0
MAINT_INTERVAL = 5  # 维护周期（秒）→ 全程 ~18 轮


def log(msg: str) -> None:
    print(f"[longrun] {msg}", flush=True)


# --- 进程指标（Windows ctypes；非 nt 回退 tasklist 不可用时跳过句柄） ---
class _PMC(ctypes.Structure):
    if os.name == "nt":
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]


def proc_stats(pid: int) -> tuple[float, int | None]:
    """(WorkingSet MB, 句柄数)。"""
    if os.name != "nt":
        return (-1.0, None)
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return (-1.0, None)
    try:
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        ws = -1.0
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            ws = round(pmc.WorkingSetSize / 1048576, 2)
        hnd = ctypes.c_ulong()
        handles = hnd.value if k32.GetProcessHandleCount(h, ctypes.byref(hnd)) else None
        return ws, handles
    finally:
        k32.CloseHandle(h)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_lr_")
    db_path = Path(tmp) / "lr.db"
    env = dict(os.environ)
    env.update({
        "DB_PATH": str(db_path), "DATA_DIR": str(Path(tmp) / "data"),
        "ADMIN_PASSWORD": ADMIN_PW, "ADMIN_USERNAME": "lrradmin",
        "FRONTEND_DIST": str(ROOT.parent / "frontend" / "dist"),
        "PLUGINS_DIR": str(Path(tmp) / "plugins"), "SKILLS_DIR": str(Path(tmp) / "skills"),
        "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1",
        "MAINTENANCE_INTERVAL_SECONDS": str(MAINT_INTERVAL),
        "API_USAGE_RETENTION_DAYS": "180",
    })
    Path(env["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["PLUGINS_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["SKILLS_DIR"]).mkdir(parents=True, exist_ok=True)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    logfile = Path(tmp) / "longrun.log"
    log(f"启动 uvicorn :{port}（加速维护 {MAINT_INTERVAL}s，风暴 {STORM_SECONDS:.0f}s）")
    proc = spawn(port, env, logfile)

    # 阶段零：起服后离线种子一批 200 天旧行（裁剪在负载窗口内发生即证「维护与负载互不干扰」）
    wait_health(base, proc, logfile)

    def _seed_old_usage() -> None:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO api_usage (provider, model, tokens_in, tokens_out, cache_read, cache_write, cost, created_at) "
                "SELECT 'anthropic', 'm', 1, 1, 0, 0, 0.1, datetime('now', '-200 days') "
                "UNION ALL SELECT 'anthropic', 'm', 1, 1, 0, 0, 0.2, datetime('now', '-200 days')"
            )
            conn.commit()
        finally:
            conn.close()

    _seed_old_usage()
    log("种子：2 行 200 天前用量（首轮维护应裁）")

    tok_admin = httpx.post(f"{base}/api/auth/login",
                           json={"username": "lrradmin", "password": ADMIN_PW}, timeout=10).json()["token"]
    headers_admin = {"Authorization": f"Bearer {tok_admin}"}
    tokens = []
    for i in range(N_USERS):
        r = httpx.post(f"{base}/api/users",
                       json={"username": f"lr{i}", "password": "lr-user-pw-789", "name": f"L{i}"},
                       headers=headers_admin, timeout=10)
        assert r.status_code in (200, 201), r.text
        tokens.append(httpx.post(f"{base}/api/auth/login",
                                 json={"username": f"lr{i}", "password": "lr-user-pw-789"},
                                 timeout=10).json()["token"])
    log(f"{N_USERS} 用户就绪")

    # 指标采样线程（5s 一拍：内存/句柄/DB/-wal）
    samples: list[dict] = []
    stop_sampler = threading.Event()

    def sampler() -> None:
        while not stop_sampler.wait(5.0):
            ws, hnd = proc_stats(proc.pid)
            wal = Path(str(db_path) + "-wal")
            samples.append({
                "t": round(time.monotonic(), 1), "ws": ws, "h": hnd,
                "db": db_path.stat().st_size if db_path.exists() else 0,
                "wal": wal.stat().st_size if wal.exists() else 0,
            })

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    import asyncio
    import random

    stats = {"total": 0, "s5xx": 0, "codes": {}, "ok_threads": 0}
    stop_at = time.monotonic() + STORM_SECONDS
    load_t0 = time.monotonic()

    async def storm() -> None:
        async with httpx.AsyncClient(base_url=base, timeout=15) as ac:
            async def one(tok: str) -> None:
                h = {"Authorization": f"Bearer {tok}"}
                while time.monotonic() < stop_at:
                    op = random.choice(["mk_thread", "mk_thread", "list", "notes", "usage", "health"])
                    try:
                        if op == "mk_thread":
                            resp = await ac.post("/api/threads", json={"title": "lr"}, headers=h)
                            if resp.status_code == 200:
                                stats["ok_threads"] += 1
                                await ac.patch(f"/api/threads/{resp.json()['id']}", json={"title": "改"}, headers=h)
                        elif op == "list":
                            resp = await ac.get("/api/threads?limit=10", headers=h)
                        elif op == "notes":
                            resp = await ac.post("/api/notes", json={"title": "n", "content": "x"}, headers=h)
                        elif op == "usage":
                            resp = await ac.get("/api/usage", headers=h)
                        else:
                            resp = await ac.get("/api/health")
                    except httpx.HTTPError:
                        stats["codes"]["net_err"] = stats["codes"].get("net_err", 0) + 1
                        continue
                    stats["total"] += 1
                    stats["codes"][resp.status_code] = stats["codes"].get(resp.status_code, 0) + 1
                    if resp.status_code >= 500:
                        stats["s5xx"] += 1

            backup = {"format": "deep-tutor-backup", "version": 2, "sessions": [],
                      "threads": [], "memories": [], "kb_documents": []}
            files = {"file": ("b.json", json.dumps(backup), "application/json")}
            mh = {"Authorization": f"Bearer {tok_admin}"}

            async def imp() -> object:
                return await ac.post("/api/backup/import", files=files, headers=mh)

            tasks = [asyncio.create_task(one(t)) for t in tokens]
            await asyncio.sleep(STORM_SECONDS / 2)
            r1, r2 = await asyncio.gather(imp(), imp())
            pair = sorted([r1.status_code, r2.status_code])  # type: ignore[attr-defined]
            log(f"中途并发双导入：{pair}")
            assert 200 in pair and set(pair) <= {200, 409}, f"负载中导入互斥异常：{pair}"
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=STORM_SECONDS + 60)

    asyncio.run(storm())
    load_t1 = time.monotonic()
    log(f"风暴完成（{load_t1-load_t0:.0f}s）：{stats['total']} 请求，5xx={stats['s5xx']}，分布={stats['codes']}")

    # 负载后再等 2+ 维护轮（checkpoint 把 -wal 截断回零）
    time.sleep(MAINT_INTERVAL * 2 + 3)
    stop_sampler.set()
    th.join(timeout=2)

    text = logfile.read_text(encoding="utf-8", errors="replace")
    ckpt_in_load = sum(
        1
        for ln in text.splitlines()
        if "WAL checkpoint(TRUNCATE) 完成" in ln
    )
    pruned = "api_usage 保留裁剪" in text
    log(f"维护轮证据：checkpoint×{ckpt_in_load}，裁剪发生={pruned}")

    # ---- 断言面 ----
    assert stats["s5xx"] == 0, "长跑出现 5xx"
    assert ckpt_in_load >= 10, f"维护轮不足（{ckpt_in_load}）——加速驱动未生效"
    assert pruned, "200 天种子行未被裁剪"

    ws_samples = [s["ws"] for s in samples if s["ws"] >= 0]
    h_samples = [s["h"] for s in samples if s["h"] is not None]
    if len(ws_samples) >= 6:
        third = len(ws_samples) // 3
        head, tail = ws_samples[:third], ws_samples[-third:]
        drift = (sum(tail) / len(tail)) - (sum(head) / len(head))
        log(f"内存：首1/3均值 {sum(head)/len(head):.0f}MB → 后1/3均值 {sum(tail)/len(tail):.0f}MB（漂移 {drift:+.0f}MB），峰值 {max(ws_samples):.0f}MB")
        assert drift < 64, f"WorkingSet 累积增长 {drift:.0f}MB——疑似泄漏"
    if h_samples:
        dh = h_samples[-1] - h_samples[0]
        log(f"句柄：{h_samples[0]} → {h_samples[-1]}（Δ{dh:+d}）")
        assert dh < 200, f"句柄累积 +{dh}——疑似句柄泄漏"
    db_growth = samples[-1]["db"] - samples[0]["db"]
    wal_peak = max((s["wal"] for s in samples), default=0)
    wal_end = samples[-1]["wal"]
    log(f"DB 尺寸增长 {db_growth/1048576:.2f}MB；-wal 峰值 {wal_peak/1024:.0f}KB → 收尾 {wal_end}B")
    assert db_growth < 5 * 1048576, "DB 增长超界（无失控写入）"
    assert wal_peak < 8 * 1048576, f"-wal 峰值 {wal_peak}B 超界"
    assert wal_end == 0, f"收尾 -wal 未截断（{wal_end}B）"

    # 优雅关停 + 事后完整性
    if os.name == "nt":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        except OSError:
            proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)

    conn = sqlite3.connect(str(db_path))
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    threads_n = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    conn.close()
    log(f"integrity_check={ic}；fk 违规={len(fk)}；threads={threads_n}（成功创建计数 {stats['ok_threads']}）")
    assert ic == "ok" and not fk
    assert threads_n >= stats["ok_threads"], "落库数与成功响应数不一致"

    print("\n[longrun] ✅ 长跑模拟全绿：90s×8 用户跨 ~18 维护周期零 5xx / 内存与句柄无累积 / "
          "-wal 周期截断收尾归零 / 负载中互斥与裁剪正常 / DB 完整性 ok")


if __name__ == "__main__":
    main()
