"""iter 372 R10：真实进程级 soak 审计（手工运行，不自动收集——文件名无 test_ 前缀）。

此前全部证据走 TestClient/ASGITransport（进程内、无真实 socket）。本脚本补最后一级证据：
    uvicorn 子进程 + 真实 TCP + 多用户混合负载 + 备份导入互斥（真并发下 409）
    + 崩溃模拟（硬杀）→ 重启恢复收敛 + 优雅关停 + 事后 SQLite integrity/foreign_key 检查。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_process.py
（使用临时 DATA_DIR/DB_PATH，不碰真实 data/；结束后清理）
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ADMIN_PW = "soak-admin-pw-123"
STORM_SECONDS = 12.0
N_USERS = 8


def log(msg: str) -> None:
    print(f"[soak] {msg}", flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def spawn(port: int, env: dict, log_path: Path) -> subprocess.Popen:
    # stdout 落日志文件而非 PIPE：PIPE 无人排水时子进程写满缓冲会阻塞；
    # 落文件还让失败时能倒出尾部诊断（wait_health 失败路径 dump）
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--app-dir", str(ROOT), "--timeout-graceful-shutdown", "20"],
        env=env, cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,
    )


def _dump_tail(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    except OSError:
        return f"(日志不可读 {log_path})"


def wait_health(base: str, proc: subprocess.Popen, log_path: Path, timeout: float = 240) -> None:
    # 冷启动实测可达 60s+（app 导入链含重量级依赖），240s 上限；每轮固定 sleep 防忙等
    dl = time.monotonic() + timeout
    while time.monotonic() < dl:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出 rc={proc.returncode}\n{_dump_tail(log_path)}")
        try:
            r = httpx.get(f"{base}/api/health", timeout=2)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"health 超时（{timeout}s）。子进程存活={proc.poll() is None}\n{_dump_tail(log_path)}")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_soak_")
    env = dict(os.environ)
    env.update({
        "DB_PATH": str(Path(tmp) / "soak.db"),
        "DATA_DIR": str(Path(tmp) / "data"),
        "ADMIN_PASSWORD": ADMIN_PW,
        "ADMIN_USERNAME": "soakadmin",
        "FRONTEND_DIST": str(ROOT.parent / "frontend" / "dist"),
        "PLUGINS_DIR": str(Path(tmp) / "plugins"),  # 空插件目录：soak 不引入插件变量
        "SKILLS_DIR": str(Path(tmp) / "skills"),
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
    })
    Path(env["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["PLUGINS_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["SKILLS_DIR"]).mkdir(parents=True, exist_ok=True)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    log(f"启动 uvicorn :{port}（临时库 {env['DB_PATH']}，日志 {log_file}）")
    t0 = time.monotonic()
    proc = spawn(port, env, log_file)
    wait_health(base, proc, log_file)
    log(f"健康检查通过（+{time.monotonic()-t0:.1f}s）")

    # SPA 托管 + 安全头（真实进程面）
    r = httpx.get(f"{base}/", timeout=5)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"], r.status_code
    for h in ("x-content-type-options", "x-frame-options", "referrer-policy"):
        assert r.headers.get(h), f"SPA 响应缺安全头 {h}"
    log("SPA 托管 + 安全响应头 ✓")

    # 管理员登录 + 建 8 用户 + 各自登录（9 次登录 < per-IP 60/min）
    tok_admin = httpx.post(f"{base}/api/auth/login",
                           json={"username": "soakadmin", "password": ADMIN_PW}, timeout=10
                           ).json()["token"]
    headers_admin = {"Authorization": f"Bearer {tok_admin}"}
    tokens = []
    for i in range(N_USERS):
        r = httpx.post(f"{base}/api/users",
                       json={"username": f"soak{i}", "password": "soak-user-pw-456", "name": f"S{i}"},
                       headers=headers_admin, timeout=10)
        assert r.status_code in (200, 201), r.text
        t = httpx.post(f"{base}/api/auth/login",
                       json={"username": f"soak{i}", "password": "soak-user-pw-456"}, timeout=10
                       ).json()["token"]
        tokens.append(t)
    log(f"{N_USERS} 用户就绪（真实 JWT）")

    import random

    stats = {"total": 0, "s5xx": 0, "codes": {}}
    stop_at = time.monotonic() + STORM_SECONDS

    async def storm():
        import asyncio

        async with httpx.AsyncClient(base_url=base, timeout=15) as ac:
            async def one(tok: str):
                h = {"Authorization": f"Bearer {tok}"}
                while time.monotonic() < stop_at:
                    op = random.choice(["mk_thread", "mk_thread", "list", "notes", "usage", "health"])
                    try:
                        if op == "mk_thread":
                            resp = await ac.post("/api/threads", json={"title": "soak"}, headers=h)
                            if resp.status_code == 200:
                                tid = resp.json()["id"]
                                resp = await ac.patch(f"/api/threads/{tid}", json={"title": "改"}, headers=h)
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

            # 备份导入互斥：风暴中并发两笔合法小导入 → 一笔 200 一笔 409（互斥生效实证）
            backup = {"format": "deep-tutor-backup", "version": 2, "sessions": [],
                      "threads": [], "memories": [], "kb_documents": []}
            files = {"file": ("b.json", json.dumps(backup), "application/json")}
            mh = {"Authorization": f"Bearer {tok_admin}"}

            async def imp():
                return await ac.post("/api/backup/import", files=files, headers=mh)

            tasks = [asyncio.create_task(one(t)) for t in tokens]
            await asyncio.sleep(1.0)
            r1, r2 = await asyncio.gather(imp(), imp())
            pair = sorted([r1.status_code, r2.status_code])
            log(f"并发双导入状态码：{pair}" + ("（409 = 互斥竞争路径实证）" if 409 in pair else ""))
            # 合法载荷必须至少一笔成功；互斥不变量 = 不出现并发双成功之外的异常
            # （[200,200] 理论可能：首笔先完成、次笔后到不重叠，同样不违反互斥）
            assert 200 in pair and set(pair) <= {200, 409}, f"导入互斥异常：{pair}"
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=STORM_SECONDS + 30)

    import asyncio

    asyncio.run(storm())
    log(f"风暴完成：{stats['total']} 请求，5xx={stats['s5xx']}，状态码分布={stats['codes']}")
    assert stats["s5xx"] == 0, "真实进程面出现 5xx"

    # 崩溃模拟：硬杀 → 同库重启 → 健康恢复
    proc.kill()
    proc.wait(timeout=10)
    log("硬杀（崩溃模拟）→ 重启")
    log_file2 = Path(tmp) / "uvicorn2.log"  # 独立日志（spawn 以 wb 打开会截断）
    proc2 = spawn(port, env, log_file2)
    wait_health(base, proc2, log_file2)
    r = httpx.get(f"{base}/api/threads?limit=1", headers={"Authorization": f"Bearer {tokens[0]}"}, timeout=10)
    assert r.status_code == 200
    log("重启后健康 + 认证读写正常（启动恢复收敛生效）")

    # 优雅关停：CTRL_BREAK（Windows 进程组）→ 有限时退出
    graceful = False
    if os.name == "nt":
        try:
            os.kill(proc2.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            proc2.wait(timeout=30)  # 有限时退出即视为优雅（uvicorn 处理 SIGBREAK 走 graceful 路径）
            graceful = True
            log(f"CTRL_BREAK 关停退出 rc={proc2.returncode}")
        except OSError as e:
            log(f"CTRL_BREAK 不可用（{e}），改硬杀收尾")
            proc2.kill()
            proc2.wait(timeout=10)
    else:
        proc2.terminate()
        proc2.wait(timeout=30)
        graceful = True
        log(f"SIGTERM 关停退出 rc={proc2.returncode}")

    # 事后 DB 完整性
    import sqlite3

    conn = sqlite3.connect(env["DB_PATH"])
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    users_n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    threads_n = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    conn.close()
    log(f"integrity_check={ic}；foreign_key 违规={len(fk)}；users={users_n} threads={threads_n}")
    assert ic == "ok" and not fk

    print("\n[soak] ✅ 全部通过：真实进程负载零 5xx / 双导入互斥 / 崩溃重启恢复 / "
          f"{'优雅关停' if graceful else '关停降级记录'} / DB 完整性 ok")


if __name__ == "__main__":
    main()
