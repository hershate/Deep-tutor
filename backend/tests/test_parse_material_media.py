"""parse_material 多模态测试（P3，iter 54-55）：图片 OCR + 音视频转写。

环境无 tesseract/faster-whisper——依赖缺失路径走真实行为；成功路径用 fake 模块注入
sys.modules + monkeypatch shutil.which，不触发真实 OCR/转写。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types


from app.agent.context import ExecContext
from app.tools import parse_material as pm_mod
from app.tools.base import ToolInput
from app.tools.parse_material import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    ocr_available,
    parse_material,
    whisper_available,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ctx() -> ExecContext:
    return ExecContext(session_id="sess_test", trace_id="trace_test")


def _run(args: dict):
    return asyncio.run(parse_material(ToolInput(tool="parse_material", session_id="s", args=args), _ctx()))


def _tmp(suffix: str, content: bytes) -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


def _fake_ocr_modules(monkeypatch, text="识别到的文字", fail_first_lang=False):
    """注入 fake pytesseract/PIL；fail_first_lang=True 时首次按 lang 调用抛 TesseractError。"""

    class TesseractError(Exception):
        pass

    calls: list[str] = []

    def image_to_string(img, lang=None, timeout=None):
        calls.append(lang)
        if fail_first_lang and len(calls) == 1:
            raise TesseractError("lang missing")
        return text

    fake_tess = types.SimpleNamespace(image_to_string=image_to_string, TesseractError=TesseractError)

    class _Img:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_image = types.SimpleNamespace(open=lambda p: _Img())
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    monkeypatch.setitem(sys.modules, "PIL", types.SimpleNamespace(Image=fake_image))
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image)
    monkeypatch.setattr(pm_mod.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    return calls


# --- 扩展名集合与分发 ---


def test_media_exts_supported():
    assert {".png", ".jpg", ".jpeg", ".webp"} <= IMAGE_EXTS
    assert ".mp3" in AUDIO_EXTS and ".mp4" in VIDEO_EXTS
    assert IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS <= pm_mod.SUPPORTED_FILE_EXTS


def test_kind_aliases_accepted(monkeypatch):
    """kind=image/audio/video 按路径分发（kind 仅语义标注）。"""
    _fake_ocr_modules(monkeypatch)
    path = _tmp(".png", _PNG_MAGIC)
    for kind in ("file", "image"):
        out = _run({"kind": kind, "source": path})
        assert out.status == "ok", (kind, out.errors)
        assert out.data["meta"]["source_type"] == "image"


# --- 图片 OCR ---


def test_image_dependency_missing_no_binary(monkeypatch):
    """无 tesseract 二进制 -> dependency_missing + 安装提示（R9）。"""
    monkeypatch.setattr(pm_mod.shutil, "which", lambda name: None)
    out = _run({"kind": "file", "source": _tmp(".png", _PNG_MAGIC)})
    assert out.status == "error"
    assert out.errors[0].code == "dependency_missing"
    assert "tesseract" in out.errors[0].message


def test_image_ocr_ok(monkeypatch):
    _fake_ocr_modules(monkeypatch, text="你好，世界")
    out = _run({"kind": "file", "source": _tmp(".png", _PNG_MAGIC)})
    assert out.status == "ok"
    assert out.data["text"] == "你好，世界"
    assert out.data["meta"]["parser"] == "tesseract"
    assert out.data["meta"]["ocr_langs"]


def test_image_ocr_lang_fallback(monkeypatch):
    """语言包缺失（TesseractError）-> 回退 eng 重试成功。"""
    calls = _fake_ocr_modules(monkeypatch, text="hello", fail_first_lang=True)
    out = _run({"kind": "file", "source": _tmp(".jpg", b"\xff\xd8\xff" + b"\x00" * 16)})
    assert out.status == "ok"
    assert calls == ["eng+chi_sim", "eng"]  # 先配置语言，失败后 eng


def test_image_ocr_timeout(monkeypatch):
    """OCR 超时（RuntimeError）-> upstream_timeout。"""
    class TesseractError(Exception):
        pass

    def image_to_string(img, lang=None, timeout=None):
        raise RuntimeError("Tesseract process timeout")

    fake_tess = types.SimpleNamespace(image_to_string=image_to_string, TesseractError=TesseractError)

    class _Img:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    fake_image = types.SimpleNamespace(open=lambda p: _Img())
    monkeypatch.setitem(sys.modules, "PIL", types.SimpleNamespace(Image=fake_image))
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image)
    monkeypatch.setattr(pm_mod.shutil, "which", lambda name: "/usr/bin/tesseract")
    out = _run({"kind": "file", "source": _tmp(".png", _PNG_MAGIC)})
    assert out.status == "error"
    assert out.errors[0].code == "upstream_timeout"


def test_image_ocr_empty_text(monkeypatch):
    _fake_ocr_modules(monkeypatch, text="   ")
    out = _run({"kind": "file", "source": _tmp(".png", _PNG_MAGIC)})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"


def test_ocr_available_false_without_binary(monkeypatch):
    monkeypatch.setattr(pm_mod.shutil, "which", lambda name: None)
    assert ocr_available() is False


# --- 音视频转写 ---


def test_media_dependency_missing(monkeypatch):
    """无 faster-whisper -> dependency_missing + 安装提示（R9，本环境真实无此包）。"""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # 强制 import 失败
    out = _run({"kind": "file", "source": _tmp(".mp3", b"ID3" + b"\x00" * 16)})
    assert out.status == "error"
    assert out.errors[0].code == "dependency_missing"
    assert "faster-whisper" in out.errors[0].message


def test_media_duration_guard(monkeypatch):
    """ffprobe 时长超上限 -> payload_too_large（不触发转写）。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "media_max_minutes", 1)
    monkeypatch.setattr(pm_mod, "_media_duration_seconds", lambda p: 120.0)  # 2 分钟 > 1 分钟上限

    class _WM:  # 不应被实例化
        def __init__(self, *a, **kw):
            raise AssertionError("超长媒体不应触发转写")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_WM))
    out = _run({"kind": "file", "source": _tmp(".mp4", b"\x00" * 4 + b"ftyp" + b"\x00" * 16)})
    assert out.status == "error"
    assert out.errors[0].code == "payload_too_large"
    assert "超上限" in out.errors[0].message


def test_media_transcribe_ok(monkeypatch):
    """转写成功：segments 拼接 + meta（source_type/parser/language/duration）。"""
    monkeypatch.setattr(pm_mod, "_media_duration_seconds", lambda p: 65.2)

    class _Seg:
        def __init__(self, t):
            self.text = t

    class _WM:
        def __init__(self, model, device=None, compute_type=None):
            assert device == "cpu"

        def transcribe(self, p):
            return iter([_Seg(" 第一段。"), _Seg("第二段。"), _Seg("  ")]), types.SimpleNamespace(language="zh")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_WM))
    out = _run({"kind": "video", "source": _tmp(".mp4", b"\x00" * 4 + b"ftyp" + b"\x00" * 16)})
    assert out.status == "ok"
    assert out.data["text"] == "第一段。\n第二段。"
    meta = out.data["meta"]
    assert meta["source_type"] == "video"
    assert meta["parser"] == "faster-whisper"
    assert meta["language"] == "zh"
    assert meta["duration_s"] == 65.2


def test_media_transcribe_empty(monkeypatch):
    monkeypatch.setattr(pm_mod, "_media_duration_seconds", lambda p: None)

    class _WM:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, p):
            return iter([]), types.SimpleNamespace(language=None)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_WM))
    out = _run({"kind": "file", "source": _tmp(".mp3", b"ID3" + b"\x00" * 16)})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"


def test_whisper_available_false_without_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert whisper_available() is False


# --- 二进制降级护栏 ---


def test_binary_parse_failure_no_garbage_fallback(monkeypatch):
    """二进制解析异常 -> 不按文本回读产出乱码（可用性护栏），报 empty_content。"""
    _fake_ocr_modules(monkeypatch)  # tesseract 在，但 PIL open 抛异常

    class _BrokenImage:
        @staticmethod
        def open(p):
            raise ValueError("cannot identify image file")

    monkeypatch.setitem(sys.modules, "PIL", types.SimpleNamespace(Image=_BrokenImage))
    monkeypatch.setitem(sys.modules, "PIL.Image", _BrokenImage)
    out = _run({"kind": "file", "source": _tmp(".png", _PNG_MAGIC)})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"  # 而非乱码文本当正常解析结果
