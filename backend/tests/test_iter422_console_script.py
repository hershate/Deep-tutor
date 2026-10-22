"""iter 422：pip 装的入口脚本不能只按 PATH 找。

背景
----
pip 把 console script 装进**当前环境**的 scripts 目录（Windows: ``<venv>\\Scripts``），
而按路径启动解释器（``backend\\.venv\\Scripts\\python.exe -m uvicorn ...``）并不把那个
目录加进 PATH。服务进程里 ``shutil.which("marker_single")`` 因此返回 None——启动脚本
花 2GB 装好的 marker，full 档 PDF 解析在真实部署里却报「未在 PATH 找到」。

本文件钉住「PATH 优先，找不到再看当前环境的 scripts 目录」这条规则，以及
「找不到就是 None」这个边界。
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

import pytest

from app.core import console_script as cs


def _fake_environment(monkeypatch: pytest.MonkeyPatch, scripts: Path, on_path: str | None) -> None:
    """把解析器的两个查找处都指到临时目录，并决定 PATH 上有没有。"""
    monkeypatch.setattr(cs.sysconfig, "get_path", lambda name: str(scripts))
    monkeypatch.setattr(cs.sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(cs.shutil, "which", lambda name: on_path)


def test_an_entry_point_next_to_the_interpreter_is_found_without_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    exe = scripts / "marker_single.exe"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    _fake_environment(monkeypatch, scripts, on_path=None)

    assert cs.find_console_script("marker_single") == str(exe)


def test_a_posix_style_entry_point_is_found_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripts = tmp_path / "bin"
    scripts.mkdir()
    entry = scripts / "marker_single"
    entry.write_text("#!/bin/sh\n", encoding="utf-8")

    _fake_environment(monkeypatch, scripts, on_path=None)

    assert cs.find_console_script("marker_single") == str(entry)


def test_path_wins_over_the_environment_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PATH 上的同名程序优先：用户可能有意换掉了 pip 装的那份。"""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "marker_single.exe").write_text("#!/bin/sh\n", encoding="utf-8")
    on_path = str(tmp_path / "elsewhere" / "marker_single")

    _fake_environment(monkeypatch, scripts, on_path=on_path)

    assert cs.find_console_script("marker_single") == on_path


def test_a_missing_entry_point_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_environment(monkeypatch, tmp_path, on_path=None)

    assert cs.find_console_script("marker_single") is None


def test_the_running_environment_sees_its_own_pip_installed_entry_point() -> None:
    """本机装了 marker-pdf 时，解析器必须能找到它——哪怕 PATH 里没有。

    这正是真实部署的形状：启动脚本按路径调用 venv 的解释器，PATH 里没有它的
    scripts 目录。
    """
    scripts = Path(sysconfig.get_path("scripts"))
    installed = any(
        (scripts / name).is_file() for name in ("marker_single.exe", "marker_single")
    )
    if not installed:
        pytest.skip("本机未装 marker-pdf（OCR 栈可选）")

    assert cs.find_console_script("marker_single") is not None
