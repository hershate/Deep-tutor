"""Skill 加载器（plan/05 §三）。

启动时扫描 SKILLS_DIR 下 `<skill-name>/SKILL.md`，解析 frontmatter + 正文，
构建 `skill_name -> 正文` 注册表，注入 `ExecContext.skill_registry` 供 loop 作
LLM 工具的 system prompt（稳定前缀 + cache_control，Pillar 1）。

稳定性（R13 / plan/11 §四）：
- 目录缺失 / 无 SKILL.md -> 返回空注册表（loop 回退 inline_prompt，向后兼容）。
- 单个 Skill 读取/解析失败 -> 跳过该 Skill + 告警，不影响其余，**永不抛异常**。
- frontmatter 解析逐行判定（仅整行 `---` 作开/闭），容忍 BOM 与 CRLF。

注册表键：frontmatter `name:` 优先，缺失回退目录名（与 `skills/<name>/SKILL.md`
约定一致，匹配工具的 `skill_name`）。值：剥 frontmatter 后的正文。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.settings import settings

log = logging.getLogger("deep_tutor.skills")

# 模块级缓存：lifespan 启动加载一次，_run_start 读取。事件循环线程内读写，
# 无跨线程竞态；测试经 reset_skill_registry() 隔离。
_CACHE: dict[str, str] | None = None


def load_skills(skills_dir: str | Path) -> dict[str, str]:
    """扫描 skills_dir，返回 {skill_name: 正文} 注册表。

    纯函数、无全局状态：目录缺失/无 Skill -> {}；单文件失败 -> 跳过+告警。
    """
    root = Path(skills_dir)
    if not root.is_dir():
        return {}
    registry: dict[str, str] = {}
    for skill_md in sorted(root.glob("*/SKILL.md")):  # sorted 保证确定性
        name, body = _parse_skill(skill_md)
        if not name or not body:
            continue  # _parse_skill 已告警
        if name in registry:
            log.warning("Skill 名称重复 %s（%s），后者覆盖", name, skill_md)
        registry[name] = body
    return registry


def get_skill_registry() -> dict[str, str]:
    """返回缓存的 Skill 注册表；首次调用从 settings.skills_dir 加载。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_skills(settings.skills_dir)
    return _CACHE


def reset_skill_registry() -> None:
    """清缓存（测试隔离用）。"""
    global _CACHE
    _CACHE = None


# --- 内部 ---


def _parse_skill(path: Path) -> tuple[str | None, str | None]:
    """解析单个 SKILL.md -> (name, body)。失败返回 (None, None) + 告警。"""
    try:
        raw = path.read_text(encoding="utf-8-sig")  # 容忍 BOM
    except OSError as exc:  # noqa: BLE001
        log.warning("读取 Skill 失败 %s: %s", path, exc)
        return None, None
    text = raw.replace("\r\n", "\n").replace("\r", "\n")  # CRLF 归一化
    name, body = _split_frontmatter(text)
    if not name:
        name = path.parent.name  # frontmatter 无 name -> 回退目录名
    body = body.strip()
    if not body:
        log.warning("Skill 正文为空，跳过: %s", path)
        return None, None
    return name, body


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """逐行切分 frontmatter -> (name|None, body)。

    仅整行 `---` 作开/闭标记（容忍 frontmatter 值内含 `---`）。
    无 frontmatter / 无闭合 -> (None, 全文)。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    close: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return None, text  # 无闭合，按无 frontmatter 处理
    frontmatter = "\n".join(lines[1:close])
    body = "\n".join(lines[close + 1 :])
    return _extract_name(frontmatter), body


def _extract_name(frontmatter: str) -> str | None:
    """从 frontmatter 逐行提取 `name:` 值（去首尾引号）。缺失返回 None。"""
    for line in frontmatter.split("\n"):
        s = line.strip()
        if s.startswith("name:"):
            val = s[5:].strip()
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            return val or None
    return None
