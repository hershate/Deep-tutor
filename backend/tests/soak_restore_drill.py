"""备份/恢复链真实进程演练（iter 392 R35，手工运行不自动收集）。

对 R26 静态审计结论（_RESTORE_LOCK 互斥 / append-only 导入 / restore 全程 to_thread
不冻事件循环 / 单事务回滚）做真实 uvicorn × 并发流量下的复验：

- 导入期间在途请求零 5xx（读走 WAL 不受写锁影响；并发写在 busy_timeout 内串行）
- 事件循环不冻（restore 窗口内 /api/health 延迟有界）
- 并发第二导入撞互斥 -> 409（窗口命中即证据，未命中留日志）
- 导入后完整性：append 语义（原数据不动 + 新行全量到位）、材料文件落盘、
  DB integrity_check、u2（他用户）数据不受扰动
- SSE 长连接全程心跳不断（restore 不扰动全局流）

数据面：u1 直插 200 篇 8KB 笔记（API 种子会撞 60/min 写频控）+ 1 会话 1 材料
（API 建：upload+confirm none，材料文件入 data/sessions/<sid>/）；u2 打流量。
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
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

spec = importlib.util.spec_from_file_location("soak385", ROOT / "tests" / "soak_plugin_hotreload.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)
spec2 = importlib.util.spec_from_file_location("soak389", ROOT / "tests" / "soak_overnight.py")
overnight = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(overnight)

ADMIN_PW = "DrillAdmin!2026"
U1_PW = "DrillU1!2026"
U2_PW = "DrillU2!2026"
SEED_NOTES = 150
NOTE_KB = 24 * 1024  # 备份 ~3.6MB：导入窗口足够宽（上传+解析+插入+commit 数秒），互斥可命中；
# 150×3（种子+双导入全成功）= 450 < 500 导入配额——second 两种结局（409/200）均可确定性断言
TRAFFIC_SECONDS = 12  # restore 前后各半，导入在 ~40% 处触发
HEALTH_MAX_IN_WINDOW_S = 3.0

TITLES_PREFIX = "drill-note-"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def db_ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    return con


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dt_drill392_")
    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(Path(tmp) / "drill.db"),
            "DATA_DIR": str(Path(tmp) / "data"),
            "ADMIN_PASSWORD": ADMIN_PW,
            "ADMIN_USERNAME": "drilladmin",
            "PLUGINS_DIR": str(Path(tmp) / "plugins"),
            "SKILLS_DIR": str(Path(tmp) / "skills"),
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
        }
    )
    Path(env["PLUGINS_DIR"]).mkdir(parents=True, exist_ok=True)

    port = soak.free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    log(f"启动 uvicorn :{port}")
    t0 = time.monotonic()
    proc = soak.spawn(port, env, log_file)
    stats = {"codes": {}, "s5xx": 0, "health_lat": [], "u2_writes": 0, "u2_deletes": 0}
    stop_flag = threading.Event()
    watch = None
    try:
        soak.wait_health(base, proc, log_file)
        tok_admin = httpx.post(
            f"{base}/api/auth/login", json={"username": "drilladmin", "password": ADMIN_PW}, timeout=10
        ).json()["token"]
        h_admin = {"Authorization": f"Bearer {tok_admin}"}
        users = {}
        for u, pw in (("u1", U1_PW), ("u2", U2_PW)):
            r = httpx.post(
                f"{base}/api/users", json={"username": u, "password": pw, "name": u}, headers=h_admin, timeout=10
            )
            assert r.status_code in (200, 201), r.text
            users[u] = httpx.post(f"{base}/api/auth/login", json={"username": u, "password": pw}, timeout=10).json()[
                "token"
            ]
        h1 = {"Authorization": f"Bearer {users['u1']}"}
        h2 = {"Authorization": f"Bearer {users['u2']}"}

        # --- 种子：u1 直插 200 篇 8KB 笔记（绕 60/min 频控；导出/导入路径与 API 行完全等价）---
        db_path = env["DB_PATH"]
        uid1 = 2  # drilladmin=1, u1=2, u2=3（建号顺序）
        con = sqlite3.connect(db_path, timeout=15)
        try:
            cur = con.executemany(
                "INSERT INTO notes(user_id, title, content) VALUES (?, ?, ?)",
                [(uid1, f"{TITLES_PREFIX}{i:03d}", "x" * NOTE_KB) for i in range(SEED_NOTES)],
            )
            con.commit()
            assert cur.rowcount == SEED_NOTES
        finally:
            con.close()
        # 会话 + 材料（API 全链路：材料文件落盘 + session 行）
        sid = httpx.post(f"{base}/api/sessions", json={"title": "drill 会话"}, headers=h1, timeout=10).json()[
            "session_id"
        ]
        mat_r = httpx.post(
            f"{base}/api/materials/upload",
            data={"session_id": str(sid)},
            files={"file": ("seed.txt", "drill material content".encode(), "text/plain")},
            headers=h1,
            timeout=30,
        )
        assert mat_r.status_code in (200, 201), mat_r.text
        mid = mat_r.json()["material_id"]
        r = httpx.post(f"{base}/api/materials/{mid}/confirm", json={}, headers=h1, timeout=10)
        assert r.status_code == 200, r.text
        log(f"种子完成：{SEED_NOTES} 笔记 + 会话 {sid} + 材料 {mid}")

        # --- 基线（进程外只读）---
        con = db_ro(db_path)
        base_counts = {
            "notes_u1": con.execute("SELECT COUNT(*) FROM notes WHERE user_id=?", (uid1,)).fetchone()[0],
            "sessions": con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "materials": con.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
        }
        con.close()

        # --- 导出 u1 备份 ---
        exp = httpx.get(f"{base}/api/backup/export", headers=h1, timeout=120)
        assert exp.status_code == 200, exp.text
        backup = exp.content
        backup_json = json.loads(backup)
        assert len(backup_json["notes"]) == SEED_NOTES and len(backup_json["sessions"]) == 1
        log(f"导出完成：{len(backup) / 1e6:.2f}MB（notes={len(backup_json['notes'])}, sessions=1, materials=1）")
        # 预检（同文件）：计数一致（preview 只读不落库）
        prev = httpx.post(
            f"{base}/api/backup/preview", files={"file": ("b.json", backup, "application/json")}, headers=h1, timeout=60
        )
        assert prev.status_code == 200, prev.text
        assert prev.json()["notes"] == SEED_NOTES, prev.json()

        # --- SSE 看护（u2）---
        watch = overnight.SseWatch(port, users["u2"], "u2")
        watch.start()
        deadline = time.monotonic() + 15
        while not watch.started and time.monotonic() < deadline:
            time.sleep(0.2)
        assert watch.started, "SSE 建连失败"

        # --- 流量线程 ---
        def reader() -> None:
            reads = [("/api/notes", h2), ("/api/health", None), ("/api/settings", h2), ("/api/plans", h2)]
            i = 0
            with httpx.Client(base_url=base, timeout=30) as c:
                while not stop_flag.is_set():
                    path, hh = reads[i % len(reads)]
                    t = time.monotonic()
                    try:
                        r = c.get(path, headers=hh)
                        lat = time.monotonic() - t
                        stats["codes"][r.status_code] = stats["codes"].get(r.status_code, 0) + 1
                        if r.status_code >= 500:
                            stats["s5xx"] += 1
                            log(f"5xx: GET {path} -> {r.status_code} {r.text[:120]}")
                        if path == "/api/health":
                            stats["health_lat"].append((time.monotonic() - t0, lat))
                    except httpx.HTTPError as e:
                        stats["codes"]["net_err"] = stats["codes"].get("net_err", 0) + 1
                        log(f"网络错误: GET {path}: {e}")
                    i += 1
                    stop_flag.wait(0.3)

        def writer() -> None:
            # u2 CRUD churn：每 3s 一对 create+delete（40 写/min < 60/min 频控）
            with httpx.Client(base_url=base, timeout=30) as c:
                n = 0
                while not stop_flag.is_set():
                    try:
                        r = c.post("/api/notes", json={"title": f"churn-{n}", "content": "y"}, headers=h2)
                        stats["codes"][r.status_code] = stats["codes"].get(r.status_code, 0) + 1
                        if r.status_code >= 500:
                            stats["s5xx"] += 1
                            log(f"5xx: POST /api/notes -> {r.status_code}")
                        if r.status_code == 201:
                            stats["u2_writes"] += 1
                            cid = r.json()["id"]
                            d = c.delete(f"/api/notes/{cid}", headers=h2)
                            stats["codes"][d.status_code] = stats["codes"].get(d.status_code, 0) + 1
                            if d.status_code >= 500:
                                stats["s5xx"] += 1
                            elif d.status_code == 200 or d.status_code == 204:
                                stats["u2_deletes"] += 1
                    except httpx.HTTPError as e:
                        stats["codes"]["net_err"] = stats["codes"].get("net_err", 0) + 1
                        log(f"网络错误（写）: {e}")
                    n += 1
                    stop_flag.wait(3.0)

        th_r = threading.Thread(target=reader, daemon=True, name="reader")
        th_w = threading.Thread(target=writer, daemon=True, name="writer")
        th_r.start()
        th_w.start()
        time.sleep(3)  # 流量进入稳态

        # --- restore 演练：主导入 + 30ms 后第二导入（撞互斥窗口）---
        results: dict[str, object] = {}

        def do_import(tag: str) -> None:
            try:
                r = httpx.post(
                    f"{base}/api/backup/import",
                    files={"file": ("b.json", backup, "application/json")},
                    headers=h1,
                    timeout=120,
                )
                results[tag] = {"status": r.status_code, "body": r.json() if r.status_code != 500 else r.text[:200]}
            except Exception as e:  # noqa: BLE001
                results[tag] = {"status": -1, "err": str(e)}

        t_imp = time.monotonic() - t0
        th1 = threading.Thread(target=do_import, args=("first",))
        th1.start()
        time.sleep(0.01)
        th2 = threading.Thread(target=do_import, args=("second",))
        th2.start()
        th1.join(timeout=150)
        th2.join(timeout=150)
        t_imp_done = time.monotonic() - t0
        log(
            f"导入窗口 [{t_imp:.1f}s, {t_imp_done:.1f}s]：first={results['first']['status']} second={results['second']['status']}"
        )
        time.sleep(3)  # 尾部流量落定
        stop_flag.set()
        th_r.join(timeout=30)
        th_w.join(timeout=30)

        # ===== 断言 =====
        problems: list[str] = []
        first, second = results["first"], results["second"]
        # 双导入竞态对称：锁胜者（先完成解析触达互斥检查的一方）拿 200，另一方 409——
        # 胜者身份不定（线程调度/解析并发），断言按「恰好一个 200」口径
        statuses = sorted([first["status"], second["status"]])
        if statuses == [200, 409]:
            winner = first if first["status"] == 200 else second
            log("并发导入互斥命中 ✓（一胜一 409）")
            s = winner["body"]
            for key, want in [("notes", SEED_NOTES), ("sessions", 1), ("materials", 1)]:
                if s.get(key) != want:
                    problems.append(f"导入摘要 {key}={s.get(key)} != {want}")
        elif statuses == [200, 200]:
            log("⚠ 双导入均成功（互斥窗口未命中，串行两次 append）——互斥语义以单测为准，此处留档")
            for r in (first, second):
                for key, want in [("notes", SEED_NOTES), ("sessions", 1), ("materials", 1)]:
                    if r["body"].get(key) != want:
                        problems.append(f"导入摘要 {key}={r['body'].get(key)} != {want}")
        else:
            problems.append(f"双导入状态异常（期望 [200,409] 或 [200,200]）：{first['status']}/{second['status']}")

        if stats["s5xx"]:
            problems.append(f"全程 5xx×{stats['s5xx']}（{stats['codes']}）")

        # 事件循环不冻：restore 窗口内 health 延迟有界
        in_window = [lat for (t, lat) in stats["health_lat"] if t_imp <= t <= t_imp_done + 1]
        if in_window:
            mx = max(in_window)
            log(f"restore 窗口 health 请求 {len(in_window)} 个，max {mx * 1000:.0f}ms")
            if mx > HEALTH_MAX_IN_WINDOW_S:
                problems.append(
                    f"restore 窗口 health 最大延迟 {mx:.2f}s > {HEALTH_MAX_IN_WINDOW_S:.0f}s（事件循环冻结嫌疑）"
                )
        all_lat = [lat for (_, lat) in stats["health_lat"]]
        if all_lat:
            all_lat.sort()
            p95 = all_lat[int(len(all_lat) * 0.95) - 1] if len(all_lat) >= 20 else max(all_lat)
            log(f"全程 health p95 {p95 * 1000:.0f}ms / max {max(all_lat) * 1000:.0f}ms")
            if max(all_lat) > HEALTH_MAX_IN_WINDOW_S:
                problems.append(f"全程 health 最大延迟 {max(all_lat):.2f}s（DB 写锁传导超界）")

        # SSE 存活
        if watch.eof:
            problems.append("SSE 流中途断开")
        if watch.max_gap > 45.0:
            problems.append(f"SSE 心跳最大间隙 {watch.max_gap:.0f}s")

        # 导入后完整性（进程外只读核对）
        con = db_ro(db_path)
        ic = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            problems.append(f"integrity_check={ic}")
        # 成功导入次数（1=互斥命中，2=窗口未命中串行双成功）；配额核算：150×3=450 < 500
        imports_ok = statuses.count(200)
        notes_u1 = con.execute("SELECT COUNT(*) FROM notes WHERE user_id=?", (uid1,)).fetchone()[0]
        if notes_u1 != SEED_NOTES * (1 + imports_ok):
            problems.append(f"u1 笔记 {notes_u1} != 种子+导入 {SEED_NOTES}×{1 + imports_ok}")
        # 200 个标题全部出现 (1+imports_ok) 次（append 内容完整）
        appear = 1 + imports_ok
        cnt = con.execute(
            "SELECT COUNT(*) FROM (SELECT title FROM notes WHERE user_id=? AND title LIKE ? GROUP BY title HAVING COUNT(*)=?)",
            (uid1, TITLES_PREFIX + "%", appear),
        ).fetchone()[0]
        if cnt != SEED_NOTES:
            problems.append(f"标题完整复核 {cnt}/{SEED_NOTES}（应有 {SEED_NOTES} 个标题恰好出现 {appear} 次）")
        sessions_now = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if sessions_now != base_counts["sessions"] + imports_ok:
            problems.append(f"会话数 {sessions_now} != 期望 {base_counts['sessions'] + imports_ok}")
        # 材料：新会话的材料行 + 解析文本文件在盘（parsed_text_path 为绝对路径，restore
        # 经 write_parsed_text 落到新会话目录）
        mats = con.execute("SELECT id, parsed_text_path FROM materials ORDER BY id").fetchall()
        con.close()
        if len(mats) < 2:
            problems.append(f"材料行 {len(mats)} < 2（append 未到位）")
        for mid2, ptp in mats:
            if ptp and not Path(ptp).exists():
                problems.append(f"材料 {mid2} 解析文本缺失：{ptp}")
        # u2 不受扰动：churn 净零 + 无 u1 数据混入
        con = db_ro(db_path)
        notes_u2 = con.execute("SELECT COUNT(*) FROM notes WHERE user_id=3").fetchone()[0]
        con.close()
        u2_live = stats["u2_writes"] - stats["u2_deletes"]
        if notes_u2 != u2_live:
            problems.append(f"u2 笔记 {notes_u2} != churn 净存 {u2_live}（他用户数据被扰动）")

        log("=== 汇总 ===")
        log(
            f"请求分布 {stats['codes']}；restore 窗口 [{t_imp:.1f}s, {t_imp_done:.1f}s]（{(t_imp_done - t_imp) * 1000:.0f}ms）"
        )
        win_body = (first if first["status"] == 200 else second)["body"]
        log(f"导入摘要（胜者）={win_body}")
        log(f"SSE pings={watch.pings} maxgap={watch.max_gap:.0f}s eof={watch.eof}")
        log(
            f"DB：u1 笔记 {notes_u1}（种子+{imports_ok} 次导入）、会话 {sessions_now}、材料 {len(mats)}、integrity={ic}；u2 笔记 {notes_u2}"
        )

        if problems:
            for p in problems:
                log(f"❌ {p}")
            raise SystemExit(1)
        log("✅ 备份/恢复链真实进程演练全部断言通过")

    finally:
        stop_flag.set()
        if proc.poll() is None:
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        log(f"uvicorn 退出 rc={proc.returncode}；日志 {log_file}")


if __name__ == "__main__":
    main()
