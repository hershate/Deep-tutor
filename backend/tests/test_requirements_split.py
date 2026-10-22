"""依赖文件必须保持「自身可解析」：OCR 栈不得回到服务栈的依赖文件里。

背景
----
marker-pdf 与 surya-ocr 都钉 ``Pillow<11``（上游至今未放开：marker-pdf 2.0.0、surya-ocr
0.22.1 仍钉 <11），而 ``backend/requirements.txt`` 为**服务进程**强制 ``Pillow>=13.0``
（服务进程直接 ``Image.open`` 解码用户上传的图片，见 ``app/tools/parse_material.py``）。
两者写进同一个依赖文件时，pip 的解析器判为无解（ResolutionImpossible）——安装必然失败，
而启动脚本第一步就是装它：装不上，服务永远起不来（2026-10-22 实测 ``scripts/start.ps1``
即卡在这一步，``/api/health`` 拒绝连接）。

故服务栈与 OCR 栈分两个文件，OCR 栈另装（见 ``backend/requirements-ocr.txt`` 的说明）。
本文件钉住这个形状：有人把 marker 写回服务栈、或把两个栈又并回一个文件，启动脚本会重新
变成「装不上就起不来」，而那正是本文件要防的回归。
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CORE_REQ = BACKEND / "requirements.txt"
OCR_REQ = BACKEND / "requirements-ocr.txt"

# 这些包把 Pillow 钉在 <11，与服务栈的下限互斥，故不得出现在服务栈的依赖文件里。
OCR_STACK = ("marker-pdf", "surya-ocr")

_SEPARATORS = ("==", ">=", "<=", "!=", "~=", ">", "<", ";")


def _requirement_names(path: Path) -> set[str]:
    """读一个依赖文件里被声明的包名（忽略注释行与 pip 选项行）。"""
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[", 1)[0]
        for sep in _SEPARATORS:
            name = name.split(sep, 1)[0]
        cleaned = name.strip().lower().replace("_", "-")
        if cleaned:
            names.add(cleaned)
    return names


def _script_text(name: str) -> str:
    return (REPO / "scripts" / name).read_text(encoding="utf-8-sig").replace("\\", "/")


def test_the_service_requirements_do_not_carry_the_ocr_stack() -> None:
    names = _requirement_names(CORE_REQ)
    clash = names & set(OCR_STACK)
    assert not clash, (
        f"{CORE_REQ.name} 不得声明 {clash}——它们钉 Pillow<11，与本文件强制的 Pillow 下限互斥，"
        "pip 会判为无解（ResolutionImpossible），装不上服务就起不来；OCR 栈另见 requirements-ocr.txt"
    )


def test_the_ocr_requirements_carry_the_pdf_stack() -> None:
    assert OCR_REQ.exists(), "缺少 backend/requirements-ocr.txt——PDF 深度解析栈的依赖无处可去"
    names = _requirement_names(OCR_REQ)
    assert "marker-pdf" in names, f"{OCR_REQ.name} 应当声明 marker-pdf（实际：{sorted(names)}）"


def test_the_service_requirements_keep_the_pillow_security_floor() -> None:
    """服务栈的 Pillow 下限不得低于 13.0。

    iter 372 定的 12.3 只是当时的最新版；OSV 实测（iter 421）：10.4.0 → 60 条公告、
    12.3.0 → 28 条、13.0.0 → 0 条。这些公告里有「恶意图片解码可达」类（服务进程直接
    ``Image.open`` 解码用户上传的图片），13.0.0 才是修掉这批的那一版。

    这里按数值比较而不是写死字符串：日后下限再往上升不必改这个守卫，往下降则必须红。
    """
    text = CORE_REQ.read_text(encoding="utf-8")
    match = re.search(r"(?im)^Pillow>=(\d+)\.(\d+)", text)
    assert match, "服务栈必须声明 Pillow 的版本下限"
    floor = (int(match.group(1)), int(match.group(2)))
    assert floor >= (13, 0), (
        f"服务栈的 Pillow 下限 {floor[0]}.{floor[1]} 低于 13.0——"
        "12.3.0 仍带 28 条 OSV 公告（恶意图片解码可达），13.0.0 是零公告版"
    )


def test_both_startup_scripts_install_the_ocr_stack_after_the_service_stack() -> None:
    """顺序是「服务栈 → OCR 栈 → 服务栈」：末次的服务栈安装把 Pillow 拽回安全版本。"""
    core = "pip install -q -r backend/requirements.txt"
    ocr = "pip install -q -r backend/requirements-ocr.txt"
    for name in ("start.ps1", "start.sh"):
        text = _script_text(name)
        assert core in text, f"scripts/{name} 未安装服务栈依赖"
        assert ocr in text, f"scripts/{name} 未安装 OCR 栈依赖"
        assert text.index(core) < text.index(ocr), f"scripts/{name} 必须先装服务栈再装 OCR 栈"
        assert text.rindex(core) > text.index(ocr), f"scripts/{name} 装完 OCR 栈必须重装服务栈（Pillow 回到安全版本）"
