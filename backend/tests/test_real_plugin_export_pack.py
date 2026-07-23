"""study_export_pack 真实插件装载测试（API 稳定性验证）。

与单扩展点契约测试（test_plugin_exporters.py）的区别：本测试加载仓库 plugins/ 下的
**真实插件源码**（复制到临时目录 → 经 load_plugins 完整装载链路），用 TestClient 走
真实 HTTP 端点做端到端验证 + 边界情况——检验 exporters+events+storage+routes 在真实
复合场景下的稳定性。测试与插件物理分离（插件在 plugins/，测试在 backend/tests/）。
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient

# 仓库根 plugins 目录（真实插件源码所在）
_PLUGINS_SRC = Path(__file__).resolve().parents[2] / "plugins"

# 含嵌套知识图谱 / 阶段 / 测验的样例方案（覆盖三种导出格式的所有分支）
PLAN = {
    "title": "React 入门",
    "intent": {"topic": "React", "level": "零基础", "goal": "做项目", "daily_minutes": 90},
    "knowledge_map": {
        "nodes": [
            {"id": "n1", "name": "HTML", "difficulty": 2, "must_learn": True},
            {"id": "n2", "name": "组件", "difficulty": 4, "must_learn": True, "parent_id": "n1"},
        ],
        "edges": [{"from": "n1", "to": "n2", "relation": "前置"}],
    },
    "stages": [
        {
            "index": 1,
            "name": "基础",
            "goal": "HTML/CSS",
            "difficulty": 2,
            "estimated_weeks": 2,
            "topics": [{"name": "HTML", "why": "页面结构", "must_learn": True}],
            "milestone": "完成静态页面",
        }
    ],
    "quizzes": [
        {
            "node_id": "n2",
            "type": "choice",
            "question": "React 组件的本质是？",
            "options": ["函数/类", "CSS 规则"],
            "answer": "函数/类",
            "explanation": "组件是返回 UI 的函数或类。",
        }
    ],
}


# --- 辅助：加载真实插件到临时目录（隔离），返回 fresh app ---


def _load_real(name: str, tmp_path: Path):
    """把仓库 plugins/<name>.py 复制到 tmp_path 后经 load_plugins 真实装载。"""
    src = _PLUGINS_SRC / f"{name}.py"
    assert src.is_file(), f"真实插件源码缺失: {src}"
    shutil.copy2(src, tmp_path / f"{name}.py")
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    return app


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


def _seed_plan(content: dict) -> int:
    """直接往测试 DB 插一个方案（归属首个管理员 = test-token 认证用户），返回 plan_id。"""
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(models.User).filter_by(is_admin=True).first()
        if admin is None:
            admin = models.User(name="admin", username="admin", password_hash="x", is_admin=True)
            db.add(admin)
            db.commit()
        sess = models.Session(user_id=admin.id, title="导出测试会话")
        db.add(sess)
        db.commit()
        plan = models.Plan(session_id=sess.id, version=1, content=content)
        db.add(plan)
        db.commit()
        return plan.id
    finally:
        db.close()


# --- 装载与状态 ---


def test_loads(tmp_path):
    _load_real("study_export_pack", tmp_path)
    s = _status_map()["study_export_pack"]
    assert s.state == "loaded"
    assert set(s.exporters) == {"csv", "txt", "opml"}
    assert "/api/plugins/study_export_pack" in s.routes  # routes 记录挂载命名空间前缀
    assert "plan_created" in s.events


def test_formats_discovered(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    r = client.get("/api/export/formats", headers=auth_headers)
    assert r.status_code == 200, r.text
    plugin_fmts = {f["format"]: f for f in r.json()["plugin"]}
    assert {"csv", "txt", "opml"} <= set(plugin_fmts)
    assert plugin_fmts["csv"]["plugin_id"] == "study_export_pack"
    assert plugin_fmts["opml"]["label"]


# --- 三种导出格式端到端 ---


def test_export_csv(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan(PLAN)
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "csv"
    assert body["filename"].endswith(".csv")
    txt = body["content"]
    assert "node" in txt and "quiz" in txt  # 两段表头
    assert "HTML" in txt and "组件" in txt  # 知识点
    assert "React 组件的本质是？" in txt  # 测验题


def test_export_txt(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan(PLAN)
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "txt"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"].endswith(".txt")
    assert "[阶段 1]" in body["content"]
    assert "基础" in body["content"]
    assert "里程碑" in body["content"]


def test_export_opml(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan(PLAN)
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "opml"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"].endswith(".opml")
    root = ET.fromstring(body["content"])  # XML 必须合法可解析
    top = root.find("body").find("outline")
    assert top.get("text") == "React"  # 根节点 = 主题
    # 嵌套层级：HTML(root) -> 组件(子)
    html = next(o for o in top.findall("outline") if o.get("text") == "HTML")
    comp = html.find("outline")
    assert comp is not None and comp.get("text") == "组件"


# --- events + storage + routes 协同 ---


def test_stats_counts_plan_created_events(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    from app.plugins import bus

    bus.emit("plan_created", {"plan_id": 1, "session_id": 1, "version": 1, "topic": "x"})
    bus.emit("plan_created", {"plan_id": 2, "session_id": 1, "version": 1, "topic": "y"})
    client = TestClient(app)
    r = client.get("/api/plugins/study_export_pack/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["plan_count"] == 2  # events -> storage 持久化 -> routes 暴露
    assert set(data["formats"]) >= {"csv", "txt", "opml"}


def test_stats_persists_across_reload(tmp_path, auth_headers):
    """storage 落盘后，重置运行时再装载应读到累计值（模拟重启）。"""
    from app.plugins import bus
    from app.plugins.loader import reset_plugins

    _load_real("study_export_pack", tmp_path)
    bus.emit("plan_created", {"plan_id": 1, "session_id": 1, "version": 1, "topic": "x"})

    reset_plugins()  # 清运行时注册表（storage 文件保留在 data_dir）

    from app.main import create_app
    from app.plugins.loader import load_plugins

    app2 = create_app()
    load_plugins(app2, tmp_path)
    _seed_plan({})  # 建 admin 供 test-token 认证
    client = TestClient(app2)
    r = client.get("/api/plugins/study_export_pack/stats", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["plan_count"] == 1  # 跨"重启"读到持久化值


# --- 边界情况 ---


def test_edge_truly_empty_plan_rejected(tmp_path, auth_headers):
    """content={} 在端点层被 empty_content 拒绝（宿主保护，不到插件 handler）。"""
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan({})
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "csv"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_content"


def test_edge_minimal_plan_each_format(tmp_path, auth_headers):
    """结构部分缺失（仅 intent，无 stages/nodes/quizzes）三种格式都不崩（防御性 .get）。"""
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan({"intent": {"topic": "极简方案"}})
    for fmt in ("csv", "txt", "opml"):
        r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": fmt})
        assert r.status_code == 200, f"{fmt}: {r.text}"
        assert r.json()["content"]  # 非空最小产物（csv 表头 / txt 提示 / opml 根节点）


def test_edge_opml_with_orphan_parent(tmp_path, auth_headers):
    """parent_id 指向不存在节点 -> 该节点视为根（悬挂父不致崩溃）。"""
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan(
        {
            "intent": {"topic": "孤儿"},
            "knowledge_map": {
                "nodes": [
                    {"id": "a", "name": "A", "parent_id": "missing"},  # 悬挂父
                ]
            },
        }
    )
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "opml"})
    assert r.status_code == 200
    ET.fromstring(r.json()["content"])  # 仍合法 XML


def test_unknown_format_rejected(tmp_path, auth_headers):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    pid = _seed_plan(PLAN)
    r = client.post(f"/api/export/{pid}", headers=auth_headers, json={"format": "docx"})
    assert r.status_code == 400  # unsupported_format，不静默降级


def test_stats_requires_auth(tmp_path):
    app = _load_real("study_export_pack", tmp_path)
    client = TestClient(app)
    r = client.get("/api/plugins/study_export_pack/stats")
    assert r.status_code == 401  # 默认带宿主认证
