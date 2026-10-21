"""可丢弃临时产物的落盘位置。

与 Go 侧 ``internal/base/scratchroot`` 同一套规则。可重建的产物（测试库、
测试数据目录、临时导出件）不写系统卷：系统卷小、由操作系统按自己的节奏
回收，并且住着机器上其他租户。

解析顺序：

1. **调用方重定向过的临时目录**（``TMPDIR``/``TEMP``/``TMP`` 与系统默认
   不同）。隔离优先：用 ``TMPDIR`` 做隔离的夹具必须继续隔离。
2. ``$DEEPTUTOR_SCRATCH_DIR``——部署方显式指定。
3. **项目所在卷的 TEMP 目录**——该卷与系统临时目录不同卷时才用（否则不是
   搬迁，是原地踏步）。
4. 系统临时目录。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ENV_VAR = "DEEPTUTOR_SCRATCH_DIR"
PROJECT_DIR_NAME = "deeptutor"
VOLUME_TEMP_NAME = "TEMP"

# 仓库根（backend/app/core/scratchroot.py -> 上三级），用于定位项目所在卷。
_REPO_ROOT = Path(__file__).resolve().parents[3]

_probe_cache: dict[str, bool] = {}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _system_temp() -> str:
    """当前生效的临时目录（不读 Python 的 tempfile 缓存）。"""
    for key in ("TMPDIR", "TEMP", "TMP"):
        value = _env(key)
        if value:
            return value
    return tempfile.gettempdir()


def _default_system_temp() -> str:
    """无人干预时的系统临时目录。"""
    if os.name == "nt":
        local = _env("LOCALAPPDATA")
        if local:
            return os.path.join(local, "Temp")
        root = _env("SystemRoot")
        if root:
            return os.path.join(root, "Temp")
        return ""
    return "/tmp"


def _same_path(a: str, b: str) -> bool:
    ca, cb = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    return ca == cb


def _writable(path: str) -> bool:
    cached = _probe_cache.get(path)
    if cached is not None:
        return cached
    ok = False
    try:
        fd, probe = tempfile.mkstemp(prefix=".deeptutor-probe-", dir=path)
        os.close(fd)
        os.unlink(probe)
        ok = True
    except OSError:
        ok = False
    _probe_cache[path] = ok
    return ok


def _volume_temp_base() -> str | None:
    """项目所在卷的 TEMP 目录；不可用时返回 None。"""
    drive = os.path.splitdrive(str(_REPO_ROOT))[0]
    if not drive:
        return None
    system_drive = os.path.splitdrive(_system_temp())[0]
    if drive and system_drive and drive.lower() == system_drive.lower():
        # 与系统临时目录同卷：换过去不是搬迁。
        return None
    candidate = os.path.join(drive + os.sep, VOLUME_TEMP_NAME)
    if not os.path.isdir(candidate) or not _writable(candidate):
        return None
    return candidate


def root() -> str:
    """可丢弃临时产物的根目录（不创建）。"""
    system_temp = _system_temp()
    default = _default_system_temp()
    if default and not _same_path(system_temp, default):
        # 调用方重定向过：隔离优先。
        return os.path.normpath(system_temp)

    override = _env(ENV_VAR)
    if override:
        if os.path.isabs(override):
            return os.path.normpath(override)
        return os.path.abspath(override)

    base = _volume_temp_base()
    if base:
        return os.path.join(base, PROJECT_DIR_NAME)
    return os.path.join(system_temp, PROJECT_DIR_NAME)


def subdir(name: str) -> str:
    """根目录下的具名子目录（已创建）。"""
    path = os.path.join(root(), name)
    os.makedirs(path, exist_ok=True)
    return path


def configure_tempdir() -> str:
    """把 ``tempfile`` 的落盘位置指向根目录。

    ``tempfile.gettempdir()`` 一旦取值就缓存，且不校验目录是否存在；这里
    先建好目录再赋值，避免 ``mkstemp`` 在目录缺失时抛错。
    """
    path = root()
    os.makedirs(path, exist_ok=True)
    tempfile.tempdir = path
    return path
