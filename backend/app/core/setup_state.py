"""首次设置状态存储（iter 89 零配置开箱即用）。

存 `data/.setup_state.json`（与 .jwt_secret 同款文件模式，零 DB 迁移）：
```json
{ "version": 1, "dismissed": false }
```
- `version` 字段 + 顶层 dict 结构为后续向导步骤（bing key、主题等）预留扩展；
- 读写永不抛异常（OSError/JSON 损坏 -> 默认值），data_dir 不可写时降级为
  「向导每次出现」，不阻塞应用；
- 写入用 tmp+rename 原子写（对齐 plan/09 §十一 约定，防半写损坏）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.settings import settings

_STATE_VERSION = 1
_DEFAULT: dict[str, Any] = {"version": _STATE_VERSION, "dismissed": False}


def _path() -> Path:
    return Path(settings.data_dir) / ".setup_state.json"


def load_state() -> dict[str, Any]:
    """读状态（损坏/缺失/不可读 -> 默认值的副本）。"""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {**_DEFAULT, **raw}
    except (OSError, ValueError):
        pass
    return dict(_DEFAULT)


def save_state(patch: dict[str, Any]) -> None:
    """合并写状态（原子写；失败仅告警不抛——dismissed 丢失仅表现为向导复现）。"""
    state = {**load_state(), **patch, "version": _STATE_VERSION}
    path = _path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logging.getLogger(__name__).warning("setup 状态写入失败（%s）：%s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def is_dismissed() -> bool:
    """用户是否已跳过/完成首次设置向导。"""
    return bool(load_state().get("dismissed"))


def mark_dismissed() -> None:
    save_state({"dismissed": True})
