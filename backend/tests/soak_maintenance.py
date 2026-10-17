"""iter 372 R14：维护任务真实调度实证（手工运行，不入自动收集——无 test_ 前缀）。

此前 maintenance 只被单测覆盖（直接调 run_maintenance 各函数）；「lifespan 拉起的
maintenance_loop 周期调度」这一层从未在真实进程下实证。本脚本以加速节奏
（MAINTENANCE_INTERVAL_SECONDS=3s / ORPHAN_GRACE_SECONDS=8s，settings 旋钮，
默认仍 24h）驱动真实 uvicorn 子进程，实证四件事：

1. WAL checkpoint(TRUNCATE) 由循环真实触发（日志 + -wal 截断）；
2. prune_api_usage 按 API_USAGE_RETENTION_DAYS=180 裁掉 200 天旧行、保留新行，
   且幂等（后续轮次不再删——裁剪日志恰出现一次）；
3. 孤儿目录 24h 冷却（加速为 8s）：陈旧孤儿首轮即删（恰一次）；新鲜孤儿冷却窗内
   不删、过期后在后续轮次删除；活跃会话目录永不删；
4. 全程零 5xx 面不受影响，事后 DB integrity_check ok。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_maintenance.py
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from soak_process import ROOT, free_port, spawn, wait_health

ADMIN_PW = "soak-mt-pw-123"


def log(msg: str) -> None:
    print(f"[mt] {msg}", flush=True)


def _mk_env(tmp: str, **extra: str) -> dict:
    env = dict(os.environ)
    env.update({
        "DB_PATH": str(Path(tmp) / "mt.db"),
        "DATA_DIR": str(Path(tmp) / "data"),
        "ADMIN_PASSWORD": ADMIN_PW,
        "ADMIN_USERNAME": "soakmtadmin",
        "FRONTEND_DIST": str(ROOT.parent / "frontend" / "dist"),
        "PLUGINS_DIR": str(Path(tmp) / "plugins"),
        "SKILLS_DIR": str(Path(tmp) / "skills"),
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        # 加速节奏：3s 一轮 / 孤儿冷却 8s / 保留 180 天（裁 200 天旧行）
        "MAINTENANCE_INTERVAL_SECONDS": "3",
        "ORPHAN_GRACE_SECONDS": "8",
        "API_USAGE_RETENTION_DAYS": "180",
        **extra,
    })
    for k in ("DATA_DIR", "PLUGINS_DIR", "SKILLS_DIR"):
        Path(env[k]).mkdir(parents=True, exist_ok=True)
    return env


def _stop(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        except OSError:
            proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _age(path: Path, days: float) -> None:
    import os as _os

    stamp = time.time() - days * 86400
    for p in [path, *path.iterdir()]:
        _os.utime(p, (stamp, stamp))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_mt_")
    db_path = str(Path(tmp) / "mt.db")
    env = _mk_env(tmp)
    sess_root = Path(env["DATA_DIR"]) / "sessions"

    # 阶段一：起服建 schema（迁移提交后即可离线种子）
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log1 = Path(tmp) / "phase1.log"
    p1 = spawn(port, env, log1)
    wait_health(base, p1, log1)
    assert httpx.get(f"{base}/api/health", timeout=5).status_code == 200
    _stop(p1)
    log("阶段一：schema 就绪（首轮启动仅迁移，未到首个维护轮）")

    # 离线种子：
    #  api_usage：3 行 200 天前（应裁）+ 2 行当下（应留）；user_id 可空（P4 列可空）
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO api_usage (provider, model, tokens_in, tokens_out, cache_read, cache_write, cost, created_at) "
        "SELECT 'anthropic', 'm', 10, 5, 0, 0, 0.1, datetime('now', '-200 days') "
        "UNION ALL SELECT 'anthropic', 'm', 20, 8, 0, 0, 0.2, datetime('now', '-200 days') "
        "UNION ALL SELECT 'anthropic', 'm', 30, 9, 0, 0, 0.3, datetime('now', '-200 days') "
        "UNION ALL SELECT 'anthropic', 'm', 5, 2, 0, 0, 1.0, datetime('now') "
        "UNION ALL SELECT 'anthropic', 'm', 6, 3, 0, 0, 1.5, datetime('now')"
    )
    #  活跃会话（行在 + 目录旧）：永不删
    conn.execute("INSERT INTO users (name, username, password_hash) VALUES ('mt', 'mt_seed', 'x')")
    conn.execute("INSERT INTO sessions (user_id, title, created_at) "
                 "SELECT id, '存活', datetime('now', '-30 days') FROM users WHERE username='mt_seed'")
    conn.commit()
    alive_sid = conn.execute("SELECT id FROM sessions").fetchone()[0]
    conn.close()
    alive_dir = sess_root / str(alive_sid)
    alive_dir.mkdir(parents=True)
    (alive_dir / "parsed.txt").write_text("存活会话资料", encoding="utf-8")
    _age(alive_dir, days=30)  # mtime 远超冷却——考验「行在则不删」而非靠新鲜度
    #  陈旧孤儿（无行 + mtime 3 天前）：首轮即删
    stale_dir = sess_root / "999991"
    stale_dir.mkdir()
    (stale_dir / "orphan.txt").write_text("x", encoding="utf-8")
    _age(stale_dir, days=3)
    #  新鲜孤儿：阶段二健康通过后再落盘（健康耗时不挤占 8s 冷却窗），见下
    log(f"种子完成：旧用量 3 行+新 2 行；存活会话 {alive_sid}；陈旧孤儿 999991")

    # 阶段二：加速节奏起服，观察真实循环调度
    log2 = Path(tmp) / "phase2.log"
    p2 = spawn(port, env, log2)
    t0 = time.monotonic()
    try:
        wait_health(base, p2, log2)
        # 新鲜孤儿此刻落盘（冷却 8s 从现在起算；循环首轮在 ~3s 后）
        fresh_dir = sess_root / "999992"
        fresh_dir.mkdir()
        (fresh_dir / "orphan.txt").write_text("x", encoding="utf-8")
        # 两轮维护（t≈3s、6s；冷却 8s 内）：陈旧孤儿应已删、新鲜孤儿应仍在
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and stale_dir.exists():
            time.sleep(0.3)
        assert not stale_dir.exists(), "陈旧孤儿未被首轮清理"
        assert fresh_dir.exists(), "冷却窗内的 fresh 孤儿被误删（冷却失效）"
        assert alive_dir.exists() and (alive_dir / "parsed.txt").exists(), "存活会话目录被误删"
        # 服务面不受维护影响
        r = httpx.get(f"{base}/api/health", timeout=5)
        assert r.status_code == 200
        conn = sqlite3.connect(db_path)
        old_n = conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE created_at < datetime('now', '-150 days')"
        ).fetchone()[0]
        new_n = conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE created_at >= datetime('now', '-150 days')"
        ).fetchone()[0]
        conn.close()
        assert old_n == 0, f"200 天旧用量行未被裁剪（剩 {old_n}）"
        assert new_n == 2, f"新用量行被误裁（剩 {new_n}）"
        log(f"前两轮（+{time.monotonic()-t0:.1f}s）：裁剪/陈旧孤儿/冷却保护/存活保护 全部命中")

        # 冷却过期（8s）后的轮次：新鲜孤儿被删（grace 语义的另一侧）
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and fresh_dir.exists():
            time.sleep(0.3)
        assert not fresh_dir.exists(), "冷却过期后的孤儿未被后续轮清理"
        assert alive_dir.exists(), "存活会话目录在多轮维护后被误删"
        log(f"冷却过期侧（+{time.monotonic()-t0:.1f}s）：新鲜孤儿到期删除 ✓")

        # 日志证据：真实循环（非 CLI）触发 + 幂等（裁剪日志恰一次、陈旧孤儿清理恰一次）
        text = log2.read_text(encoding="utf-8", errors="replace")
        assert "WAL checkpoint(TRUNCATE) 完成" in text, "checkpoint 未由循环触发"
        assert text.count("api_usage 保留裁剪") == 1, f"裁剪应恰执行一次（幂等），实际 {text.count('api_usage 保留裁剪')}"
        assert text.count(f"清理孤儿会话目录: {stale_dir}") == 1, "陈旧孤儿应恰清理一次"
        # WAL 截断实证：checkpoint 后 -wal 为 0 字节
        wal = Path(db_path + "-wal")
        assert not wal.exists() or wal.stat().st_size == 0, f"-wal 未截断（{wal.stat().st_size if wal.exists() else '缺失'}B）"
        log("日志证据：checkpoint/裁剪恰一次（幂等）/孤儿恰一次 + -wal 已截断")

        _stop(p2)
        p2 = None
    finally:
        if p2 is not None and p2.poll() is None:
            proc = p2
            _stop(proc)

    conn = sqlite3.connect(db_path)
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    assert ic == "ok", ic
    log(f"关停后 integrity_check={ic}")
    print("\n[mt] ✅ 维护任务真实调度全部实证：checkpoint 裁剪/冷却双向语义/存活保护/幂等/DB 完整性")


if __name__ == "__main__":
    main()
