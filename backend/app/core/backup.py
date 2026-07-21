"""用户数据备份导出/导入（iter 93，用户需求：导出能再导入回去方便备份）。

导出（build_export）：当前用户全部学习资产打包为单个自包含 JSON——
会话 / 资料（解析文本内联）/ 方案（content 内联，含溢出文件回读）/ 知识节点树 /
进度 / 打卡 / 测验（含作答记录）/ 自学材料（md 内联，拆分子文件自动串联）/
用户偏好 KV（iter 177：日预算等覆盖项）。
**凭证边界**：api_key 等敏感字段绝不导出——供应商档案以空 key 导出，
导入后在设置页补填并激活。

导入（restore_backup）：**追加式还原**——全部以新行重建（不覆盖现有数据，
重复导入产生副本，UI 已提示）；外键全量重映射，知识节点树/进度/测验/单元
按节点 sid 重关联；大内容经 app.core.storage 重新落盘（新 session/unit id 命名）。
单事务：任何硬错误整体回滚（端点负责），不留半截数据。
格式带 format/version 头：后续版本演进时按 version 分派迁移逻辑。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.storage import read_unit_content, write_parsed_text, write_unit_content
from app.db import models

log = logging.getLogger(__name__)

FORMAT = "deep-tutor-backup"
VERSION = 1

# 与 app.api.plans.PLAN_OVERFLOW_BYTES 保持一致（plan/01 §五：>512KB 溢出文件）；
# 不跨层 import api 模块，故在此冗余定义（稳定常量，变更概率极低）
_PLAN_OVERFLOW_BYTES = 512 * 1024


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


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
    sessions = (
        db.query(models.Session)
        .filter_by(user_id=user.id)
        .order_by(models.Session.id)
        .all()
    )
    for sess in sessions:
        materials_out = [
            {
                "kind": m.kind.value,
                "source": m.source,
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
            "is_active": bool(p.is_active),
        }
        for p in db.query(models.LlmProviderProfile).filter_by(user_id=user.id).all()
    ]

    prefs_out = {  # 用户偏好 KV（iter 177）：日预算等覆盖项随备份走
        r.key: r.value
        for r in db.query(models.UserPref).filter_by(user_id=user.id).all()
    }

    notes_out = [  # 独立自由笔记（iter 227）：顶层 notes 键，v1 兼容（旧备份无该键不还原）
        {"title": n.title, "content": n.content, "updated_at": _iso(n.updated_at)}
        for n in db.query(models.Note).filter_by(user_id=user.id).order_by(models.Note.id).all()
    ]

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # 与全库 UTC 约定一致（其余时间戳均 func.now()/UTC；本地时间仅用于人类可读导出标题/文件名）
        "user": {"username": user.username or "", "name": user.name},
        "provider_profiles": profiles_out,
        "prefs": prefs_out,
        "notes": notes_out,
        "sessions": sessions_out,
    }


def _export_plan(db: DbSession, plan: models.Plan, user_id: int) -> dict:
    nodes = (
        db.query(models.KnowledgeNode)
        .filter_by(plan_id=plan.id)
        .order_by(models.KnowledgeNode.id)
        .all()
    )
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
        raise BackupFormatError(
            f"备份版本过新（v{version} > 当前支持的 v{VERSION}），请升级 Deep-tutor 后再导入"
        )
    if not isinstance(data.get("sessions") or [], list):
        raise BackupFormatError("备份文件 sessions 字段非法")
    return data


def preview_backup(data: Any) -> dict:
    """导入预检（iter 159）：validate + 只读计数，不落库。结构非法抛 BackupFormatError。

    供 POST /backup/preview——用户导入前看清备份里有什么（防误导入错误文件）。
    计数口径与 restore 的 summary 键一致（另加导出元信息）。
    """
    data = validate_backup(data)
    sessions = [s for s in data["sessions"] if isinstance(s, dict)]
    plans = [p for s in sessions for p in (s.get("plans") or []) if isinstance(p, dict)]
    user = data.get("user") or {}
    return {
        "version": data.get("version"),
        "exported_at": data.get("exported_at"),
        "username": str(user.get("username") or user.get("name") or ""),
        "sessions": len(sessions),
        "plans": len(plans),
        "materials": sum(len(s.get("materials") or []) for s in sessions),
        "knowledge_nodes": sum(len(p.get("knowledge_nodes") or []) for p in plans),
        "node_notes": sum(
            1 for p in plans for n in (p.get("knowledge_nodes") or []) if n.get("note")
        ),
        "progress": sum(len(p.get("progress") or []) for p in plans),
        "learning_logs": sum(len(p.get("learning_logs") or []) for p in plans),
        "quizzes": sum(len(p.get("quizzes") or []) for p in plans),
        "study_units": sum(len(p.get("study_units") or []) for p in plans),
        "provider_profiles": len(data.get("provider_profiles") or []),
        "prefs": len(data.get("prefs")) if isinstance(data.get("prefs"), dict) else 0,
        "notes": len(data.get("notes") or []),
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
    与同函数内 learning_logs.minutes 的容错跳过语义不一致。"""
    try:
        return int(v or default)
    except (TypeError, ValueError):
        return default


def _float_or(v: Any, default: float) -> float:
    """同 _int_or；非有限值（nan/inf 字符串可过 float()）亦取默认。"""
    try:
        out = float(v or default)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def restore_backup(db: DbSession, user: models.User, data: dict) -> dict:
    """把备份数据追加还原为当前用户的新数据；返回各项计数摘要。

    单事务语义：本函数只 flush，commit/rollback 由调用方（端点）负责。
    """
    data = validate_backup(data)
    summary: dict[str, Any] = {
        "sessions": 0,
        "materials": 0,
        "plans": 0,
        "knowledge_nodes": 0,
        "study_units": 0,
        "quizzes": 0,
        "progress": 0,
        "learning_logs": 0,
        "provider_profiles": 0,
        "node_notes": 0,
        "prefs": 0,
        "notes": 0,
        "skipped": [],
    }

    # 配额与 API 路径一致（对齐 sessions.py/materials.py/notes.py 上限）：
    # 导入不放大用户的资源占用，超限部分跳过并记入 skipped（导入不硬失败）
    _MAX_SESSIONS, _MAX_MATERIALS_PER_SESSION, _MAX_NOTES = 200, 20, 500
    _MAX_PLANS_PER_SESSION = 50  # iter 246：方案每会话上限（此前子对象仅校验类型，构造备份可无限放大行数）
    sessions_room = _MAX_SESSIONS - db.query(models.Session).filter_by(user_id=user.id).count()

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
            row = models.Material(
                session_id=sess.id,
                kind=kind,
                source=str(m.get("source") or ""),
                parsed_chars=_int_or(m.get("parsed_chars"), 0),
                meta=m.get("meta") if isinstance(m.get("meta"), dict) else {},
                content_hash=(str(m.get("content_hash"))[:64] if m.get("content_hash") else None),
            )
            if dt := _parse_dt(m.get("created_at")):
                row.created_at = dt
            db.add(row)
            db.flush()  # 取 material_id 命名解析文本文件
            text = m.get("parsed_text")
            if isinstance(text, str) and text:
                row.parsed_text_path = str(write_parsed_text(sess.id, row.id, text))
            summary["materials"] += 1
        if mats_skipped:
            summary["skipped"].append(f"会话「{sess.title or sess.id}」资料超每会话 {_MAX_MATERIALS_PER_SESSION} 份上限，{mats_skipped} 份未导入")

        plans_room = _MAX_PLANS_PER_SESSION
        plans_skipped = 0
        for p in s.get("plans") or []:
            if not isinstance(p, dict):
                continue
            if plans_room <= 0:
                plans_skipped += 1
                continue
            plans_room -= 1
            _restore_plan(db, sess, p, summary, user.id)
        if plans_skipped:
            summary["skipped"].append(f"会话「{sess.title or sess.id}」方案超每会话 {_MAX_PLANS_PER_SESSION} 个上限，{plans_skipped} 个未导入")

    _restore_profiles(db, user, data.get("provider_profiles") or [], summary)
    _restore_prefs(db, user, data.get("prefs") or {}, summary)
    _restore_notes(db, user, data.get("notes") or [], summary, _MAX_NOTES)
    return summary


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
    _MAX_PREFS = 200  # iter 246：偏好 KV 上限（构造备份可无限写键）
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


def _restore_plan(db: DbSession, sess: models.Session, p: dict, summary: dict, user_id: int) -> None:
    # iter 246：单方案子对象配额——构造备份此前可在一次导入里写入无限行
    # （外层仅有会话/资料/笔记配额）；超限跳过并计入 skipped（对齐外层配额语义）
    _MAX_NODES, _MAX_RESOURCES, _MAX_PROGRESS = 100, 200, 500
    _MAX_LOGS, _MAX_QUIZZES, _MAX_ATTEMPTS, _MAX_UNITS = 2000, 200, 100, 100
    over: dict[str, int] = {}  # 标签 -> 跳过条数（函数末尾汇总进 summary["skipped"]）

    def _skip(tag: str) -> None:
        over[tag] = over.get(tag, 0) + 1

    content = p.get("content") if isinstance(p.get("content"), dict) else {}
    plan = models.Plan(
        session_id=sess.id,
        content=content,
        version=_int_or(p.get("version"), 1),
    )
    if dt := _parse_dt(p.get("created_at")):
        plan.created_at = dt
    db.add(plan)
    db.flush()
    # 大方案同样溢出落盘（与 _persist_plan 同规则，DB 行不塞超大 JSON）
    content_json = json.dumps(content, ensure_ascii=False)
    if content and len(content_json.encode("utf-8")) > _PLAN_OVERFLOW_BYTES:
        from app.core.storage import exports_dir

        path = exports_dir(sess.id) / f"plan_{plan.id}.json"
        path.write_text(content_json, encoding="utf-8")
        plan.content_path = str(path)
        plan.content = {}
    summary["plans"] += 1

    # 知识节点两遍插入（先建行取 id，再按 sid 回填父引用），sid -> 新行映射
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
        nodes_room -= 1
        row = models.KnowledgeNode(
            plan_id=plan.id,
            sid=str(n.get("sid") or "")[:64],
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
    for n in p.get("knowledge_nodes") or []:
        if not isinstance(n, dict):
            continue
        note_content = n.get("note")
        if not isinstance(note_content, str) or not note_content.strip():
            continue
        row = sid_to_node.get(str(n.get("sid") or ""))
        if row is None:
            continue  # 无 sid 锚点的节点无法重关联笔记
        db.add(
            models.NodeNote(
                user_id=user_id, node_id=row.id, content=note_content.strip()[:20_000]
            )
        )
        summary["node_notes"] += 1
    db.flush()

    resources_room = _MAX_RESOURCES
    for r in p.get("resources") or []:
        if not isinstance(r, dict):
            continue
        node = sid_to_node.get(str(r.get("node_sid") or ""))
        if node is None:
            continue  # 无锚点资源跳过（节点未导入时无归属）
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

    progress_room = _MAX_PROGRESS
    for pr in p.get("progress") or []:
        if not isinstance(pr, dict):
            continue
        node = sid_to_node.get(str(pr.get("node_sid") or ""))
        if node is None:
            continue
        if progress_room <= 0:
            _skip("进度记录")
            continue
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
        unit = models.StudyUnit(
            plan_id=plan.id,
            node_id=node.id if node else None,
            stage_index=u.get("stage_index") if isinstance(u.get("stage_index"), int) else None,
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


def _restore_profiles(db: DbSession, user: models.User, profiles: list, summary: dict) -> None:
    """供应商档案：空 key 导入（凭证不随备份走），一律置为未激活——
    用户在设置页补填 API Key 后手动激活，避免空 key 档案被误用。
    同名档案已存在则跳过（(user_id,name) 唯一约束）。"""
    existing = {
        r.name for r in db.query(models.LlmProviderProfile).filter_by(user_id=user.id).all()
    }
    for pr in profiles:
        if not isinstance(pr, dict):
            continue
        name = str(pr.get("name") or "")[:64]
        if not name:
            continue
        if name in existing:
            summary["skipped"].append(f"供应商档案「{name}」已存在，已跳过")
            continue
        db.add(
            models.LlmProviderProfile(
                user_id=user.id,
                name=name,
                base_url=str(pr.get("base_url") or "")[:512],
                api_key="",  # 凭证边界：不随备份导入
                model=str(pr.get("model") or "")[:128],
                is_active=False,
            )
        )
        existing.add(name)
        summary["provider_profiles"] += 1
