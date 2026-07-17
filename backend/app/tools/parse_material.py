"""parse_material 工具（plan/02 §1）。

多模态资料 -> 结构化文本 + 元数据 + chunks。纯 Python，无 LLM。
- file: PDF(PyMuPDF) / DOCX(python-docx) / PPTX(python-pptx) / TXT / MD（按扩展名分发）
- text: 直接原文
- url: httpx 抓取 + 正文提取（trafilatura 优先，缺失则 stdlib HTML 转文本）
错误：unsupported_format / empty_content / degraded（降级纯文本）。
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
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.parse")

MAX_PARSED_CHARS = 500_000  # 09 §二十 上限（chars 近似）
CHUNK_SIZE = 3000
URL_TIMEOUT = 30.0
MARKER_TIMEOUT = 600.0  # marker --force_ocr 含 OCR，较慢，单独给长超时
SUPPORTED_FILE_EXTS = {".pdf", ".docx", ".pptx", ".txt", ".md"}


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

    if kind not in {"file", "text", "url"}:
        return _err("unsupported_format", f"不支持的 kind: {kind}（支持 file/text/url）")

    try:
        if kind == "text":
            text, meta = _parse_text(source)
        elif kind == "url":
            text, meta = await _parse_url(source)
        else:
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
        return _parse_docx(path)
    if ext == ".pptx":
        return _parse_pptx(path)
    # .txt / .md
    text = _read_text_file(path)
    return text, {"source_type": ext.lstrip("."), "pages": 1}


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
    raw = path.read_bytes()
    if not raw:
        return ""
    try:
        from charset_normalizer import from_bytes

        result = from_bytes(raw).best()
        if result is not None:
            return str(result)
    except Exception:  # noqa: BLE001
        pass
    # 兜底 UTF-8 / GBK
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def _parse_url(source: str) -> tuple[str, dict]:
    import httpx

    if not _is_http_url(source):
        raise _ParseError("unsupported_format", "URL 非法（需 http/https）")
    # SSRF 防护（R19）：复用 verify_url 的安全判定
    from app.tools.verify_url import _is_safe_url

    safe, reason = _is_safe_url(source)
    if not safe:
        raise _ParseError("ssrf_blocked", f"URL 被安全策略拒绝: {reason}")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(URL_TIMEOUT)
        ) as client:
            resp = await client.get(source)
            resp.raise_for_status()
    except _ParseError:
        raise
    except httpx.HTTPError as exc:
        raise _ParseError("degraded", f"URL 抓取失败: {exc}") from exc
    html = resp.text
    final_url = str(resp.url)
    text = _html_to_text(html)
    return text, {"source_type": "url", "final_url": final_url, "status": resp.status_code}


# --- 辅助 ---


def _degraded_fallback(source: str, kind: str, exc: Exception) -> tuple[str, dict]:
    """解析失败的降级：尽量取纯文本，标记 degraded。"""
    if kind == "file":
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
