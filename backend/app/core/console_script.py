"""定位「pip 装的包」提供的可执行入口。

pip 把 console script 装进**当前环境**的 scripts 目录（Windows: ``<venv>\\Scripts``，
POSIX: ``<venv>/bin``）。而按路径启动解释器（``backend\\.venv\\Scripts\\python.exe -m
uvicorn ...``）并**不会**把那个目录加进 PATH——于是服务进程里 ``shutil.which`` 看不见
自己刚装的东西。

实测（iter 422）：启动脚本花 2GB 装好 marker-pdf，服务进程却报「PDF 解析依赖
marker_single（pip install marker-pdf），但未在 PATH 找到」——full 档 PDF 解析在真实
部署里根本走不通（该入口只有装 OCR 栈的机器才有，故此前没被自动测试覆盖）。

系统级二进制（tesseract / ffprobe）不在此列：它们由系统安装，本来就该在 PATH 上，
按 PATH 找才是对的。

不在启动脚本里改 PATH 的理由：那会改掉整个进程的环境，**子进程**（marker 拉起的
python、tesseract 等）也会跟着换一套可执行文件解析结果——影响面比缺一个入口大得多。
这里只在**需要时**多看一个目录。
"""

from __future__ import annotations

import shutil
import sys
import sysconfig
from pathlib import Path


def find_console_script(name: str) -> str | None:
    """找一个可执行入口，返回绝对路径；找不到返回 None。

    顺序：先按 PATH 找（用户可能有意换掉了 pip 装的那份），再找当前环境的 scripts
    目录——后者是「服务进程自己装的包」的所在处。
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in _script_dirs():
        for candidate in (directory / f"{name}.exe", directory / name):
            if candidate.is_file():
                return str(candidate)
    return None


def _script_dirs() -> list[Path]:
    """当前解释器所属环境的 scripts 目录（去重）。"""
    dirs: list[Path] = []
    try:
        dirs.append(Path(sysconfig.get_path("scripts")))
    except (KeyError, OSError):  # pragma: no cover - sysconfig 基本不会失败
        pass
    beside = Path(sys.executable).parent
    if beside not in dirs:
        dirs.append(beside)
    return dirs
