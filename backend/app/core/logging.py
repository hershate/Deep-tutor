"""日志配置（plan/09 §六/§十四：结构化 + 轮转；不记 PII 与 API key）。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-5.5s %(name)s: %(message)s"
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 单文件 5MB
_LOG_BACKUP_COUNT = 5  # 保留 5 个历史文件


def _file_handler(log_dir: Path) -> RotatingFileHandler:
    """轮转文件 handler（plan/09 §六 日志轮转）：5MB × 5 份，utf-8。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        log_dir / "deep-tutor.log",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )


def setup_logging(level: str = "info") -> logging.Logger:
    """配置根日志（控制台 + 轮转文件），返回 deep_tutor 应用 logger。

    文件日志写 <data_dir>/logs/deep-tutor.log（RotatingFileHandler，plan/09 §六 日志轮转）；
    目录不可写时降级为仅控制台（不阻塞启动）。
    """
    # Windows 控制台默认 cp936，强制 UTF-8 以正确显示中文日志
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    lvl = getattr(logging, level.upper(), logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))  # 控制台：时间即可
    handlers: list[logging.Handler] = [console]
    try:
        from app.core.settings import settings

        fh = _file_handler(Path(settings.data_dir) / "logs")
        # err.md C15：文件日志跨天轮转/长期留存，须带完整日期（控制台保持简洁）
        fh.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        handlers.append(fh)
    except OSError:
        pass  # 目录不可写 -> 仅控制台

    logging.basicConfig(level=lvl, handlers=handlers)
    # 降低第三方库噪声
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("deep_tutor")
