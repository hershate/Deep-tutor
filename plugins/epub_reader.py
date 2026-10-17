"""EPUB 电子书解析器（后端插件，复合 parsers+tools+routes）。

为内置 parse_material 不支持的 .epub 格式注册解析器——上传 EPUB 电子书即可自动解析为文本
注入学习方案生成（API.md 点名的「私有文件格式处理」）。EPUB 本质是 zip，内含 XHTML 章节。

实际用途：用户上传 EPUB 技术书 / 教材 -> 解析为文本 -> 生成学习方案 / 入知识库。

复合 API（验证扩展点协同）：
- api.parsers：注册 .epub 解析器（zipfile + HTML 剥标签），上传校验与 parse_material 自动委托
- api.tools：注册 epub_reader.chapters 工具（返回章节标题列表，供其他工作流经 api.tools.call 调用）
- api.routes：GET /info 展示支持的格式

稳定性：zipfile 只 read 不 extract（无 zip-slip 落地风险）；HTML 剥标签 + 实体解码防御性；
解析失败由宿主转 parse_failed（消息清洗不回显路径）。
"""

from __future__ import annotations

import re
import zipfile
from html import unescape
from pathlib import Path

from fastapi import APIRouter, Depends

PLUGIN_META = {
    "id": "epub_reader",
    "name": "EPUB 电子书解析器",
    "version": "0.1.0",
    "description": "解析 .epub 电子书为文本（parsers+tools+routes）",
    "min_api": "1.3",  # parsers(1.3) + tools(1.0) + routes(1.0)
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _strip_html(html: str) -> str:
    """剥 HTML 标签 + 解码实体 + 块标签转换行 + 压缩空白。"""
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|br|tr)>", "\n", html)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    # 逐行 strip + 压缩连续空行
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _extract_chapters(path: Path) -> list[tuple[str, str]]:
    """从 epub 提取 [(标题, 正文)]；标题取首个 h1-h3，无则用文件名。zipfile 只 read 不 extract。"""
    # iter 369：改用公开别名——私有符号不受 API 兼容承诺保护（API.md §9.1.3），
    # 官方示例带头违反「只依赖稳定符号」契约会诱导插件作者照抄
    from app.tools.parse_material import check_zip_safety

    check_zip_safety(path)  # iter 365：zip bomb 防护（同 docx/pptx；插件 parser 路径绕过内置检查）
    chapters: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        for name in names:
            raw = zf.read(name).decode("utf-8", "replace")
            body = _strip_html(raw)
            if not body:
                continue
            m = re.search(r"(?i)<h[1-3][^>]*>(.*?)</h[1-3]>", raw, re.DOTALL)
            title = _strip_html(m.group(1)) if m else Path(name).stem
            chapters.append((title, body))
    return chapters


def parse_epub(path: Path, parse_mode: str):  # sync handler（zip CPU 密集，宿主在线程池执行）
    """EPUB 解析器：(path, parse_mode) -> (text, meta)。"""
    chapters = _extract_chapters(Path(path))
    text = "\n\n".join(f"# {title}\n\n{body}" for title, body in chapters)
    meta = {
        "source_type": "epub",
        "parser": "epub_reader",
        "pages": len(chapters),  # 章节数近似页数
        "chars": len(text),
    }
    return text, meta


def register(api):
    api.parsers.register([".epub"], parse_epub, label="EPUB 电子书", magic=b"PK")

    async def chapters_tool(inp, ctx):
        """列出 EPUB 章节标题（供其他插件/工作流经 api.tools.call 组合）。"""
        source = inp.args.get("source")
        if not source:
            return {"error": "missing source"}
        try:
            chs = _extract_chapters(Path(str(source)))
        except (FileNotFoundError, zipfile.BadZipFile, OSError) as exc:
            return {"error": str(exc)}
        return {"chapters": [t for t, _ in chs], "count": len(chs)}

    api.tools.register_python("epub_reader.chapters", chapters_tool, description="列出 EPUB 电子书的章节标题")

    router = APIRouter()

    @router.get("/info")
    def info(user=Depends(api.deps.get_current_user)):
        return {
            "plugin": api.id,
            "formats": [e for e in api.parsers.extensions() if e == ".epub"],
            "description": "解析 .epub 电子书为文本（上传 .epub 自动注入学习方案生成）",
        }

    api.routes.mount(router)
    api.log.info("epub_reader 已加载：.epub 解析器 + chapters 工具")
