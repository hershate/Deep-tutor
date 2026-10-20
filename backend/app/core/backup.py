"""用户数据备份导出/导入（iter 93，用户需求：导出能再导入回去方便备份）。

导出（build_export）：当前用户全部学习资产打包为单个自包含 JSON——
会话 / 资料（解析文本内联）/ 方案（content 内联，含溢出文件回读）/ 知识节点树 /
进度 / 打卡 / 测验（含作答记录）/ 自学材料（md 内联，拆分子文件自动串联）/
用户偏好 KV（iter 177：日预算等覆盖项）。
**凭证边界**：api_key 等敏感字段绝不导出——供应商档案以空 key 导出，
导入后在设置页补填并激活。

iter 369 R6 备份覆盖面闭合（version 2）：v1 只导出会话族，threads/messages/runs/
memories/kb_documents 五类表及 kb 原件文件不在备份内——恢复后这些数据直接丢失
且无提示（数据完整性缺口）。v2 新增顶层键 threads/memories/kb_documents（v1 旧备份
无该键 -> 导入侧按空处理，往返兼容）。kb 原件以 base64 内联（单文件 ≤32MB，
超限跳过并记 skipped——与上传 50MB 上限同量级但留 JSON 体积余量）。

导入（restore_backup）：**追加式还原**——全部以新行重建（不覆盖现有数据，
重复导入产生副本，UI 已提示）；外键全量重映射，知识节点树/进度/测验/单元
按节点 sid 重关联；大内容经 app.core.storage 重新落盘（新 session/unit id 命名）。
单事务：任何硬错误整体回滚（端点负责），不留半截数据。
格式带 format/version 头：后续版本演进时按 version 分派迁移逻辑。

资源边界（网络服务暴露后的硬约束）：
- **总行数预算**：单次导入创建的总行数受 _MAX_TOTAL_ROWS 限制——此前配额全按父级
  计（每会话 50 方案 × 每方案 2000 打卡 …），一个 256MB 请求即可在单事务内创建
  千万级行，独占 SQLite 写锁数分钟，其间全服务写请求 busy_timeout 超时 500。
  超预算的 payload 直接拒绝（413），不做部分导入——避免"导了一半"的歧义状态。
- **导出字节预算**：内联 kb 原件的累计字节受 _MAX_EXPORT_BYTES 限制，超出部分跳过
  并在导出结果中显式声明（truncated），防重度用户单次导出把进程撑爆。
- **文件补偿**：restore 期间落盘的文件被登记，事务失败时由端点删除——DB 回滚不回滚
  磁盘，kb 原件孤儿不会被 maintenance 回收（maintenance 只扫 data/sessions/）。
- **覆盖面披露**：未纳入备份的表在导出的 not_backed_up 中显式列出，避免"全部学习数据"
  的表述与实现不符。
"""

from __future__ import annotations

import base64
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session as DbSession

from app.core.settings import settings
from app.core.storage import (
    read_unit_content,
    save_kb_upload,
    write_kb_parsed_text,
    write_parsed_text,
    write_unit_content,
)
from app.db import models

if TYPE_CHECKING:  # pragma: no cover
    from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger(__name__)

FORMAT = "deep-tutor-backup"
VERSION = 2

# iter 369 R6：kb 原件 base64 内联的单文件上限（字节，解码前以原文长度近似估算）
_KB_ORIGINAL_MAX_BYTES = 32 * 1024 * 1024

# 与 app.api.plans.PLAN_OVERFLOW_BYTES 保持一致（plan/01 §五：>512KB 溢出文件）；
# 不跨层 import api 模块，故在此冗余定义（稳定常量，变更概率极低）
_PLAN_OVERFLOW_BYTES = 512 * 1024

# 单次导入的总行数预算（全局，非每父级）。正常用户的真实备份远低于此量级
# （200 会话 × 3 方案 × 100 打卡 ≈ 6 万行已属重度）；超出即视为构造的放大 payload，
# 直接拒绝而非部分导入——单事务内的百万级插入会独占 SQLite 写锁导致全服 500。
_MAX_TOTAL_ROWS = 100_000

# 单次导出的内联字节总预算（kb 原件 base64 之前的原始字节）。与导入侧 256MB 上限
# 对称：重度用户可持有 200 × 32MB 原件，内联后 base64 约 8.5GB，会把 worker 撑爆。
_MAX_EXPORT_BYTES = 256 * 1024 * 1024

# 备份字段整数的钳制区间（32 位有符号）——SQLite INTEGER 虽为 64 位，
# 但 Python sqlite3 绑定超 2**63-1 会抛 OverflowError；本仓 schema 无字段需超 32 位
_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1

# 未纳入备份的表（导出结果 not_backed_up 显式列出，导入响应同样回传）。# 口径：备份覆盖"学习资产本体"，审计流水/计量台账/分享链接等不在其中——
# 此前既不导出也不声明，"导出全部学习数据"的表述与实现不符。
_NOT_BACKED_UP = [
    "orchestrator_state",  # 编排器状态机（运行态，无归档价值）
    "tool_calls",  # 生成流水线工具调用审计
    "api_usage",  # 成本计量台账（日预算从零重算）
    "run_steps",  # Agent 每轮明细
    "run_artifacts",  # 工作区产物登记
    "kb_chat_records",  # 知识库问答记录
    "share_links",  # 方案分享链接（撤销后不可复原）
    "credential_profiles",  # 凭证档案（密钥边界：绝不导出）
]

# ---------------------------------------------------------------------------
# 导入配额（模块级常量）
# ---------------------------------------------------------------------------
# restore 与 preview 共用同一套数字：此前配额是各函数内的局部变量，preview 只能
# 数 payload 条目、restore 才知道实际落库条数——两端口径不一致，界面预检显示
# "250 个会话"而导入只落地 200 个（额度被本机已有数据占用），用户无从预期。
# 提升为模块级常量后，preview 按同一配额计算"实际会导入多少"。

_MAX_SESSIONS = 200
_MAX_MATERIALS_PER_SESSION = 20
_MAX_NOTES = 500
_MAX_PLANS_PER_SESSION = 50
# iter 396：还原侧的对话会话上限此前是 200，而在线 API 是 500（memory.py 的
# _MAX_THREADS_PER_USER）——拥有 200~500 个 thread 的用户做「导出→导入」往返会
# 静默丢 thread（只在 summary.skipped 留一行）。按 API 口径对齐为 500。
_MAX_THREADS = 500
_MAX_RUNS_PER_THREAD = 2000
_MAX_MSGS_PER_THREAD = 5000
_MAX_MEMORIES = 2000
_MAX_MEM_CHARS = 20_000
_MAX_KB_DOCS = 200
_MAX_PREFS = 200
_MAX_PROFILES = 50
_MAX_NODES, _MAX_RESOURCES, _MAX_PROGRESS = 100, 200, 500
_MAX_LOGS, _MAX_QUIZZES, _MAX_ATTEMPTS, _MAX_UNITS = 2000, 200, 100, 100


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(v: Any) -> datetime | None:
    """解析 ISO 时间戳；带时区偏移的归一到 naive UTC。

    SQLite 的 DateTime 列只存 naive 分量：直接写 tz-aware 值会被 SQLAlchemy
    绑定器静默丢掉偏移——`2026-01-01T08:00+08:00`（UTC 零点）被存成
    `2026-01-01 08:00:00`，全库时间线平移 8 小时（打卡日期落到错误的日历日）。
    本仓导出的时间戳本身是 naive UTC，故只影响外部来源的备份文件。
    """
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_date(v: Any) -> date | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _read_text(path: str | None) -> str | None:
    """读文件文本；路径空/读失败 -> None（导出尽量完整，单文件缺失不阻塞）。"""
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        log.warning("备份导出：文件读取失败 %s", path)
        return None


def _load_plan_content(plan: models.Plan) -> dict:
    """plan 内容（大方案从 content_path 溢出文件回读）；读不到返 {}。"""
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("备份导出：plan %s 溢出文件读取失败", plan.id)
            content = None
    return content if isinstance(content, dict) else {}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def build_export(db: DbSession, user: models.User) -> dict:
    """组装当前用户的完整备份 dict（不落盘，由端点序列化为下载响应）。"""
    sessions_out: list[dict] = []
    sessions = db.query(models.Session).filter_by(user_id=user.id).order_by(models.Session.id).all()
    for sess in sessions:
        materials_out = [
            {
                "kind": m.kind.value,
                "source": m.source,
                # 材料三段制状态（pending/confirmed/parsed）必须随备份走：此前导出
                # 丢弃该字段，导入侧按列默认落 "parsed"——未确认的上传件被还原成
                # "已解析"，确认动作不可达，且 parsed 却无解析文本。
                "status": m.status,
                "parsed_text": _read_text(m.parsed_text_path),
                "parsed_chars": m.parsed_chars,
                "meta": m.meta or {},
                "content_hash": m.content_hash,
                "created_at": _iso(m.created_at),
            }
            for m in sess.materials
        ]
        plans_out = [_export_plan(db, p, user.id) for p in sess.plans]
        sessions_out.append(
            {
                "title": sess.title,
                "created_at": _iso(sess.created_at),
                "materials": materials_out,
                "plans": plans_out,
            }
        )

    profiles_out = [
        {  # api_key 绝不导出（模块 docstring 凭证边界）
            "name": p.name,
            "base_url": p.base_url,
            "model": p.model,
            "kind": p.kind or "anthropic",  # iter 313 协议（导出供导入往返）
            "is_active": bool(p.is_active),
        }
        for p in db.query(models.LlmProviderProfile).filter_by(user_id=user.id).all()
    ]

    prefs_out = {  # 用户偏好 KV（iter 177）：日预算等覆盖项随备份走
        r.key: r.value for r in db.query(models.UserPref).filter_by(user_id=user.id).all()
    }

    notes_out = [  # 独立自由笔记（iter 227）：顶层 notes 键，v1 兼容（旧备份无该键不还原）
        {"title": n.title, "content": n.content, "updated_at": _iso(n.updated_at)}
        for n in db.query(models.Note).filter_by(user_id=user.id).order_by(models.Note.id).all()
    ]

    # --- iter 369 R6（version 2）：threads/messages/runs + memories + kb_documents ---
    # threads 与 messages/runs 内部以列表下标互关联（导入侧按下标重映射新 id）；
    # thread.session_id（plan-gen overlay 桥接）不导出——会话经追加式还原得到新 id，
    # 跨族重映射引入序耦合，且该链接仅是导航便利，缺省 None 不影响数据本体。
    user_threads = db.query(models.Thread).filter_by(user_id=user.id).order_by(models.Thread.id).all()
    thread_id_to_idx = {t.id: i for i, t in enumerate(user_threads)}
    threads_out: list[dict] = []
    for t in user_threads:
        thread_runs = db.query(models.Run).filter_by(thread_id=t.id).order_by(models.Run.id).all()
        run_id_to_idx = {r.id: i for i, r in enumerate(thread_runs)}
        runs_out = [
            {
                "kind": r.kind.value,
                # 非终态（running/awaiting_approval）收敛为 interrupted——恢复的 run
                # 无工作区/事件流可续，保留原态会让前端显示「运行中」但永远等不到事件
                "status": r.status.value
                if r.status.value in ("done", "error", "interrupted", "cancelled")
                else "interrupted",
                "step_count": r.step_count,
                "budget_used": r.budget_used,
                "trace_id": r.trace_id,
                "error": r.error or {},
                "started_at": _iso(r.started_at),
                "ended_at": _iso(r.ended_at),
            }
            for r in thread_runs
        ]
        messages_out = [
            {
                "role": m.role.value,
                "content": m.content if isinstance(m.content, list) else [],
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "model": m.model,
                # 关联 run 以下标表达（导入侧先建 runs 再按下标回填 run_id）
                "run_idx": run_id_to_idx.get(m.run_id),
                "created_at": _iso(m.created_at),
            }
            for m in db.query(models.Message).filter_by(thread_id=t.id).order_by(models.Message.id).all()
        ]
        threads_out.append(
            {
                "title": t.title,
                "kind": t.kind,
                "model": t.model,
                "created_at": _iso(t.created_at),
                "updated_at": _iso(t.updated_at),
                "runs": runs_out,
                "messages": messages_out,
            }
        )

    memories_out = [
        {
            "scope": m.scope,
            "kind": m.kind,
            "content": m.content,
            "meta": m.meta or {},
            "created_at": _iso(m.created_at),
            "updated_at": _iso(m.updated_at),
            # thread 关联以下标表达（scope=user 恒 None）
            "thread_idx": thread_id_to_idx.get(m.thread_id),
        }
        for m in db.query(models.Memory).filter_by(user_id=user.id).order_by(models.Memory.id).all()
    ]

    kb_docs_out: list[dict] = []
    inlined_bytes = 0
    truncated_originals = 0
    for d in db.query(models.KbDocument).filter_by(user_id=user.id).order_by(models.KbDocument.id).all():
        original_b64: str | None = None
        if d.original_path:
            try:
                raw = Path(d.original_path).read_bytes()
            except OSError:
                log.warning("备份导出：kb 原件读取失败 %s", d.original_path)
                raw = None
            if raw is not None:
                if len(raw) > _KB_ORIGINAL_MAX_BYTES:
                    log.warning(
                        "备份导出：kb 原件超 %dMB 上限跳过内联 %s",
                        _KB_ORIGINAL_MAX_BYTES // 1024 // 1024,
                        d.original_path,
                    )
                    truncated_originals += 1
                elif inlined_bytes + len(raw) > _MAX_EXPORT_BYTES:
                    # 导出总字节预算用尽：跳过剩余原件（显式计数，界面可提示）
                    truncated_originals += 1
                else:
                    inlined_bytes += len(raw)
                    original_b64 = base64.b64encode(raw).decode("ascii")
        kb_docs_out.append(
            {
                "title": d.title,
                "kind": d.kind.value,
                "ext": d.ext,
                "source": d.source,
                "parsed_text": _read_text(d.parsed_text_path),
                "parsed_chars": d.parsed_chars,
                "meta": d.meta or {},
                "content_hash": d.content_hash,
                "status": d.status,
                "created_at": _iso(d.created_at),
                "updated_at": _iso(d.updated_at),
                "original_b64": original_b64,
            }
        )

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),  # 与全库 UTC 约定一致（其余时间戳均 func.now()/UTC；本地时间仅用于人类可读导出标题/文件名）
        "user": {"username": user.username or "", "name": user.name},
        "provider_profiles": profiles_out,
        "prefs": prefs_out,
        "notes": notes_out,
        "sessions": sessions_out,
        "threads": threads_out,
        "memories": memories_out,
        "kb_documents": kb_docs_out,
        # 覆盖面披露：未纳入备份的表显式列出（导入/预检同样回传）——
        # 此前既不导出也不声明，"导出全部学习数据"的表述与实现不符
        "not_backed_up": list(_NOT_BACKED_UP),
        # 导出因预算被跳过的 kb 原件数（0 表示完整内联）
        "truncated_originals": truncated_originals,
    }


def _export_plan(db: DbSession, plan: models.Plan, user_id: int) -> dict:
    nodes = db.query(models.KnowledgeNode).filter_by(plan_id=plan.id).order_by(models.KnowledgeNode.id).all()
    id_to_sid = {n.id: n.sid for n in nodes}
    # 知识点笔记（iter 105）：node_id -> content（仅当前用户；空内容不出现在导出）
    note_map: dict[int, str] = {}
    if nodes:
        note_map = {
            row.node_id: row.content
            for row in db.query(models.NodeNote)
            .filter(
                models.NodeNote.user_id == user_id,
                models.NodeNote.node_id.in_(id_to_sid.keys()),
            )
            .all()
            if row.content
        }
    nodes_out = [
        {
            "sid": n.sid,
            "name": n.name,
            "difficulty": n.difficulty,
            "must_learn": bool(n.must_learn),
            "parent_sid": id_to_sid.get(n.parent_id) if n.parent_id else None,
            **({"note": note_map[n.id]} if n.id in note_map else {}),
        }
        for n in nodes
    ]
    resources_out = [
        {
            "node_sid": id_to_sid.get(r.node_id) if r.node_id else None,
            "url": r.url,
            "title": r.title,
            "type": r.type.value,
            "verified": r.verified.value,
            "note": r.note,
        }
        for r in db.query(models.Resource)
        .join(models.KnowledgeNode, models.Resource.node_id == models.KnowledgeNode.id)
        .filter(models.KnowledgeNode.plan_id == plan.id)
        .all()
    ]
    progress_out = [
        {
            "node_sid": id_to_sid.get(p.node_id),
            "status": p.status.value,
            "mastery": p.mastery,
        }
        for p in db.query(models.Progress).filter_by(plan_id=plan.id).all()
        if p.node_id in id_to_sid  # 孤儿进度行（节点已删）无重关联锚点，跳过
    ]
    logs_out = [
        {"date": _iso(lg.date), "minutes": lg.minutes, "note": lg.note}
        for lg in db.query(models.LearningLog).filter_by(plan_id=plan.id).all()
    ]
    quizzes_out = [
        {
            "node_sid": id_to_sid.get(q.node_id) if q.node_id else None,
            "type": q.type.value,
            "payload": q.payload or {},
            "attempts": [
                {
                    "user_answer": a.user_answer or {},
                    "correct": a.correct,
                    "created_at": _iso(a.created_at),
                }
                for a in q.attempts
            ],
        }
        for q in db.query(models.Quiz).filter_by(plan_id=plan.id).all()
    ]
    units_out = []
    for u in db.query(models.StudyUnit).filter_by(plan_id=plan.id).order_by(models.StudyUnit.order).all():
        content_md: str | None = None
        mat_resources: list = []
        if u.materials:
            mat = u.materials[0]
            try:
                content_md = read_unit_content(mat.content_md_path)  # 自动串联拆分子文件
            except OSError:
                log.warning("备份导出：单元 %s 内容文件缺失 %s", u.id, mat.content_md_path)
            mat_resources = mat.resources or []
        units_out.append(
            {
                "node_sid": id_to_sid.get(u.node_id) if u.node_id else None,
                # iter 367：node_sids（iter 348 单元覆盖知识点）此前漏导——往返后
                # scope.node_ids 局部重生成/按单元重生成静默失效
                "node_sids": [str(s) for s in (u.node_sids or [])],
                "stage_index": u.stage_index,
                "title": u.title,
                "prerequisites": u.prerequisites or [],
                "objectives": u.objectives or [],
                "estimated_time": u.estimated_time,
                "order": u.order,
                "done_at": _iso(u.done_at),
                "content_md": content_md,
                "material_resources": mat_resources,
            }
        )
    return {
        "version": plan.version,
        "created_at": _iso(plan.created_at),
        "content": _load_plan_content(plan),
        "knowledge_nodes": nodes_out,
        "resources": resources_out,
        "progress": progress_out,
        "learning_logs": logs_out,
        "quizzes": quizzes_out,
        "study_units": units_out,
    }


# ---------------------------------------------------------------------------
# 导入（追加式还原）
# ---------------------------------------------------------------------------


class BackupFormatError(ValueError):
    """备份文件结构/版本不合法（端点转 400）。"""


class BackupTooLargeError(BackupFormatError):
    """备份 payload 超出单次导入的总行数预算（端点转 413）。

    与 BackupFormatError 分开：结构非法是"文件不对"（400），行数超预算是
    "文件太大"（413），前端可据此区分提示（拆小备份 vs 换个文件）。
    """


def validate_backup(data: Any) -> dict:
    """校验备份顶层结构；不合法抛 BackupFormatError。返回 data（dict）。"""
    if not isinstance(data, dict):
        raise BackupFormatError("备份文件须为 JSON 对象")
    if data.get("format") != FORMAT:
        raise BackupFormatError("不是 Deep-tutor 备份文件（format 字段缺失或不匹配）")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise BackupFormatError("备份文件 version 字段缺失或非法")
    if version > VERSION:
        raise BackupFormatError(f"备份版本过新（v{version} > 当前支持的 v{VERSION}），请升级 Deep-tutor 后再导入")
    if not isinstance(data.get("sessions") or [], list):
        raise BackupFormatError("备份文件 sessions 字段非法")
    # 其余顶层段同样做类型校验：此前只校验 sessions，preview/import 对
    # `provider_profiles: 5` 走 len(5) 抛 TypeError、对 `knowledge_nodes: ["x"]`
    # 走 n.get() 抛 AttributeError——两者都不被端点的 BackupFormatError 分支捕获，
    # 落 500 internal_error（坏文件应是 400）。
    for key in ("provider_profiles", "notes", "threads", "memories", "kb_documents"):
        val = data.get(key)
        if val is not None and not isinstance(val, list):
            raise BackupFormatError(f"备份文件 {key} 字段非法（须为数组）")
    prefs = data.get("prefs")
    if prefs is not None and not isinstance(prefs, dict):
        raise BackupFormatError("备份文件 prefs 字段非法（须为对象）")
    user = data.get("user")
    if user is not None and not isinstance(user, dict):
        raise BackupFormatError("备份文件 user 字段非法（须为对象）")
    return data


def _as_list(v: Any) -> list:
    """容错取数组：坏备份里的非数组段按空处理（不抛）。"""
    return v if isinstance(v, list) else []


def _as_dicts(v: Any) -> list[dict]:
    """容错取对象数组：非对象元素剔除（不抛）。"""
    return [x for x in _as_list(v) if isinstance(x, dict)]


def preview_backup(data: Any, db: DbSession | None = None, user_id: int | None = None) -> dict:
    """导入预检（iter 159）：validate + 只读计数，不落库。结构非法抛 BackupFormatError。

    供 POST /backup/preview——用户导入前看清备份里有什么（防误导入错误文件）。

    计数口径与 restore 一致：共用同一套配额常量，并按"本机已有数据占用额度"抵扣，
    因此显示的就是**实际会落库的条数**（此前只数 payload 条目，界面显示
    "250 个会话"而导入实际只落地 200 个，额度被本机已有会话占用）。
    db / user_id 缺省时按"本机为空"计算。
    """
    data = validate_backup(data)
    sessions = _as_dicts(data.get("sessions"))

    def _used(model, **kw) -> int:
        if db is None or user_id is None:
            return 0
        return db.query(model).filter_by(**kw).count()

    sessions_room = max(0, _MAX_SESSIONS - _used(models.Session, user_id=user_id))
    notes_room = max(0, _MAX_NOTES - _used(models.Note, user_id=user_id))
    threads_room = max(0, _MAX_THREADS - _used(models.Thread, user_id=user_id))
    kb_room = max(0, _MAX_KB_DOCS - _used(models.KbDocument, user_id=user_id))
    profiles_room = max(0, _MAX_PROFILES - _used(models.LlmProviderProfile, user_id=user_id))
    prefs_room = max(0, _MAX_PREFS - _used(models.UserPref, user_id=user_id))
    memories_room = max(0, _MAX_MEMORIES - _used(models.Memory, user_id=user_id))

    # 已有键/同名档案在 restore 中被跳过，预检需同样抵扣
    existing_pref_keys: set[str] = set()
    existing_profile_names: set[str] = set()
    if db is not None and user_id is not None:
        existing_pref_keys = {r.key for r in db.query(models.UserPref).filter_by(user_id=user_id).all()}
        existing_profile_names = {
            r.name for r in db.query(models.LlmProviderProfile).filter_by(user_id=user_id).all()
        }

    n_sessions = min(len(sessions), sessions_room)
    n_materials = n_plans = n_nodes = n_node_notes = 0
    n_progress = n_logs = n_quizzes = n_attempts = n_units = n_resources = 0
    for s in sessions[:n_sessions]:
        n_materials += min(len(_as_list(s.get("materials"))), _MAX_MATERIALS_PER_SESSION)
        plans = _as_dicts(s.get("plans"))[:_MAX_PLANS_PER_SESSION]
        n_plans += len(plans)
        for p in plans:
            kept_nodes = _as_dicts(p.get("knowledge_nodes"))[:_MAX_NODES]
            n_nodes += len(kept_nodes)
            n_node_notes += sum(
                1 for x in kept_nodes if isinstance(x.get("note"), str) and str(x["note"]).strip()
            )
            n_resources += min(len(_as_dicts(p.get("resources"))), _MAX_RESOURCES)
            n_progress += min(len(_as_list(p.get("progress"))), _MAX_PROGRESS)
            n_logs += min(len(_as_list(p.get("learning_logs"))), _MAX_LOGS)
            quizzes = _as_list(p.get("quizzes"))[:_MAX_QUIZZES]
            n_quizzes += len(quizzes)
            for q in quizzes:
                if isinstance(q, dict):
                    n_attempts += min(len(_as_list(q.get("attempts"))), _MAX_ATTEMPTS)
            n_units += min(len(_as_dicts(p.get("study_units"))), _MAX_UNITS)

    threads = _as_dicts(data.get("threads"))
    n_threads = min(len(threads), threads_room)
    n_runs = n_messages = 0
    for t in threads[:n_threads]:
        n_runs += min(len(_as_list(t.get("runs"))), _MAX_RUNS_PER_THREAD)
        n_messages += min(len(_as_list(t.get("messages"))), _MAX_MSGS_PER_THREAD)

    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else {}
    n_prefs = min(
        len([k for k in prefs if str(k)[:64] not in existing_pref_keys]),
        prefs_room,
    )
    n_profiles = min(
        len([p for p in _as_dicts(data.get("provider_profiles")) if str(p.get("name") or "")[:64] not in existing_profile_names]),
        profiles_room,
    )

    user = data.get("user") or {}
    return {
        "version": data.get("version"),
        "exported_at": data.get("exported_at"),
        "username": str(user.get("username") or user.get("name") or ""),
        "sessions": n_sessions,
        "plans": n_plans,
        "materials": n_materials,
        "knowledge_nodes": n_nodes,
        "node_notes": n_node_notes,
        "resources": n_resources,
        "progress": n_progress,
        "learning_logs": n_logs,
        "quizzes": n_quizzes,
        "quiz_attempts": n_attempts,
        "study_units": n_units,
        "provider_profiles": n_profiles,
        "prefs": n_prefs,
        "notes": min(len(_as_dicts(data.get("notes"))), notes_room),
        # iter 369 R6（version 2 新增段；v1 旧备份无键 -> 0）
        "threads": n_threads,
        "messages": n_messages,
        "runs": n_runs,
        "memories": min(len(_as_dicts(data.get("memories"))), memories_room),
        "kb_documents": min(len(_as_dicts(data.get("kb_documents"))), kb_room),
        # 导出未覆盖的表——界面据此告知用户"备份不含这些"（表达与实现一致）
        "not_backed_up": list(_NOT_BACKED_UP),
    }


def _enum_or(enum_cls, value: Any):
    """字符串 -> 枚举；非法值返回 None（调用方决定跳过或降级）。"""
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


def _int_or(v: Any, default: int) -> int:
    """容错强转（iter 246）：备份字段类型非法取默认值——此前 int()/float() 直抛
    ValueError，坏备份文件落 500 import_failed 而非 400（端点只映 BackupFormatError），
    与同函数内 learning_logs.minutes 的容错跳过语义不一致。

    超范围值同样取默认值：SQLite INTEGER 是 64 位有符号，但 Python->sqlite 绑定
    超过 2**63-1 会抛 OverflowError（整包导入 500）。本仓 schema 无字段需要超过
    32 位，统一钳到 32 位区间，超界按"非法值"处理。"""
    try:
        out = int(v or default)
    except (TypeError, ValueError):
        return default
    if out < _INT32_MIN or out > _INT32_MAX:
        return default
    return out


def _int_or_none(v: Any) -> int | None:
    """同 _int_or，但缺省/非法返回 None（用于可空整数列，如 study_units.stage_index）。"""
    if v is None:
        return None
    try:
        out = int(v)
    except (TypeError, ValueError):
        return None
    if out < _INT32_MIN or out > _INT32_MAX:
        return None
    return out


def _float_or(v: Any, default: float) -> float:
    """同 _int_or；非有限值（nan/inf 字符串可过 float()）亦取默认。"""
    try:
        out = float(v or default)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _count_payload_rows(data: dict) -> int:
    """估算 payload 将创建的总行数（全局预算用）。

    逐段累加与会话/方案/节点等父级相关的行数：配额全按父级计时，构造备份可
    用"200 会话 × 50 方案 × 2000 打卡"把单次导入的行数放大到千万级——单事务内
    这么多次插入会独占 SQLite 写锁数分钟，其间全服务写请求 busy_timeout 超时 500。
    """
    total = 0
    for s in _as_dicts(data.get("sessions")):
        total += 1
        total += len(_as_list(s.get("materials")))
        for p in _as_dicts(s.get("plans")):
            total += 1
            # iter 396：知识点笔记（node.note 非空）会**额外**插一行 NodeNote，
            # 此前未计入 -> 10 万节点全带笔记时实际插入 20 万行，超预算一倍
            # （放大单事务持锁时长）。
            for n in _as_list(p.get("knowledge_nodes")):
                total += 1
                if isinstance(n, dict):
                    note = n.get("note")
                    if isinstance(note, str) and note.strip():
                        total += 1
            total += len(_as_list(p.get("resources")))
            total += len(_as_list(p.get("progress")))
            total += len(_as_list(p.get("learning_logs")))
            total += len(_as_list(p.get("study_units")))
            for q in _as_list(p.get("quizzes")):
                if isinstance(q, dict):
                    total += 1
                    total += len(_as_list(q.get("attempts")))
    for t in _as_dicts(data.get("threads")):
        total += 1
        total += len(_as_list(t.get("runs")))
        total += len(_as_list(t.get("messages")))
    total += len(_as_list(data.get("notes")))
    total += len(_as_list(data.get("memories")))
    total += len(_as_list(data.get("kb_documents")))
    total += len(_as_list(data.get("provider_profiles")))
    total += len(data.get("prefs") or {}) if isinstance(data.get("prefs"), dict) else 0
    return total


# iter 392：解析文本文件并发落盘的线程数上限。建文件是系统调用密集操作（Windows
# ~2.7ms/个），并发 8 线程即可把 2000 个文件的持锁时长从 5.4s 压到 ~0.7s；
# 再高收益递减（磁盘随机写串行化），且会与同进程其它 IO 抢带宽。
_PARSE_TEXT_WRITE_WORKERS = 8

# 共享写线程池（惰性创建，进程级）——按会话建池会让 500 个会话反复创建/销毁
# 2000 个线程，创建开销反超并行收益。备份导入由 _RESTORE_LOCK 串行，池只服务
# 单一导入流程，不会被并发导入叠加放大。
_WRITE_POOL: "ThreadPoolExecutor | None" = None


def _write_pool() -> "ThreadPoolExecutor":
    global _WRITE_POOL
    if _WRITE_POOL is None:
        from concurrent.futures import ThreadPoolExecutor

        _WRITE_POOL = ThreadPoolExecutor(
            max_workers=_PARSE_TEXT_WRITE_WORKERS, thread_name_prefix="dt-backup-write"
        )
    return _WRITE_POOL


def _submit_parsed_texts(
    pairs: list[tuple[models.Material, str]],
    created_files: list[str] | None,
) -> list[tuple[models.Material, "Future[str]"]]:
    """提交解析文本写入（并发，返回待回填的 (row, future)）。

    iter 392：单文件建/写成本（~2.7ms）远高于对应 DB 插入（~0.2ms），逐条串行写
    使**写事务持有期**被文件 IO 拉长数倍——导入期间全服务写请求等满 busy_timeout
    后失败。并发写缩短持锁时长，语义不变（文件仍先于 commit 落盘，失败仍可补偿）。

    跨会话统一提交（而非每会话等一批）：单会话最多 20 份资料，按会话等待会把并行
    度限在 20，200 个会话 = 4000 个文件仍是串行量级。

    线程安全：worker 只做文件 IO 与 list.append（GIL 原子）；ORM 属性回填在调用
    线程完成（SQLAlchemy Session 非线程安全）。
    """
    if not pairs:
        return []

    def _one(row: models.Material, text: str) -> str:
        path = str(write_parsed_text(row.session_id, row.id, text))
        if created_files is not None:
            created_files.append(path)  # list.append 线程安全（GIL）
        return path

    pool = _write_pool()
    return [(row, pool.submit(_one, row, text)) for row, text in pairs]


def restore_backup(
    db: DbSession,
    user: models.User,
    data: dict,
    created_files: list[str] | None = None,
) -> dict:
    """把备份数据追加还原为当前用户的新数据；返回各项计数摘要。

    单事务语义：本函数只 flush，commit/rollback 由调用方（端点）负责。
    created_files：可选的落盘文件登记簿——restore 期间写入磁盘的路径追加其中，
    端点在事务失败时据此删除（DB 回滚不回滚磁盘，kb 原件孤儿不被 maintenance 回收）。
    """
    data = validate_backup(data)
    total_rows = _count_payload_rows(data)
    if total_rows > _MAX_TOTAL_ROWS:
        raise BackupTooLargeError(
            f"备份将创建约 {total_rows} 行，超过单次导入上限 {_MAX_TOTAL_ROWS} 行——"
            "请拆分为多个备份分批导入"
        )
    summary: dict[str, Any] = {
        "sessions": 0,
        "materials": 0,
        "plans": 0,
        "knowledge_nodes": 0,
        "study_units": 0,
        "quizzes": 0,
        "quiz_attempts": 0,
        "resources": 0,
        "progress": 0,
        "learning_logs": 0,
        "provider_profiles": 0,
        "node_notes": 0,
        "prefs": 0,
        "notes": 0,
        # iter 369 R6（version 2 新增段）
        "threads": 0,
        "messages": 0,
        "runs": 0,
        "memories": 0,
        "kb_documents": 0,
        "skipped": [],
        # 导出未覆盖的表——回传以便界面明确告知"备份不含这些"
        "not_backed_up": list(_NOT_BACKED_UP),
    }

    # 配额与 API 路径一致（对齐 sessions.py/materials.py/notes.py 上限）：
    # 导入不放大用户的资源占用，超限部分跳过并记入 skipped（导入不硬失败）
    # 配额常量在模块级（与 preview 共用，见 _MAX_SESSIONS 等定义）
    sessions_room = _MAX_SESSIONS - db.query(models.Session).filter_by(user_id=user.id).count()

    # iter 392：解析文本文件写入的待回填项（并发提交，末尾统一回填 parsed_text_path）
    pending_writes: list[tuple[models.Material, "Future[str]"]] = []

    for s in data.get("sessions") or []:
        if not isinstance(s, dict):
            continue
        if sessions_room <= 0:
            summary["skipped"].append(f"会话数达上限 {_MAX_SESSIONS}，其余会话未导入")
            break
        sess = models.Session(user_id=user.id, title=str(s.get("title") or "")[:256])
        if dt := _parse_dt(s.get("created_at")):
            sess.created_at = dt
        db.add(sess)
        db.flush()  # 取新 session_id（文件落盘命名需要）
        summary["sessions"] += 1
        sessions_room -= 1

        mats_room = _MAX_MATERIALS_PER_SESSION
        mats_skipped = 0
        pending_texts: list[tuple[models.Material, str]] = []
        for m in s.get("materials") or []:
            if not isinstance(m, dict):
                continue
            if mats_room <= 0:
                mats_skipped += 1
                continue
            kind = _enum_or(models.MaterialKind, m.get("kind"))
            if kind is None:
                summary["skipped"].append(f"资料 kind 非法（{m.get('kind')!r}），已跳过")
                continue
            mats_room -= 1
            # 材料三段制状态随备份走（导出侧新增 status 字段；v1 旧备份无该键
            # -> 按"已解析"处理，与既有列默认一致）
            mat_status = str(m.get("status") or "parsed")
            if mat_status not in ("pending", "confirmed", "parsed"):
                mat_status = "parsed"
            row = models.Material(
                session_id=sess.id,
                kind=kind,
                source=str(m.get("source") or ""),
                parsed_chars=_int_or(m.get("parsed_chars"), 0),
                meta=m.get("meta") if isinstance(m.get("meta"), dict) else {},
                content_hash=(str(m.get("content_hash"))[:64] if m.get("content_hash") else None),
                status=mat_status,
            )
            if dt := _parse_dt(m.get("created_at")):
                row.created_at = dt
            db.add(row)
            text = m.get("parsed_text")
            if isinstance(text, str) and text:
                pending_texts.append((row, text))
            summary["materials"] += 1
        # iter 392：解析文本文件并发落盘（一次 flush 拿全部 material_id）。
        # 此前逐条 flush + 逐条建文件：Windows 建文件 ~2.7ms/个，2000 个资料即 5.4s，
        # 且全部发生在**写事务打开期间** -> SQLite 写锁被独占同样时长，期间全服务
        # 写请求等满 busy_timeout(10s) 后 500/503。并发写把持锁时长压到约 1/N。
        if pending_texts:
            db.flush()  # 取 material_id 命名解析文本文件
            pending_writes.extend(_submit_parsed_texts(pending_texts, created_files))
        if mats_skipped:
            summary["skipped"].append(
                f"会话「{sess.title or sess.id}」资料超每会话 {_MAX_MATERIALS_PER_SESSION} 份上限，{mats_skipped} 份未导入"
            )

        plans_room = _MAX_PLANS_PER_SESSION
        plans_skipped = 0
        # iter 396：版本号去重——备份里可能含重复 version（构造/损坏文件），而
        # schema 无 (session_id, version) 唯一约束 -> 还原后同会话出现重号版本，
        # 方案列表/版本选择器错乱。正常备份的 version 原样保留（往返保真），仅在
        # 重号或非递增时顺延为 max+1。
        used_versions: set[int] = set()
        max_version = 0
        for p in s.get("plans") or []:
            if not isinstance(p, dict):
                continue
            if plans_room <= 0:
                plans_skipped += 1
                continue
            plans_room -= 1
            v = _int_or(p.get("version"), 0)
            if v <= 0 or v <= max_version or v in used_versions:
                v = max_version + 1
            used_versions.add(v)
            max_version = max(max_version, v)
            _restore_plan(db, sess, p, summary, user.id, created_files, version=v)
        if plans_skipped:
            summary["skipped"].append(
                f"会话「{sess.title or sess.id}」方案超每会话 {_MAX_PLANS_PER_SESSION} 个上限，{plans_skipped} 个未导入"
            )

    _restore_profiles(db, user, data.get("provider_profiles") or [], summary)
    _restore_prefs(db, user, data.get("prefs") or {}, summary)
    _restore_notes(db, user, data.get("notes") or [], summary, _MAX_NOTES)
    # iter 369 R6：v2 新增段（v1 旧备份无键 -> 空列表直接返回，往返兼容）。
    # memories 的 thread 关联按下标 -> 新 Thread 行（导出顺序），故先还原 threads 拿映射。
    restored_threads = _restore_threads(db, user, data.get("threads") or [], summary)
    _restore_memories(db, user, data.get("memories") or [], restored_threads, summary)
    _restore_kb_documents(db, user, data.get("kb_documents") or [], summary, created_files)
    # iter 392：解析文本写入全部完成 -> 回填 parsed_text_path（主线程改 ORM 状态）。
    # 任一写入失败在此抛出（DB 事务随之回滚，已落盘文件已登记于 created_files 可补偿）。
    for row, fut in pending_writes:
        row.parsed_text_path = fut.result()
    return summary


def cleanup_created_files(paths: list[str]) -> int:
    """删除 restore 期间落盘的文件（事务失败时的补偿动作）；返回删除数。

    DB 回滚不回滚磁盘：restore 已写入的解析文本 / kb 原件 / 单元内容 / 方案溢出文件
    在事务回滚后成为孤儿。session 侧孤儿由 maintenance 在 24h 内回收，
    **kb 原件孤儿不会被回收**（maintenance 只扫 data/sessions/ 与 data/runs/），
    故必须在此补偿，否则重复失败的导入会让 data/kb 无限增长。
    """
    from app.core.storage import delete_unit_content

    removed = 0
    for raw in paths:
        try:
            path = Path(raw)
            if not path.exists():
                continue
            # 单元内容有 .pN 拆分子文件，需一并删除
            if path.name.startswith("unit_") and path.suffix == ".md":
                delete_unit_content(path)
            else:
                path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            log.warning("备份导入补偿删除失败 %s", raw)
    return removed


def _restore_threads(
    db: DbSession, user: models.User, threads: Any, summary: dict
) -> dict[int, models.Thread]:
    """threads/messages/runs（iter 369 R6）：追加式还原，按下标重映射 runs->messages
    的关联；返回 **payload 下标 -> 新 Thread 行** 映射（供 memories 段做下标映射）。

    配额与 API 路径同口径：threads 每用户 ≤200（同 sessions）、runs 每会话 ≤2000、
    messages 每会话 ≤5000（REST 无总上限但导出侧天然有界，此处防构造备份放大）。

    下标映射修复：messages.run_idx / memories.thread_idx 引用的是 **payload 数组下标**，
    而还原后的行列表只含"存活"的行——早期条目被跳过（非 dict / 超配额）时两个下标
    空间错位，消息会被挂到**另一个** run 上（静默错配）。故此处按 payload 原始下标
    建映射，跳过项不占用下标。
    """
    if not isinstance(threads, list):
        return {}
    room = _MAX_THREADS - db.query(models.Thread).filter_by(user_id=user.id).count()
    out: dict[int, models.Thread] = {}
    threads_skipped = 0
    for payload_idx, t in enumerate(threads):
        if not isinstance(t, dict):
            continue
        if room <= 0:
            threads_skipped += 1
            continue
        room -= 1
        kind = str(t.get("kind") or "chat")
        if kind not in ("chat", "plan_generation"):  # 与 ThreadIn 白名单同口径
            kind = "chat"
        row = models.Thread(
            user_id=user.id,
            title=str(t.get("title") or "")[:256],
            kind=kind,
            model=str(t.get("model") or "")[:128],
        )
        if dt := _parse_dt(t.get("created_at")):
            row.created_at = dt
        if dt := _parse_dt(t.get("updated_at")):
            row.updated_at = dt
        db.add(row)
        db.flush()  # 取新 thread_id
        out[payload_idx] = row
        summary["threads"] += 1

        # runs 先建（messages.run_idx -> 新 run id）；按 payload 下标建映射，
        # 跳过的 run 不占下标（否则后续 message 的 run_idx 错位）
        run_rows: dict[int, models.Run] = {}
        runs_room = _MAX_RUNS_PER_THREAD
        runs_skipped = 0
        for run_idx, r in enumerate(t.get("runs") or []):
            if not isinstance(r, dict) or runs_room <= 0:
                runs_skipped += 1
                continue
            runs_room -= 1
            status = str(r.get("status") or "interrupted")
            if status not in ("done", "error", "interrupted", "cancelled"):
                status = "interrupted"  # 非终态收敛（导出侧同规则，坏备份双保险）
            run = models.Run(
                thread_id=row.id,
                user_id=user.id,
                kind=_enum_or(models.RunKind, r.get("kind")) or models.RunKind.chat,
                status=models.RunStatus(status),
                step_count=max(0, _int_or(r.get("step_count"), 0)),
                budget_used=max(0.0, _float_or(r.get("budget_used"), 0.0)),
                workspace_dir="",  # 工作区不随备份走（恢复的 run 无可续工作区）
                workspace_committed=False,
                trace_id=str(r.get("trace_id") or "")[:64],
                error=r.get("error") if isinstance(r.get("error"), dict) else {},
            )
            if dt := _parse_dt(r.get("started_at")):
                run.started_at = dt
            if dt := _parse_dt(r.get("ended_at")):
                run.ended_at = dt
            db.add(run)
            run_rows[run_idx] = run
            summary["runs"] += 1
        if runs_skipped:
            summary["skipped"].append(
                f"会话「{row.title or row.id}」run 记录超 {_MAX_RUNS_PER_THREAD} 条上限，{runs_skipped} 条未导入"
            )
        db.flush()

        msgs_room = _MAX_MSGS_PER_THREAD
        msgs_skipped = 0
        for m in t.get("messages") or []:
            if not isinstance(m, dict) or msgs_room <= 0:
                msgs_skipped += 1
                continue
            msgs_room -= 1
            role = _enum_or(models.MessageRole, m.get("role"))
            if role is None:
                continue  # 角色非法的消息无 Anthropic 语义，跳过
            run_idx = m.get("run_idx")
            msg = models.Message(
                thread_id=row.id,
                role=role,
                content=m.get("content") if isinstance(m.get("content"), list) else [],
                tokens_in=max(0, _int_or(m.get("tokens_in"), 0)),
                tokens_out=max(0, _int_or(m.get("tokens_out"), 0)),
                model=str(m.get("model") or "")[:128],
                run_id=run_rows[run_idx].id if isinstance(run_idx, int) and run_idx in run_rows else None,
            )
            if dt := _parse_dt(m.get("created_at")):
                msg.created_at = dt
            db.add(msg)
            summary["messages"] += 1
        if msgs_skipped:
            summary["skipped"].append(
                f"会话「{row.title or row.id}」消息超 {_MAX_MSGS_PER_THREAD} 条上限，{msgs_skipped} 条未导入"
            )
        db.flush()
    if threads_skipped:
        summary["skipped"].append(f"对话会话数达上限 {_MAX_THREADS} 个，{threads_skipped} 个未导入")
    return out


def _restore_memories(
    db: DbSession,
    user: models.User,
    memories: Any,
    restored_threads: dict[int, models.Thread],
    summary: dict,
) -> None:
    """memories（iter 369 R6）：追加式还原；thread_idx 按 payload 下标映射到新 Thread 行。

    配额与 REST /memories 及 agent 工具同口径（每用户 ≤2000，content ≤20000 字符）。"""
    if not isinstance(memories, list):
        return
    _MAX_CHARS = _MAX_MEM_CHARS
    room = _MAX_MEMORIES - db.query(models.Memory).filter_by(user_id=user.id).count()
    skipped = 0
    for m in memories:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        if room <= 0:
            skipped += 1
            continue
        room -= 1
        scope = str(m.get("scope") or "thread")
        if scope not in ("thread", "user"):
            scope = "thread"
        thread_idx = m.get("thread_idx")
        thread = (
            restored_threads.get(thread_idx) if isinstance(thread_idx, int) else None
        )
        row = models.Memory(
            user_id=user.id,
            thread_id=thread.id if (scope == "thread" and thread) else None,
            scope=scope,
            kind=str(m.get("kind") or "note")[:32],
            content=content[:_MAX_CHARS],
            meta=m.get("meta") if isinstance(m.get("meta"), dict) else {},
        )
        if dt := _parse_dt(m.get("created_at")):
            row.created_at = dt
        if dt := _parse_dt(m.get("updated_at")):
            row.updated_at = dt
        db.add(row)
        summary["memories"] += 1
    if skipped:
        summary["skipped"].append(f"记忆数达上限 {_MAX_MEMORIES} 条，{skipped} 条未导入")
    db.flush()


def _restore_kb_documents(
    db: DbSession,
    user: models.User,
    docs: Any,
    summary: dict,
    created_files: list[str] | None = None,
) -> None:
    """kb_documents（iter 369 R6）：追加式还原——parsed 文本重落盘（新 doc id 命名）、
    原件 base64 解码重存；配额与 /kb 同口径（每用户 ≤200）。"""
    if not isinstance(docs, list):
        return
    room = _MAX_KB_DOCS - db.query(models.KbDocument).filter_by(user_id=user.id).count()
    skipped = 0
    for d in docs:
        if not isinstance(d, dict):
            continue
        title = str(d.get("title") or "")[:256]
        if not title:
            continue
        if room <= 0:
            skipped += 1
            continue
        room -= 1
        kind = _enum_or(models.MaterialKind, d.get("kind"))
        if kind is None:
            summary["skipped"].append(f"知识库文档「{title}」kind 非法，已跳过")
            continue
        status = str(d.get("status") or "parsed")
        if status not in ("pending", "confirmed", "parsed"):
            status = "parsed"
        row = models.KbDocument(
            user_id=user.id,
            title=title,
            kind=kind,
            ext=str(d.get("ext") or "")[:16],
            source=str(d.get("source") or "")[:1024],
            parsed_chars=max(0, _int_or(d.get("parsed_chars"), 0)),
            meta=d.get("meta") if isinstance(d.get("meta"), dict) else {},
            content_hash=(str(d.get("content_hash"))[:64] if d.get("content_hash") else None),
            status=status,
        )
        if dt := _parse_dt(d.get("created_at")):
            row.created_at = dt
        if dt := _parse_dt(d.get("updated_at")):
            row.updated_at = dt
        db.add(row)
        db.flush()  # 取新 doc_id 命名文件
        text = d.get("parsed_text")
        if isinstance(text, str) and text:
            path = write_kb_parsed_text(user.id, row.id, text)
            row.parsed_text_path = str(path)
            if created_files is not None:
                created_files.append(str(path))
        b64 = d.get("original_b64")
        if isinstance(b64, str) and b64:
            try:
                raw = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError):
                summary["skipped"].append(f"知识库文档「{title}」原件数据损坏，仅还原解析文本")
                raw = None
            if raw is not None and len(raw) > _KB_ORIGINAL_MAX_BYTES:
                # 与导出侧同上限：导出侧拒绝内联 >32MB 的原件，导入侧同样不放行
                # （否则构造备份可写入 190MB 原件，绕过导出侧的体积约束）
                summary["skipped"].append(
                    f"知识库文档「{title}」原件超 {_KB_ORIGINAL_MAX_BYTES // 1024 // 1024}MB 上限，未还原原件"
                )
                raw = None
            if raw is not None:
                path = save_kb_upload(user.id, row.id, raw, row.ext)
                row.original_path = str(path)
                if created_files is not None:
                    created_files.append(str(path))
        summary["kb_documents"] += 1
    if skipped:
        summary["skipped"].append(f"知识库文档数达上限 {_MAX_KB_DOCS} 篇，{skipped} 篇未导入")
    db.flush()


def _restore_notes(db: DbSession, user: models.User, notes: Any, summary: dict, max_notes: int = 500) -> None:
    """独立自由笔记（iter 227）：追加式还原（不去重，导入即新增）；标题/正文同 API
    上限截断，双空条目跳过；旧备份无 notes 字段 -> 空列表直接返回。
    配额与 notes.py 一致（每用户 ≤500，含已有），超限跳过。"""
    if not isinstance(notes, list):
        return
    room = max_notes - db.query(models.Note).filter_by(user_id=user.id).count()
    skipped = 0
    for n in notes:
        if not isinstance(n, dict):
            continue
        title = str(n.get("title") or "").strip()[:256]
        content = str(n.get("content") or "").strip()[:20_000]
        if not title and not content:
            continue
        if room <= 0:
            skipped += 1
            continue
        room -= 1
        note = models.Note(user_id=user.id, title=title, content=content)
        if dt := _parse_dt(n.get("updated_at")):
            note.updated_at = dt
        db.add(note)
        summary["notes"] += 1
    if skipped:
        summary["skipped"].append(f"笔记数达上限 {max_notes} 条，{skipped} 条未导入")
    db.flush()


def _restore_prefs(db: DbSession, user: models.User, prefs: Any, summary: dict) -> None:
    """用户偏好 KV（iter 177）：本机已有同键则跳过（与供应商档案同策略——
    追加式还原不覆盖现有设置）；旧备份无 prefs 字段 -> 空 dict 直接返回。"""
    if not isinstance(prefs, dict):
        return
    existing = {r.key for r in db.query(models.UserPref).filter_by(user_id=user.id).all()}
    room = _MAX_PREFS - len(existing)
    prefs_skipped = 0
    for k, v in prefs.items():
        key = str(k)[:64]
        if not key:
            continue
        if key in existing:
            summary["skipped"].append(f"偏好「{key}」本机已有值，已跳过")
            continue
        if room <= 0:
            prefs_skipped += 1
            continue
        room -= 1
        db.add(models.UserPref(user_id=user.id, key=key, value=str(v)[:4000]))
        existing.add(key)
        summary["prefs"] += 1
    if prefs_skipped:
        summary["skipped"].append(f"偏好数达上限 {_MAX_PREFS} 条，{prefs_skipped} 条未导入")


def _restore_plan(
    db: DbSession,
    sess: models.Session,
    p: dict,
    summary: dict,
    user_id: int,
    created_files: list[str] | None = None,
    version: int | None = None,
) -> None:
    # iter 246：单方案子对象配额——构造备份此前可在一次导入里写入无限行
    # （外层仅有会话/资料/笔记配额）；超限跳过并计入 skipped（对齐外层配额语义）
    # 配额常量在模块级（与 preview 共用）
    over: dict[str, int] = {}  # 标签 -> 跳过条数（函数末尾汇总进 summary["skipped"]）
    orphan: dict[str, int] = {}  # 标签 -> 因锚点缺失而丢弃的条数（同样显式披露）

    def _skip(tag: str) -> None:
        over[tag] = over.get(tag, 0) + 1

    def _orphan(tag: str) -> None:
        orphan[tag] = orphan.get(tag, 0) + 1

    content = p.get("content") if isinstance(p.get("content"), dict) else {}
    plan = models.Plan(
        session_id=sess.id,
        content=content,
        # iter 396：version 由调用方按会话内顺序去重后传入（重号/非递增时顺延为
        # max+1）——备份里的 version 可能重复（构造/损坏文件），schema 又无
        # (session_id, version) 唯一约束，还原后同会话出现重号版本 -> 方案列表/
        # 版本选择器错乱。不传 version 时沿用备份值（兼容单测直调）。
        version=version if version is not None else _int_or(p.get("version"), 1),
    )
    if dt := _parse_dt(p.get("created_at")):
        plan.created_at = dt
    db.add(plan)
    db.flush()
    # 大方案同样溢出落盘（与 _persist_plan 同规则，DB 行不塞超大 JSON）
    content_json = json.dumps(content, ensure_ascii=False)
    if content and len(content_json.encode("utf-8")) > _PLAN_OVERFLOW_BYTES:
        from app.core.storage import atomic_write_text, exports_dir

        # iter 369：tmp+os.replace 原子写（与正常生成路径 plans._persist_plan 同口径）——
        # 裸 write_text 中途崩溃/IO 错留下半截 plan_<id>.json，若其后 commit 成功或
        # 重试导入复用同 id，content_path 指向截断文件 -> 大方案静默变空
        path = exports_dir(sess.id) / f"plan_{plan.id}.json"
        atomic_write_text(path, content_json)
        plan.content_path = str(path)
        plan.content = {}
        if created_files is not None:
            created_files.append(str(path))
    summary["plans"] += 1

    # 知识节点两遍插入（先建行取 id，再按 sid 回填父引用），sid -> 新行映射
    #
    # 重复 sid 的处理（确定性化）：schema 未对 (plan_id, sid) 建唯一约束，构造备份
    # 或经非 clean_map 路径落库的方案可能含两条同 sid 的节点。原实现两条都插、
    # 映射表却"后者覆盖前者"——先插的那条永远拿不到 parent_id（退化成根节点），
    # 而挂在该 sid 上的进度/资源/笔记全部改挂到后一条（知识点被错认）。
    # 现改为：**首条胜出，后续同 sid 行跳过并显式计入 skipped**，
    # 映射表与父子回填保持一一对应，树形与归属不再漂移。
    sid_to_node: dict[str, models.KnowledgeNode] = {}
    nodes_room = _MAX_NODES
    for n in p.get("knowledge_nodes") or []:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name") or "")[:256]
        if not name:
            continue
        if nodes_room <= 0:
            _skip("知识节点")
            continue
        sid = str(n.get("sid") or "")[:64]
        if sid and sid in sid_to_node:
            _skip("重复 sid 知识节点")
            continue
        nodes_room -= 1
        row = models.KnowledgeNode(
            plan_id=plan.id,
            sid=sid,
            name=name,
            difficulty=max(1, min(5, _int_or(n.get("difficulty"), 1))),
            must_learn=bool(n.get("must_learn")),
        )
        db.add(row)
        if row.sid:
            sid_to_node[row.sid] = row
        summary["knowledge_nodes"] += 1
    db.flush()
    for n in p.get("knowledge_nodes") or []:
        if not isinstance(n, dict):
            continue
        row = sid_to_node.get(str(n.get("sid") or ""))
        parent = sid_to_node.get(str(n.get("parent_sid") or ""))
        if row is not None and parent is not None and parent is not row:
            row.parent_id = parent.id
    db.flush()

    # 知识点笔记（iter 105）：随节点还原（note 字段缺省=无笔记，兼容旧备份）
    # iter 367：备份内同 sid 重复出现时只取首条——两条同 (user_id,node_id) 的
    # NodeNote 会撞唯一约束让整包导入 500（手工编辑/损坏备份的健壮性）
    seen_note_sids: set[str] = set()
    for n in p.get("knowledge_nodes") or []:
        if not isinstance(n, dict):
            continue
        note_content = n.get("note")
        if not isinstance(note_content, str) or not note_content.strip():
            continue
        sid = str(n.get("sid") or "")
        row = sid_to_node.get(sid)
        if row is None:
            continue  # 无 sid 锚点的节点无法重关联笔记
        if sid in seen_note_sids:
            _skip("重复节点笔记")
            continue
        seen_note_sids.add(sid)
        db.add(models.NodeNote(user_id=user_id, node_id=row.id, content=note_content.strip()[:20_000]))
        summary["node_notes"] += 1
    db.flush()

    resources_room = _MAX_RESOURCES
    for r in p.get("resources") or []:
        if not isinstance(r, dict):
            continue
        node = sid_to_node.get(str(r.get("node_sid") or ""))
        if node is None:
            # 锚点知识点缺失（节点被 100 条上限截断或 sid 损坏）——此前静默丢弃，
            # 界面只看到"资源 0"，用户无从得知学习资源丢失
            _orphan("资源")
            continue
        if resources_room <= 0:
            _skip("资源")
            continue
        resources_room -= 1
        rtype = _enum_or(models.ResourceType, r.get("type")) or models.ResourceType.doc
        verified = _enum_or(models.VerifyLabel, r.get("verified")) or models.VerifyLabel.uncertain
        db.add(
            models.Resource(
                node_id=node.id,
                url=str(r.get("url") or ""),
                title=str(r.get("title") or "")[:512],
                type=rtype,
                verified=verified,
                note=str(r.get("note") or ""),
            )
        )
        summary["resources"] += 1

    progress_room = _MAX_PROGRESS
    seen_progress_sids: set[str] = set()  # iter 367：同上，重复 (plan_id,node_id) 撞唯一约束防 500
    for pr in p.get("progress") or []:
        if not isinstance(pr, dict):
            continue
        sid = str(pr.get("node_sid") or "")
        node = sid_to_node.get(sid)
        if node is None:
            # 进度锚点缺失 = 学习进度丢失（掌握度/复习排期），必须显式披露
            _orphan("进度记录")
            continue
        if sid in seen_progress_sids:
            _skip("重复进度记录")
            continue
        if progress_room <= 0:
            _skip("进度记录")
            continue
        seen_progress_sids.add(sid)
        progress_room -= 1
        status = _enum_or(models.ProgressStatus, pr.get("status")) or models.ProgressStatus.not_started
        db.add(
            models.Progress(
                plan_id=plan.id,
                node_id=node.id,
                status=status,
                mastery=max(0.0, min(1.0, _float_or(pr.get("mastery"), 0.0))),
            )
        )
        summary["progress"] += 1

    logs_room = _MAX_LOGS
    for lg in p.get("learning_logs") or []:
        if not isinstance(lg, dict):
            continue
        d = _parse_date(lg.get("date"))
        if d is None:
            continue
        # 分钟数钳制到 API 的 1-1440（progress.py LogIn 同约束；备份数据可能带 0/负数/超大值）
        try:
            minutes = int(lg.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        if logs_room <= 0:
            _skip("打卡记录")
            continue
        logs_room -= 1
        minutes = max(1, min(1440, minutes))
        db.add(
            models.LearningLog(
                plan_id=plan.id,
                date=d,
                minutes=minutes,
                note=lg.get("note") if isinstance(lg.get("note"), str) else None,
            )
        )
        summary["learning_logs"] += 1

    quizzes_room = _MAX_QUIZZES
    for q in p.get("quizzes") or []:
        if not isinstance(q, dict):
            continue
        if quizzes_room <= 0:
            _skip("测验")
            continue
        quizzes_room -= 1
        qtype = _enum_or(models.QuizType, q.get("type")) or models.QuizType.choice
        node = sid_to_node.get(str(q.get("node_sid") or ""))
        quiz = models.Quiz(
            plan_id=plan.id,
            node_id=node.id if node else None,
            type=qtype,
            payload=q.get("payload") if isinstance(q.get("payload"), dict) else {},
        )
        db.add(quiz)
        db.flush()
        summary["quizzes"] += 1
        attempts_room = _MAX_ATTEMPTS
        for a in q.get("attempts") or []:
            if not isinstance(a, dict):
                continue
            if attempts_room <= 0:
                _skip("作答记录")
                continue
            attempts_room -= 1
            att = models.QuizAttempt(
                quiz_id=quiz.id,
                user_answer=a.get("user_answer") if isinstance(a.get("user_answer"), dict) else {},
                correct=a.get("correct") if isinstance(a.get("correct"), bool) else None,
            )
            if dt := _parse_dt(a.get("created_at")):
                att.created_at = dt
            db.add(att)
            summary["quiz_attempts"] += 1

    units_room = _MAX_UNITS
    for u in p.get("study_units") or []:
        if not isinstance(u, dict):
            continue
        title = str(u.get("title") or "")[:256]
        if not title:
            continue
        if units_room <= 0:
            _skip("自学单元")
            continue
        units_room -= 1
        node = sid_to_node.get(str(u.get("node_sid") or ""))
        raw_sids = u.get("node_sids")
        unit = models.StudyUnit(
            plan_id=plan.id,
            node_id=node.id if node else None,
            # iter 367：回填单元覆盖知识点（sid 直存列，无需 id 映射；旧备份缺省 []）
            node_sids=[str(s) for s in raw_sids if isinstance(s, str)][:100] if isinstance(raw_sids, list) else [],
            # 超范围整数按非法值处理（直接写入会在 sqlite 绑定时抛 OverflowError，整包 500）
            stage_index=_int_or_none(u.get("stage_index")),
            title=title,
            prerequisites=u.get("prerequisites") if isinstance(u.get("prerequisites"), list) else [],
            objectives=u.get("objectives") if isinstance(u.get("objectives"), list) else [],
            estimated_time=str(u.get("estimated_time") or "")[:64],
            order=_int_or(u.get("order"), 0),
        )
        if dt := _parse_dt(u.get("done_at")):
            unit.done_at = dt
        db.add(unit)
        db.flush()  # 取 unit_id 命名内容文件
        summary["study_units"] += 1
        content_md = u.get("content_md")
        if isinstance(content_md, str) and content_md:
            path = write_unit_content(sess.id, unit.id, content_md)  # >500 行自动拆子文件
            if created_files is not None:
                created_files.append(str(path))
            db.add(
                models.StudyMaterial(
                    unit_id=unit.id,
                    content_md_path=str(path),
                    size=len(content_md),
                    resources=u.get("material_resources") if isinstance(u.get("material_resources"), list) else [],
                )
            )

    if over:
        detail = "、".join(f"{tag} {n} 条" for tag, n in over.items())
        summary["skipped"].append(f"方案「{str(p.get('title') or plan.id)[:64]}」子对象超上限，跳过：{detail}")
    if orphan:
        # 锚点缺失导致的丢弃此前完全静默——界面只看到"进度 0"，用户无从得知
        # 学习进度/资源在导入中丢失。显式披露，与"超上限跳过"分列。
        detail = "、".join(f"{tag} {n} 条" for tag, n in orphan.items())
        summary["skipped"].append(
            f"方案「{str(p.get('title') or plan.id)[:64]}」部分数据因锚点知识点缺失未导入：{detail}"
        )


def _restore_profiles(db: DbSession, user: models.User, profiles: list, summary: dict) -> None:
    """供应商档案：空 key 导入（凭证不随备份走），一律置为未激活——
    用户在设置页补填 API Key 后手动激活，避免空 key 档案被误用。
    同名档案已存在则跳过（(user_id,name) 唯一约束）。
    iter 367：条数上限（iter 246 配额补丁漏项）——构造备份可带无限个不同名档案。"""
    existing = {r.name for r in db.query(models.LlmProviderProfile).filter_by(user_id=user.id).all()}
    room = _MAX_PROFILES - len(existing)
    profiles_skipped = 0
    for pr in profiles:
        if not isinstance(pr, dict):
            continue
        name = str(pr.get("name") or "")[:64]
        if not name:
            continue
        if name in existing:
            summary["skipped"].append(f"供应商档案「{name}」已存在，已跳过")
            continue
        # iter 396：写时 SSRF 校验——base_url 是**服务端出站目标**且由备份内容
        # 控制；导入不校验即可写入 file:// 之类非法协议，或指向私网 / 云元数据
        # （169.254.169.254）的域名（调用期只做字面量判定，不解析 DNS）。此处补
        # 与 api/providers.py 写时同口径的校验（解析 DNS 后判私网），非法即跳过。
        base_url = str(pr.get("base_url") or "")[:512]
        if base_url and not settings.allow_private_llm_base_url:
            from app.core.urlguard import check_outbound_url

            ok, reason = check_outbound_url(base_url)
            if not ok:
                summary["skipped"].append(
                    f"供应商档案「{name}」base_url 被安全策略拒绝（{reason}），已跳过"
                )
                continue
        if room <= 0:
            profiles_skipped += 1
            continue
        room -= 1
        # iter 367：kind 缺省时旧表达式 (… or None)[:64] 即 None[:64] TypeError——
        # 备份档案不带 kind 会让整包导入 500（测试实测暴露）
        kind_val = str(pr.get("kind") or "").strip().lower()
        db.add(
            models.LlmProviderProfile(
                user_id=user.id,
                name=name,
                base_url=base_url,
                api_key="",  # 凭证边界：不随备份导入
                model=str(pr.get("model") or "")[:128],
                kind=kind_val[:64] if kind_val else None,  # iter 313 协议
                is_active=False,
            )
        )
        existing.add(name)
        summary["provider_profiles"] += 1
    if profiles_skipped:
        summary["skipped"].append(f"供应商档案达上限 {_MAX_PROFILES} 条，{profiles_skipped} 条未导入")
