"""学习方案导出增强包（后端插件，复合多 API）。

为内置导出（md/pdf/slides/ics/anki）补三种实用格式，并演示 exporters + events +
storage + routes 多扩展点在真实复合场景下的协同（装载测试见 backend/tests/
test_real_plugin_export_pack.py）。

复合 API（验证稳定性）：
- api.exporters：注册 csv / txt / opml 导出格式（内置优先安全默认对这些非内置格式直接生效）
- api.events：订阅 plan_created，累计「可导出方案」计数
- api.storage：持久化导出计数（跨重启）
- api.routes：GET /stats 查询导出统计与可用格式

实际用途（不为编写而编写）：
- csv：知识点 + 测验题导出 CSV，导入 Anki / Excel / Notion（每行一条，机器友好）
- txt：极简纯文本大纲，复制到任意编辑器 / 终端 / 笔记软件
- opml：知识图谱导出 OPML，Workflowy / Xmind / Dynalist 等思维导图软件可直接导入

所有 handler 防御性读取 plan 字段（.get），对结构变化有韧性——契合 API 稳定性承诺。
"""

from __future__ import annotations

import csv
import io
from xml.sax.saxutils import escape as _xml_escape

from fastapi import APIRouter, Depends

PLUGIN_META = {
    "id": "study_export_pack",
    "name": "学习方案导出增强包",
    "version": "0.1.0",
    "description": "csv/txt/opml 导出 + 导出统计（复合 exporters+events+storage+routes）",
    "min_api": "1.11",  # 用到 api.exporters(1.4) + api.storage(1.11)
}


def register(api):
    def _topic(plan: dict) -> str:
        intent = plan.get("intent") or {}
        return intent.get("topic") or plan.get("title") or "学习方案"

    # --- exporters：三种实用格式（sync handler，在线程池执行，不阻塞事件循环）---

    def to_csv(plan: dict, ctx: dict) -> dict:
        """知识点 + 测验题 -> CSV（两段，以 kind 列区分）。"""
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["kind", "id", "name", "difficulty", "must_learn", "answer_or_extra"])
        kmap = plan.get("knowledge_map") or {}
        for n in kmap.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            w.writerow([
                "node",
                n.get("id", ""),
                n.get("name", ""),
                n.get("difficulty", ""),
                "yes" if n.get("must_learn") else "no",
                "",
            ])
        for q in plan.get("quizzes") or []:
            if not isinstance(q, dict):
                continue
            w.writerow([
                "quiz",
                q.get("node_id", ""),
                q.get("question", ""),
                q.get("type", ""),
                "",
                q.get("answer", ""),
            ])
        return {
            "filename": f"plan-{ctx.get('plan_id', 'plan')}.csv",
            "content": out.getvalue(),
            "mime": "text/csv",
        }

    def to_txt(plan: dict, ctx: dict) -> dict:
        """阶段 + 知识点 -> 极简纯文本大纲。"""
        topic = _topic(plan)
        lines = [topic, "=" * max(8, min(len(topic), 60)), ""]
        stages = plan.get("stages") or []
        if not stages:
            lines.append("(方案未含阶段信息)")
        for s in stages:
            if not isinstance(s, dict):
                continue
            lines.append(
                f"[阶段 {s.get('index', '?')}] {s.get('name', '')}"
                f" — {s.get('goal', '')}"
            )
            for t in s.get("topics") or []:
                mark = "* " if t.get("must_learn") else "  "
                lines.append(f"{mark}- {t.get('name', '')}")
            if s.get("milestone"):
                lines.append(f"  里程碑：{s['milestone']}")
            lines.append("")
        return {
            "filename": f"plan-{ctx.get('plan_id', 'plan')}.txt",
            "content": "\n".join(lines),
            "mime": "text/plain",
        }

    def to_opml(plan: dict, ctx: dict) -> dict:
        """知识图谱 -> OPML（按 parent_id 层级嵌套，思维导图软件可读）。"""
        kmap = plan.get("knowledge_map") or {}
        nodes = [n for n in (kmap.get("nodes") or []) if isinstance(n, dict)]
        ids = {n.get("id") for n in nodes if n.get("id") is not None}
        by_parent: dict = {}
        roots: list = []
        for n in nodes:
            pid = n.get("parent_id")
            if pid is None or pid not in ids:
                roots.append(n)
            else:
                by_parent.setdefault(pid, []).append(n)

        def esc(s) -> str:
            return _xml_escape(str(s if s is not None else ""))

        lines: list[str] = []

        def walk(n: dict, depth: int) -> None:
            indent = "  " * depth
            children = by_parent.get(n.get("id"), [])
            label = esc(n.get("name"))
            if children:
                lines.append(f'{indent}<outline text="{label}">')
                for c in children:
                    walk(c, depth + 1)
                lines.append(f"{indent}</outline>")
            else:
                lines.append(f'{indent}<outline text="{label}"/>')

        for r in roots:
            walk(r, 3)

        topic = esc(_topic(plan))
        body_inner = "\n".join(lines) if lines else '      <outline text="(无知识点)"/>'
        opml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<opml version="2.0">\n'
            f"  <head><title>{topic}</title></head>\n"
            "  <body>\n"
            f'    <outline text="{topic}">\n'
            f"{body_inner}\n"
            "    </outline>\n"
            "  </body>\n"
            "</opml>\n"
        )
        return {
            "filename": f"plan-{ctx.get('plan_id', 'plan')}.opml",
            "content": opml,
            "mime": "text/x-opml",
        }

    api.exporters.register("csv", to_csv, label="CSV 表格（知识点+测验）")
    api.exporters.register("txt", to_txt, label="纯文本大纲")
    api.exporters.register("opml", to_opml, label="OPML 思维导图")

    # --- events + storage：统计可导出方案数（跨重启持久化）---
    def on_plan_created(payload: dict) -> None:
        n = api.storage.get("plan_count", 0) + 1
        api.storage.set("plan_count", n)
        api.log.info("study_export_pack: 可导出方案累计 %d", n)

    api.events.on("plan_created", on_plan_created)

    # --- routes：导出统计（默认带宿主认证）---
    router = APIRouter()

    @router.get("/stats")
    def stats(user=Depends(api.deps.get_current_user)):
        return {
            "plugin": api.id,
            "plan_count": api.storage.get("plan_count", 0),
            "formats": api.exporters.formats(),
        }

    api.routes.mount(router)
    api.log.info("study_export_pack 已加载：csv/txt/opml 导出 + 统计端点")
