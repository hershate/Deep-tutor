"""iter 383 R26：真实进程 soak——删除族×导入写入族交错 + R23-R25 改动路径复验（手工运行，不自动收集）。

审计面（此前证据均为进程内 TestClient）：
    S1 写入族×删除族风暴：8 用户混合读写**含删除**，期间并发导入实质载荷（threads×
       messages 使导入事务持写锁秒级）与导出——对偶 R23 的删除族竞态：导入写锁与
       删除/常规写交错下的稳定性（零 5xx、导入互斥不变量）。
    S2 事件流吊销断连（R24 修复的进程级复验）：真实 SSE 长连接建立后改密（吊销
       tokens_valid_after）→ 服务端须在 ≤2 心跳周期内主动断流（EOF），而非永续。
    S3 事后 SQLite integrity/foreign_key + 行数对账。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_restore_races.py
（临时 DATA_DIR/DB_PATH，不碰真实 data/；结束后清理）
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
STORM_SECONDS = 15.0
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


def _mk_import_payload(n_threads: int = 30, msgs_per: int = 60) -> dict:
    """v2 备份载荷：threads + runs/messages 索引互链——让导入事务有实质写量（持锁数百 ms+）。"""
    threads = []
    for t in range(n_threads):
        tid = t + 1
        runs = [{"id": 1, "status": "done", "model": "m", "started_at": "2026-01-01T00:00:00",
                 "ended_at": "2026-01-01T00:01:00", "budget_used": 0.0, "error": None}]
        messages = [
            {"id": m + 1, "role": "user" if m % 2 == 0 else "assistant",
             "content": [{"type": "text", "text": f"m{m}"}], "run_id": 1,
             "tokens_in": 1, "tokens_out": 1, "model": "m", "created_at": "2026-01-01T00:00:00"}
            for m in range(msgs_per)
        ]
        threads.append({"id": tid, "title": f"imp{tid}", "kind": "chat", "model": "",
                        "created_at": "2026-01-01T00:00:00", "runs": runs, "messages": messages})
    return {"format": "deep-tutor-backup", "version": 2, "exported_at": "2026-01-01T00:00:00",
            "user": {"name": "x", "username": "x"}, "sessions": [], "threads": threads,
            "memories": [], "kb_documents": []}


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_soak383_")
    env = dict(os.environ)
    env.update({
        "DB_PATH": str(Path(tmp) / "soak.db"),
        "DATA_DIR": str(Path(tmp) / "data"),
        "ADMIN_PASSWORD": ADMIN_PW,
        "ADMIN_USERNAME": "soakadmin",
        "PLUGINS_DIR": str(Path(tmp) / "plugins"),
        "SKILLS_DIR": str(Path(tmp) / "skills"),
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
    })
    for k in ("DATA_DIR", "PLUGINS_DIR", "SKILLS_DIR"):
        Path(env[k]).mkdir(parents=True, exist_ok=True)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    log(f"启动 uvicorn :{port}（临时库 {env['DB_PATH']}）")
    t0 = time.monotonic()
    proc = spawn(port, env, log_file)
    try:
        wait_health(base, proc, log_file)
        log(f"健康检查通过（+{time.monotonic()-t0:.1f}s）")

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
            tokens.append(httpx.post(f"{base}/api/auth/login",
                                     json={"username": f"soak{i}", "password": "soak-user-pw-456"},
                                     timeout=10).json()["token"])
        log(f"{N_USERS} 用户就绪")

        # --- S2：事件流吊销断连（R24 复验；先行——与风暴隔离避免干扰） ---
        # 注：httpx 0.28 的 stream 进入阶段会等首个网络读（SSE 首字节=15s 心跳），
        # 会误判 ReadTimeout——须用原始 socket（浏览器/EventsBridge 语义同此路径）。
        r = httpx.post(f"{base}/api/users",
                       json={"username": "evuser", "password": "soak-user-pw-456", "name": "E"},
                       headers=headers_admin, timeout=10)
        assert r.status_code in (200, 201), r.text
        ev_tok = httpx.post(f"{base}/api/auth/login",
                            json={"username": "evuser", "password": "soak-user-pw-456"}, timeout=10).json()["token"]
        ev_uid = httpx.get(f"{base}/api/auth/me", headers={"Authorization": f"Bearer {ev_tok}"}, timeout=10).json()["id"]

        sk = socket.create_connection(("127.0.0.1", port), timeout=10)
        sk.sendall(
            (f"GET /api/events/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
             f"Authorization: Bearer {ev_tok}\r\nAccept: text/event-stream\r\n\r\n").encode()
        )
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            hdr += sk.recv(4096)
        assert b"200" in hdr.split(b"\r\n")[0], hdr[:120]

        t_revoke = time.monotonic()
        r = httpx.put(f"{base}/api/users/{ev_uid}",
                      json={"password": "soak-user-pw-789"}, headers=headers_admin, timeout=10)
        assert r.status_code == 200, r.text
        # 服务端须在 ≤2 心跳周期（30s）+ 余量内主动收流：chunked 终止块或 EOF
        sk.settimeout(45.0)
        closed_at: float | None = None
        try:
            while True:
                data = sk.recv(4096)
                if not data or b"0\r\n\r\n" in data:
                    closed_at = time.monotonic() - t_revoke
                    break
        except OSError:
            closed_at = time.monotonic() - t_revoke  # 连接被重置同样视为断流
        finally:
            sk.close()
        assert closed_at is not None and closed_at <= 45.0, f"吊销后事件流未断开（{closed_at}s）"
        log(f"S2 事件流吊销断连 ✓（改密后 {closed_at:.1f}s 收流，R24 心跳重验生效）")

        # --- S1：删除族×导入写入族交错风暴 ---
        import asyncio
        import random

        stats = {"total": 0, "s5xx": 0, "codes": {}, "deletes": 0}
        stop_at = time.monotonic() + STORM_SECONDS

        async def storm():
            async with httpx.AsyncClient(base_url=base, timeout=15) as ac:
                async def one(tok: str):
                    h = {"Authorization": f"Bearer {tok}"}
                    while time.monotonic() < stop_at:
                        op = random.choice(
                            ["mk_del_session", "mk_del_thread", "notes", "mk_del_note", "list", "usage", "health"]
                        )
                        try:
                            if op == "mk_del_session":
                                resp = await ac.post("/api/sessions", json={"title": "s"}, headers=h)
                                if resp.status_code == 200:
                                    sid = resp.json()["session_id"]
                                    resp = await ac.delete(f"/api/sessions/{sid}", headers=h)
                                    if resp.status_code == 200:
                                        stats["deletes"] += 1
                            elif op == "mk_del_thread":
                                resp = await ac.post("/api/threads", json={"title": "t"}, headers=h)
                                if resp.status_code == 200:
                                    tid = resp.json()["id"]
                                    resp = await ac.delete(f"/api/threads/{tid}", headers=h)
                                    if resp.status_code == 200:
                                        stats["deletes"] += 1
                            elif op == "notes":
                                resp = await ac.post("/api/notes", json={"title": "n", "content": "x"}, headers=h)
                            elif op == "mk_del_note":
                                resp = await ac.post("/api/notes", json={"title": "d", "content": "y"}, headers=h)
                                if resp.status_code in (200, 201):
                                    nid = resp.json().get("id")
                                    if nid:
                                        resp = await ac.delete(f"/api/notes/{nid}", headers=h)
                            elif op == "list":
                                resp = await ac.get("/api/threads?limit=10", headers=h)
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

                async def importer(idx: int):
                    files = {"file": (f"b{idx}.json", json.dumps(_mk_import_payload()), "application/json")}
                    t1 = time.monotonic()
                    r = await ac.post("/api/backup/import", files=files, headers=headers_admin)
                    return r.status_code, time.monotonic() - t1

                tasks = [asyncio.create_task(one(t)) for t in tokens]
                await asyncio.sleep(1.0)
                # 交错窗口：风暴进行中串行 3 笔实质导入 + 1 笔导出（写锁与删除/常规写交错）
                codes = []
                for i in range(3):
                    c, dur = await importer(i)
                    codes.append((c, round(dur, 2)))
                r_exp = await ac.get("/api/backup/export", headers=headers_admin)
                assert r_exp.status_code == 200, r_exp.status_code
                log(f"交错导入（含导入耗时s）：{codes}；导出 {len(r_exp.content) // 1024}KB")
                assert all(c == 200 for c, _ in codes), f"交错导入失败：{codes}"
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=STORM_SECONDS + 30)
                return r_exp

        asyncio.run(storm())
        log(f"S1 风暴完成：{stats['total']} 请求（删除 {stats['deletes']} 次），"
            f"5xx={stats['s5xx']}，状态码分布={stats['codes']}")
        assert stats["s5xx"] == 0, "删除×导入交错出现 5xx"
        # 导入数据完整对账走 S3（直连 DB 计数；threads 列表端点无 x-total-count 表头）

        # --- S3：事后完整性（直接开临时库） ---
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        try:
            proc.wait(timeout=30)
            log("优雅关停 ✓")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("关停超时")

        import sqlite3

        con = sqlite3.connect(env["DB_PATH"])
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            assert not fk, f"FK 违规：{fk[:5]}"
            n_threads = con.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            n_msgs = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            log(f"S3 integrity/foreign_key ✓；threads={n_threads} messages={n_msgs}")
            assert n_msgs >= 3 * 30 * 60, (n_threads, n_msgs)
        finally:
            con.close()
        log("soak_restore_races 全部通过 ✓")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
