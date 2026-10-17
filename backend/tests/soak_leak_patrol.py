"""iter 386 R29：长时间运行内存/句柄泄漏 patrol（手工运行，不自动收集）。

热管理多代循环（reload/disable/enable × 30+ 代）× 常规流量下的进程级趋势：
    - RSS（工作集）/句柄数：暖机后漂移有界（内存/句柄泄漏检测）
    - dt_plugin_* 模块计数：逐出语义的进程内证据（全部禁用后归零）
    - 覆盖对（ga/gb 同 ext 相互覆盖）持续搅动 displaced 栈——iter 386 幽灵修复后
      每代重载不再累积栈条目
诊断数据由服务进程内自检插件（ctypes GetCurrentProcess/GetProcessMemoryInfo/
GetProcessHandleCount，仅 Windows）提供——规避外部 psutil 依赖与跨进程权限问题。

运行：cd backend && ./.venv/Scripts/python.exe tests/soak_leak_patrol.py
"""

from __future__ import annotations

import importlib.util
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
USER_PW = "soak-user-pw-456"
N_CYCLES = int(os.environ.get("PATROL_CYCLES", "30"))
N_ADMINS = 5  # plugin_admin 频控 20/min/管理员——轮转分摊热管理操作

# 自检插件：进程内读自身 RSS/句柄 + dt_plugin 模块计数（泄漏 patrol 的数据源）
SRC_DIAG = """
import ctypes
import sys
from fastapi import APIRouter

PLUGIN_META = {"id": "diagp", "name": "DIAG", "version": "1.0.0"}

if sys.platform == "win32":
    class _PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    _psapi = ctypes.windll.psapi
    _kernel32 = ctypes.windll.kernel32
    # 伪句柄 -1 须按 64 位指针传递（默认 int 截断致调用静默失败）
    _kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    _psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

def register(api):
    router = APIRouter()

    @router.get("/metrics")
    def metrics():
        out = {"plugin_modules": len([m for m in sys.modules if m.startswith("dt_plugin_")]),
               "modules_total": len(sys.modules)}
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

    @router.post("/gc")
    def run_gc():
        # 强制三代回收并回报（泄漏机制探针：回收后句柄回落=挂在可回收垃圾上；
        # 不回落=挂在长寿引用上）
        import gc

        before = handles_now()
        n = gc.collect()
        return {"collected": n, "handles_before": before, "handles_after": handles_now()}

    def handles_now():
        if sys.platform != "win32":
            return -1
        cnt = ctypes.c_uint32(0)
        if _kernel32.GetProcessHandleCount(_kernel32.GetCurrentProcess(), ctypes.byref(cnt)):
            return cnt.value
        return -1

    api.routes.mount(router, auth=False)
"""

# 覆盖对：同 ext 互相覆盖，搅动 displaced 栈
SRC_GA = """
from fastapi import APIRouter

PLUGIN_META = {"id": "ga", "name": "GA", "version": "1.0.0"}

def _parse(path, parse_mode):
    return ("A", {})

def register(api):
    api.parsers.register([".gx"], _parse, label="GA")
"""

SRC_GB = """
from fastapi import APIRouter

PLUGIN_META = {"id": "gb", "name": "GB", "version": "1.0.0"}

def _parse(path, parse_mode):
    return ("B", {})

def register(api):
    api.parsers.register([".gx"], _parse, label="GB")
"""


def log(msg: str) -> None:
    print(f"[patrol] {msg}", flush=True)


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
        return log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        return "(日志不可读)"


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


def main() -> None:
    # 复用 R28 soak 的 5 型插件源码
    spec = importlib.util.spec_from_file_location("soak385", ROOT / "tests" / "soak_plugin_hotreload.py")
    soak = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(soak)

    tmp = tempfile.mkdtemp(prefix="dt_patrol386_")
    sink_port = free_port()
    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(Path(tmp) / "patrol.db"),
            "DATA_DIR": str(Path(tmp) / "data"),
            "ADMIN_PASSWORD": ADMIN_PW,
            "ADMIN_USERNAME": "patroladmin0",
            "PLUGINS_DIR": str(Path(tmp) / "plugins"),
            "SKILLS_DIR": str(Path(tmp) / "skills"),
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
        }
    )
    pdir = Path(env["PLUGINS_DIR"])
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "schd.py").write_text(soak.SRC_SCHD, encoding="utf-8")
    (pdir / "mwp.py").write_text(soak.SRC_MWP, encoding="utf-8")
    # hookp 变体二分（PATROL_HOOKP_MODE）：full=路由+webhook（原样）；route_only=仅路由
    # （含 emit 闭包）；hook_only=仅 webhook 注册——定位重载泄漏面
    hookp_mode = os.environ.get("PATROL_HOOKP_MODE", "full")
    if hookp_mode == "route_only":
        src_hookp = 'from fastapi import APIRouter\n\nPLUGIN_META = {"id": "hookp", "name": "HOOK", "version": "1.0.0"}\n\ndef register(api):\n    router = APIRouter()\n\n    @router.get("/ping")\n    def ping():\n        api.events.emit("hookp.ping", {"t": 1})\n        return {"ok": True}\n\n    api.routes.mount(router, auth=False)\n'
    elif hookp_mode == "route_noapi":
        src_hookp = 'from fastapi import APIRouter\n\nPLUGIN_META = {"id": "hookp", "name": "HOOK", "version": "1.0.0"}\n\ndef register(api):\n    router = APIRouter()\n\n    @router.get("/ping")\n    def ping():\n        return {"ok": True}\n\n    api.routes.mount(router, auth=False)\n'
    elif hookp_mode == "hook_only":
        src_hookp = (
            'PLUGIN_META = {"id": "hookp", "name": "HOOK", "version": "1.0.0"}\n\ndef register(api):\n    api.webhooks.register("http://127.0.0.1:%d/hook", events=["hookp.ping"], secret="%s")\n'
            % (sink_port, soak.WEBHOOK_SECRET)
        )
    else:
        src_hookp = soak.SRC_HOOKP.replace("{SINK_PORT}", str(sink_port)).replace(
            "{WEBHOOK_SECRET}", soak.WEBHOOK_SECRET
        )
    (pdir / "hookp.py").write_text(src_hookp, encoding="utf-8")
    (pdir / "provp.py").write_text(soak.SRC_PROVP, encoding="utf-8")
    (pdir / "toolp.py").write_text(soak.SRC_TOOLP, encoding="utf-8")
    (pdir / "diagp.py").write_text(SRC_DIAG, encoding="utf-8")
    (pdir / "ga.py").write_text(SRC_GA, encoding="utf-8")
    (pdir / "gb.py").write_text(SRC_GB, encoding="utf-8")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_file = Path(tmp) / "uvicorn.log"
    log(f"启动 uvicorn :{port}（8 插件含覆盖对 ga/gb + 自检 diagp）")
    t0 = time.monotonic()
    proc = spawn(port, env, log_file)
    try:
        wait_health(base, proc, log_file)
        log(f"健康检查通过（+{time.monotonic() - t0:.1f}s）")

        # 管理员池（plugin_admin 频控按用户分键）+ 风暴用户
        admins = []
        r = httpx.post(f"{base}/api/auth/login", json={"username": "patroladmin0", "password": ADMIN_PW}, timeout=10)
        tok0 = r.json()["token"]
        admins.append({"Authorization": f"Bearer {tok0}"})
        for i in range(1, N_ADMINS):
            # 无自助注册面：管理员 0 代建其余管理员
            r = httpx.post(
                f"{base}/api/users",
                json={"username": f"patroladmin{i}", "password": ADMIN_PW, "name": "PA", "is_admin": True},
                headers=admins[0],
                timeout=10,
            )
            assert r.status_code in (200, 201), r.text
            tok = httpx.post(
                f"{base}/api/auth/login", json={"username": f"patroladmin{i}", "password": ADMIN_PW}, timeout=10
            ).json()["token"]
            admins.append({"Authorization": f"Bearer {tok}"})
        r = httpx.post(
            f"{base}/api/users",
            json={"username": "storm1", "password": USER_PW, "name": "S1"},
            headers=admins[0],
            timeout=10,
        )
        assert r.status_code in (200, 201), r.text
        tok_user = httpx.post(
            f"{base}/api/auth/login", json={"username": "storm1", "password": USER_PW}, timeout=10
        ).json()["token"]
        headers_user = {"Authorization": f"Bearer {tok_user}"}

        def metrics() -> dict:
            r = httpx.get(f"{base}/api/plugins/diagp/metrics", timeout=10)
            assert r.status_code == 200, r.text
            return r.json()

        def manage(op: str, pid: str, seq: int) -> None:
            r = httpx.post(f"{base}/api/plugins/{pid}/{op}", headers=admins[seq % N_ADMINS], timeout=30)
            assert r.status_code == 200, f"{op} {pid}: {r.status_code} {r.text}"

        m0 = metrics()
        assert "rss_bytes" in m0 and "handles" in m0, f"自检插件不可用（非 Windows?）: {m0}"
        log(
            f"基线：rss={m0['rss_bytes'] / 1048576:.1f}MB handles={m0['handles']} plugin_modules={m0['plugin_modules']}"
        )

        samples = []
        op_seq = 0
        do_reload = os.environ.get("PATROL_NO_RELOAD") != "1"  # 二分：隔离重载面
        do_traffic = os.environ.get("PATROL_NO_TRAFFIC") != "1"  # 二分：隔离流量面
        reload_set = os.environ.get("PATROL_RELOAD_SET", "schd,hookp,ga,gb").split(",")
        do_mwp = os.environ.get("PATROL_NO_MWP") != "1"  # 二分：中间件重建面
        for cyc in range(N_CYCLES):
            if do_reload:
                for pid in reload_set:  # 每代重载（含覆盖对搅动 displaced）
                    manage("reload", pid, op_seq)
                    op_seq += 1
                if do_mwp and cyc % 5 == 4:  # 每 5 代一对 disable/enable（卸载路径也进趋势）
                    manage("disable", "mwp", op_seq)
                    op_seq += 1
                    manage("enable", "mwp", op_seq)
                    op_seq += 1
            if do_traffic:
                # 常规流量（每代一轮，制造真实分配/GC）
                for _ in range(3):
                    httpx.get(f"{base}/api/plugins/schd/ticks", timeout=10)
                    httpx.get(f"{base}/api/plugins/hookp/ping", timeout=10)
                    httpx.get(f"{base}/api/plugins/provp/complete", headers=headers_user, timeout=15)
                    httpx.get(f"{base}/api/plugins/toolp/call", headers=headers_user, timeout=15)
            samples.append(metrics())
            # 无流量时循环太快会撞 plugin_admin 频控（20/min/管理员）——补足配速
            time.sleep(3.2 if not do_traffic else 1.2)

        rss = [s["rss_bytes"] / 1048576 for s in samples]
        hnd = [s["handles"] for s in samples]
        mods = [s["plugin_modules"] for s in samples]
        log(f"RSS(MB) 趋势：{[round(x, 1) for x in rss]}")
        log(f"句柄趋势：{hnd}")
        log(f"plugin_modules 趋势：{mods}")

        WARM = 6
        rss_drift = sum(rss[-3:]) / 3 - sum(rss[WARM : WARM + 4]) / 4
        hnd_drift = max(hnd[-3:]) - min(hnd[WARM : WARM + 4])
        assert rss_drift <= 12.0, f"RSS 暖机后漂移 +{rss_drift:.1f}MB——疑似内存泄漏"
        assert hnd_drift <= 15, f"句柄暖机后增长 +{hnd_drift}——疑似句柄泄漏"
        assert mods[-1] == mods[0], f"dt_plugin 模块数漂移 {mods[0]}->{mods[-1]}——模块逐出失效"

        # 终局：逐个禁用（diagp 最后），进程内模块计数归零/仅剩自身
        for pid in ("schd", "mwp", "hookp", "provp", "toolp", "ga", "gb"):
            manage("disable", pid, op_seq)
            op_seq += 1
        m_end = metrics()
        # 其余 7 个已卸载：仅 diagp 自身模块在（含其未被子模块前缀干扰）
        assert m_end["plugin_modules"] == 1, f"全部禁用后模块残留: {m_end['plugin_modules']}"
        manage("disable", "diagp", op_seq)
        log(
            f"终局 ✓（卸载后 plugin_modules 7→{m_end['plugin_modules']}，"
            f"rss {m_end['rss_bytes'] / 1048576:.1f}MB handles {m_end['handles']}）"
        )

        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        try:
            proc.wait(timeout=30)
            log("优雅关停 ✓")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("关停超时")

        tail = log_file.read_text(encoding="utf-8", errors="replace")
        assert "Traceback (most recent call last)" not in tail, tail[-1500:]
        log("soak_leak_patrol 全部通过 ✓（RSS 漂移 %.1fMB、句柄 %+d、模块逐出干净）" % (rss_drift, hnd_drift))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
