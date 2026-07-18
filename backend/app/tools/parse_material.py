"""parse_material 工具（plan/02 §1）。

多模态资料 -> 结构化文本 + 元数据 + chunks。纯 Python，无 LLM。
- file: PDF(Marker) / DOCX(python-docx) / PPTX(python-pptx) / TXT / MD（按扩展名分发）
- image(P3): 图片 OCR（pytesseract + tesseract 二进制；R9：缺失 -> dependency_missing + 安装提示）
- audio/video(P3): 音视频转写（faster-whisper；R9 同上；ffprobe 时长守卫）
- text: 直接原文
- url: httpx 抓取 + 正文提取（trafilatura 优先，缺失则 stdlib HTML 转文本）
错误：unsupported_format / empty_content / degraded（降级纯文本）/ dependency_missing。
上限：解析文本 ≤ MAX_PARSED_CHARS（09 §二十），超限截断 + 标注。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.core.settings import settings
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.parse")

MAX_PARSED_CHARS = 500_000  # 09 §二十 上限（chars 近似）
CHUNK_SIZE = 3000
URL_TIMEOUT = 30.0
MARKER_TIMEOUT = 600.0  # marker --force_ocr 含 OCR，较慢，单独给长超时
MAX_URL_BYTES = 10 * 1024 * 1024  # URL 抓取正文上限 10MB（防 OOM，09 §二十）
OCR_TIMEOUT = 120.0  # 图片 OCR 超时（pytesseract timeout 参数，秒）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
SUPPORTED_FILE_EXTS = {".pdf", ".docx", ".pptx", ".txt", ".md"} | IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS


class _ParseError(Exception):
    """携带 code 的解析异常。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def parse_material(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    args = inp.args
    kind = args.get("kind")
    source = args.get("source", "")

    if kind not in {"file", "text", "url", "image", "audio", "video"}:
        return _err("unsupported_format", f"不支持的 kind: {kind}（支持 file/text/url/image/audio/video）")

    try:
        if kind == "text":
            text, meta = _parse_text(source)
        elif kind == "url":
            text, meta = await _parse_url(source)
        else:
            # file / image / audio / video：均按路径 + 扩展名分发（kind 仅语义标注）
            text, meta = await asyncio.to_thread(_parse_file, source)
    except _ParseError as e:
        return _err(e.code, e.message)
    except Exception as exc:  # noqa: BLE001  降级为纯文本提取
        log.warning("parse_material %s 失败，降级: %s", kind, exc)
        text, meta = _degraded_fallback(source, kind, exc)

    if not text or not text.strip():
        return _err("empty_content", "解析文本为空（可能是扫描件/密码 PDF/空文件）")

    truncated = False
    if len(text) > MAX_PARSED_CHARS:
        text = text[:MAX_PARSED_CHARS]
        truncated = True
    meta = {**meta, "chars": len(text)}
    if truncated:
        meta["truncated"] = True

    return ToolOutput(
        tool="parse_material",
        status="ok",
        data={"text": text, "meta": meta, "chunks": _chunk(text)},
    )


# --- 各类型解析 ---

# zip bomb 防护（R23，plan/09 §四）：DOCX/PPTX 是 zip——读中央目录（不解压）
# 校验解压后总大小与条目数，超限拒绝（防 10MB 压缩包展开成 GB 级耗尽内存）。
_ZIP_MAX_UNCOMPRESSED = 512 * 1024 * 1024  # 512MB
_ZIP_MAX_ENTRIES = 10_000


def _check_zip_safety(path: Path) -> None:
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise _ParseError("parse_failed", f"损坏的 {path.suffix} 文件（非合法 zip 容器）") from exc
    if len(infos) > _ZIP_MAX_ENTRIES:
        raise _ParseError("payload_too_large", f"压缩包条目数 {len(infos)} 超上限 {_ZIP_MAX_ENTRIES}")
    total = sum(i.file_size for i in infos)
    if total > _ZIP_MAX_UNCOMPRESSED:
        raise _ParseError(
            "payload_too_large",
            f"解压后总大小 {total / 1024 / 1024:.0f}MB 超上限 {_ZIP_MAX_UNCOMPRESSED / 1024 / 1024:.0f}MB",
        )


def _parse_text(source: str) -> tuple[str, dict]:
    return source, {"source_type": "text"}


def _parse_file(source: str) -> tuple[str, dict]:
    path = Path(source)
    if not path.exists():
        raise _ParseError("unsupported_format", f"文件不存在: {source}")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_FILE_EXTS:
        raise _ParseError(
            "unsupported_format", f"不支持的扩展名 {ext}（支持 {sorted(SUPPORTED_FILE_EXTS)}）"
        )
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        _check_zip_safety(path)
        return _parse_docx(path)
    if ext == ".pptx":
        _check_zip_safety(path)
        return _parse_pptx(path)
    if ext in IMAGE_EXTS:
        return _parse_image(path)
    if ext in AUDIO_EXTS | VIDEO_EXTS:
        return _parse_media(path)
    # .txt / .md
    text = _read_text_file(path)
    return text, {"source_type": ext.lstrip("."), "pages": 1}


# --- 图片 OCR（P3，pytesseract；R9：依赖缺失 -> dependency_missing + 安装提示）---


def ocr_available() -> bool:
    """图片 OCR 依赖是否可用（tesseract 二进制 + pytesseract/Pillow）。R9 启动检测用。"""
    if shutil.which("tesseract") is None:
        return False
    try:
        import PIL.Image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _parse_image(path: Path) -> tuple[str, dict]:
    """图片 OCR：tesseract 二进制 + pytesseract。语言包缺失回退 eng；超时 -> upstream_timeout。"""
    if shutil.which("tesseract") is None:
        raise _ParseError(
            "dependency_missing",
            "图片 OCR 依赖 tesseract（未在 PATH 找到；请安装 tesseract-ocr 后重试）",
        )
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise _ParseError(
            "dependency_missing",
            "图片 OCR 依赖 pytesseract/Pillow（pip install pytesseract Pillow）",
        ) from exc

    langs = settings.ocr_languages or "eng"
    with Image.open(str(path)) as img:
        try:
            text = pytesseract.image_to_string(img, lang=langs, timeout=OCR_TIMEOUT)
        except RuntimeError as exc:  # pytesseract 超时抛 RuntimeError
            raise _ParseError("upstream_timeout", f"图片 OCR 超时（{OCR_TIMEOUT}s）") from exc
        except pytesseract.TesseractError:
            # 语言包缺失等 -> 回退 eng 重试一次
            try:
                text = pytesseract.image_to_string(img, lang="eng", timeout=OCR_TIMEOUT)
            except RuntimeError as exc:
                raise _ParseError("upstream_timeout", f"图片 OCR 超时（{OCR_TIMEOUT}s）") from exc
    text = (text or "").strip()
    if not text:
        raise _ParseError("empty_content", "图片 OCR 未识别到文字")
    return text, {"source_type": "image", "parser": "tesseract", "ocr_langs": langs}


# --- 音视频转写（P3，faster-whisper；R9：依赖缺失 -> dependency_missing + 安装提示）---

_WHISPER_COMPUTE_TYPE = "int8"  # CPU 友好（本地单用户默认无 GPU）
_FFPROBE_TIMEOUT = 30.0


def whisper_available() -> bool:
    """音视频转写依赖是否可用（faster-whisper）。R9 启动检测用。"""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _media_duration_seconds(path: Path) -> float | None:
    """ffprobe 取媒体时长（秒）；ffprobe 缺失/探测失败 -> None（跳过时长守卫，降级不阻断）。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT,
        )
        return float((proc.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return None


def _parse_media(path: Path) -> tuple[str, dict]:
    """音视频转写：faster-whisper 本地模型（CPU/int8）。

    时长守卫：ffprobe 可得且 > settings.media_max_minutes -> 拒绝（防长时阻塞/CPU 耗尽）。
    首次使用自动下载模型（HuggingFace）；模型大小经 settings.whisper_model 配置。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise _ParseError(
            "dependency_missing",
            "音视频转写依赖 faster-whisper（pip install faster-whisper；首次使用自动下载模型）",
        ) from exc

    duration = _media_duration_seconds(path)
    if duration is not None and duration > settings.media_max_minutes * 60:
        raise _ParseError(
            "payload_too_large",
            f"音视频时长 {duration / 60:.1f} 分钟超上限 {settings.media_max_minutes} 分钟",
        )

    source_type = "video" if path.suffix.lower() in VIDEO_EXTS else "audio"
    model = WhisperModel(settings.whisper_model, device="cpu", compute_type=_WHISPER_COMPUTE_TYPE)
    segments, info = model.transcribe(str(path))
    parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
    text = "\n".join(parts).strip()
    if not text:
        raise _ParseError("empty_content", "音视频转写未识别到语音文本")
    meta: dict = {
        "source_type": source_type,
        "parser": "faster-whisper",
        "whisper_model": settings.whisper_model,
        "language": getattr(info, "language", None),
    }
    if duration is not None:
        meta["duration_s"] = round(duration, 1)
    return text, meta


def _parse_pdf(path: Path) -> tuple[str, dict]:
    """PDF 解析用 Marker（marker_single CLI，--force_ocr）。

    按用户指示：`marker_single <文件> --force_ocr --output_dir <输出目录>`，默认参数。
    输出结构：<output_dir>/<stem>/<stem>.md + <stem>_meta.json。
    """
    marker_bin = shutil.which("marker_single")
    if not marker_bin:
        raise _ParseError(
            "unsupported_format",
            "PDF 解析依赖 marker_single（pip install marker-pdf），但未在 PATH 找到",
        )
    stem = path.stem
    with tempfile.TemporaryDirectory(prefix="marker_") as out_dir:
        cmd = [marker_bin, str(path), "--force_ocr", "--output_dir", out_dir]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=MARKER_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise _ParseError("upstream_timeout", f"marker_single 超时（{MARKER_TIMEOUT}s）") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "")[:500]
            raise _ParseError("degraded", f"marker_single 失败（returncode={proc.returncode}）: {stderr}")

        md_path = Path(out_dir) / stem / f"{stem}.md"
        if not md_path.exists():
            raise _ParseError("empty_content", f"marker 未产出 markdown: {md_path}")
        text = md_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise _ParseError("empty_content", "marker 产出空文本")
        pages = _marker_page_count(Path(out_dir) / stem / f"{stem}_meta.json")
        return text, {"source_type": "pdf", "pages": pages, "parser": "marker"}


def _marker_page_count(meta_path: Path) -> int:
    """从 marker 的 _meta.json 取页数（page_stats 长度）。"""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return len(data.get("page_stats") or [])
    except Exception:  # noqa: BLE001
        return 0


def _parse_docx(path: Path) -> tuple[str, dict]:
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs).strip()
    return text, {"source_type": "docx", "pages": 1}


def _parse_pptx(path: Path) -> tuple[str, dict]:
    from pptx import Presentation

    prs = Presentation(str(path))
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs)
                    if line:
                        texts.append(line)
    text = "\n".join(texts).strip()
    return text, {"source_type": "pptx", "pages": len(prs.slides)}


def _read_text_file(path: Path) -> str:
    """读取文本文件，自动检测编码（R25）。"""
    return _decode_best(path.read_bytes())


def _decode_best(raw: bytes) -> str:
    """字节 -> 文本：优先 charset_normalizer 检测，兜底 utf-8/gbk/latin-1。

    供文件与 URL 抓取复用，保证编码检测一致。
    """
    if not raw:
        return ""
    try:
        from charset_normalizer import from_bytes

        result = from_bytes(raw).best()
        if result is not None:
            return str(result)
    except Exception:  # noqa: BLE001
        pass
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def _collect_capped(aiter, max_bytes: int) -> tuple[bytes, bool]:
    """从异步字节流收集至多 max_bytes；返回 (content, too_large)。

    逐块累加，某块会令总量超 max_bytes 即停（不并入该块），内存上界 ≈ max_bytes + 单块。
    供 URL 抓取限量读取防 OOM。
    """
    chunks: list[bytes] = []
    total = 0
    too_large = False
    async for chunk in aiter:
        if total + len(chunk) > max_bytes:
            too_large = True
            break
        total += len(chunk)
        chunks.append(chunk)
    return b"".join(chunks), too_large


async def _parse_url(source: str) -> tuple[str, dict]:
    import httpx

    if not _is_http_url(source):
        raise _ParseError("unsupported_format", "URL 非法（需 http/https）")
    # SSRF 防护（R19）：复用 verify_url 的安全判定
    from app.tools.verify_url import _is_safe_url

    safe, reason = _is_safe_url(source)
    if not safe:
        raise _ParseError("ssrf_blocked", f"URL 被安全策略拒绝: {reason}")
    # 流式抓取 + 限量读取（防超大响应 OOM）
    html = ""
    final_url = ""
    status = 0
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(URL_TIMEOUT)
        ) as client:
            async with client.stream("GET", source) as resp:
                resp.raise_for_status()
                raw, too_large = await _collect_capped(resp.aiter_bytes(), MAX_URL_BYTES)
                if too_large:
                    raise _ParseError(
                        "payload_too_large", f"URL 正文超上限 {MAX_URL_BYTES // (1024 * 1024)}MB"
                    )
                html = _decode_best(raw)
                final_url = str(resp.url)
                status = resp.status_code
    except _ParseError:
        raise
    except httpx.HTTPError as exc:
        raise _ParseError("degraded", f"URL 抓取失败: {exc}") from exc
    text = _html_to_text(html)
    return text, {"source_type": "url", "final_url": final_url, "status": status}


# --- 辅助 ---


def _degraded_fallback(source: str, kind: str, exc: Exception) -> tuple[str, dict]:
    """解析失败的降级：尽量取纯文本，标记 degraded。

    二进制格式（PDF/DOCX/PPTX/图片/音视频）不按文本回读——那会产出乱码垃圾文本
    注入下游 LLM（可用性护栏）；返回空串 -> 上层 empty_content 明确报错。
    """
    if kind in {"file", "image", "audio", "video"}:
        ext = Path(source).suffix.lower()
        if ext in SUPPORTED_FILE_EXTS - {".txt", ".md"}:
            return "", {"source_type": "degraded", "error": str(exc)}
        try:
            return _read_text_file(Path(source)), {"source_type": "degraded", "error": str(exc)}
        except Exception:  # noqa: BLE001
            return "", {"source_type": "degraded", "error": str(exc)}
    return str(source), {"source_type": "degraded", "error": str(exc)}


def _chunk(text: str, size: int = CHUNK_SIZE) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


def _is_http_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s, re.IGNORECASE))


class _TextExtractor(HTMLParser):
    """极简 HTML -> 纯文本（trafilatura 缺失时的兜底）。"""

    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            s = data.strip()
            if s:
                self._parts.append(s)

    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(html)
        if extracted and extracted.strip():
            return extracted
    except Exception:  # noqa: BLE001  trafilatura 可选
        pass
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _err(code: str, message: str) -> ToolOutput:
    return ToolOutput(
        tool="parse_material",
        status="error",
        errors=[ToolError(code=code, message=message)],
    )


# Tool 实例（注册到 registry 用）
parse_material_tool = Tool(name="parse_material", llm=False, func=parse_material)
