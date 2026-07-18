"""PDF 导出（plan/03 §一 format=pdf、plan/04 §一 P1 任务、plan/01 §一 weasyprint）。

weasyprint 依赖 Pango/GObject 原生库（Windows 需另装 GTK），按 R9 模式设计为
**可选依赖**：`pdf_available()` 探测（导入可能抛 OSError 而非 ImportError），
不可用时 API 层降级 md + warning（plan/02 §11「不支持格式 -> 降级为 md」）。

链路：plan dict -> render_markdown（单一渲染源）-> markdown 库转 HTML
（纯 Python，硬依赖）-> 套打印 CSS 的完整 HTML -> weasyprint 写字节。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("deep_tutor.tools.export_pdf")

# 打印样式：A4、中文字体回退链、表格边框、标题层级（WeasyPrint 支持 @page）。
_PAGE_CSS = """
@page { size: A4; margin: 2cm; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       font-size: 11pt; line-height: 1.6; color: #1e293b; }
h1 { font-size: 20pt; border-bottom: 2px solid #3b82f6; padding-bottom: 6px; }
h2 { font-size: 15pt; margin-top: 1.2em; color: #1d4ed8; }
h3 { font-size: 12.5pt; margin-top: 1em; }
table { border-collapse: collapse; width: 100%; font-size: 10pt; }
th, td { border: 1px solid #cbd5e1; padding: 4px 8px; text-align: left; }
th { background: #eff6ff; }
ul { margin: 0.4em 0; padding-left: 1.4em; }
a { color: #2563eb; text-decoration: none; word-break: break-all; }
"""


def pdf_available() -> bool:
    """weasyprint 是否可用（R9 启动探测）。

    导入失败可能是 ImportError（未 pip 安装）或 OSError（缺 Pango 原生库，
    Windows 常见），两者都算不可用 -> API 层降级 md。
    """
    try:
        import weasyprint  # noqa: F401
    except Exception:  # noqa: BLE001  OSError/ImportError 均降级
        return False
    return True


def markdown_to_html(md: str, title: str = "学习方案") -> str:
    """Markdown -> 带打印样式的完整 HTML 文档。"""
    import markdown

    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def build_pdf(plan: dict[str, Any], md: str) -> bytes:
    """方案 Markdown -> PDF 字节。调用前须 pdf_available()，否则抛 ImportError/OSError。"""
    from weasyprint import HTML

    title = str((plan.get("intent") or {}).get("topic") or plan.get("title") or "学习方案")
    html = markdown_to_html(md, title)
    return HTML(string=html).write_pdf()
