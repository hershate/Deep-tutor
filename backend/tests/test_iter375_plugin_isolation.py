"""iter 375 R18：插件面多用户隔离回归。

授权矩阵审计（tests/auth_matrix.py）发现两处插件端点隔离缺口：
- study_stats /dashboard：插件 storage 为每插件全局 KV，此前把所有用户学习分钟/答题数
  聚成一份给任意认证用户看（跨用户隐私泄露；docstring 却宣称「我全局学了多久」）。
  iter 375 起按 days:{uid} 等键分用户记账——本文件实证双用户互不可见。
- security_guard /stats：全站路径->频次画像对普通用户收紧为管理员（403）。
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

_PLUGINS_SRC = Path(__file__).resolve().parents[2] / "plugins"


def _load_real(name: str, tmp_path: Path):
    src = _PLUGINS_SRC / f"{name}.py"
    assert src.is_file(), f"真实插件源码缺失: {src}"
    shutil.copy2(src, tmp_path / f"{name}.py")
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    return app


def _mk_user(name: str, admin: bool = False) -> tuple[int, dict]:
    from app.core.security import create_token
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x", is_admin=admin)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, {"Authorization": f"Bearer {create_token(u.id, name)}"}
    finally:
        db.close()


# --- study_stats：双用户互不可见（iter 375 核心）---


def test_study_stats_per_user_isolation(tmp_path):
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    uid_a, h_a = _mk_user("stat_a")
    uid_b, h_b = _mk_user("stat_b")
    today = date.today().isoformat()
    bus.emit("learning_log_added", {"date": today, "minutes": 45, "user_id": uid_a, "plan_id": 1})
    bus.emit("learning_log_added", {"date": today, "minutes": 10, "user_id": uid_b, "plan_id": 1})
    bus.emit("quiz_submitted", {"correct": True, "user_id": uid_a})
    bus.emit("progress_updated", {"user_id": uid_b})

    client = TestClient(app)
    da = client.get("/api/plugins/study_stats/dashboard", headers=h_a).json()
    db_ = client.get("/api/plugins/study_stats/dashboard", headers=h_b).json()
    assert da["total_minutes"] == 45 and da["today_minutes"] == 45, da
    assert da["quiz_count"] == 1 and da["progress_updates"] == 0, da
    assert db_["total_minutes"] == 10, db_
    assert db_["quiz_count"] == 0 and db_["progress_updates"] == 1, db_
    # streak 各算各的（B 只有一天也算 1，但 A 的 45 分钟不影响 B）
    assert da["streak"] == 1 and db_["streak"] == 1


def test_study_stats_missing_user_id_not_visible(tmp_path):
    """无 user_id 的载荷记到 uid=0，不混入任何真实用户视图（核心发射点均带
    user_id；此为防御语义锚定）。"""
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    uid_a, h_a = _mk_user("stat_c")
    bus.emit("learning_log_added", {"date": date.today().isoformat(), "minutes": 99})
    client = TestClient(app)
    d = client.get("/api/plugins/study_stats/dashboard", headers=h_a).json()
    assert d["total_minutes"] == 0, d


# --- security_guard：stats 收紧管理员 ---


def test_security_guard_stats_admin_only(tmp_path):
    app = _load_real("security_guard", tmp_path)
    _, h_user = _mk_user("guard_user")
    _, h_admin = _mk_user("guard_admin", admin=True)
    client = TestClient(app)
    r = client.get("/api/plugins/security_guard/stats", headers=h_user)
    assert r.status_code == 403, r.text
    r = client.get("/api/plugins/security_guard/stats", headers=h_admin)
    assert r.status_code == 200, r.text
    assert "request_counts" in r.json()
