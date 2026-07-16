"""日志配置。"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "info") -> logging.Logger:
    """配置根日志，返回 deep_tutor 应用 logger。"""
    # Windows 控制台默认 cp936，强制 UTF-8 以正确显示中文日志
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)-5.5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # 降低第三方库噪声
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("deep_tutor")
