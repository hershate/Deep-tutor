"""iter 369 第三轮审计 B12：导出与工具面修复回归。

覆盖：
- F1 GET 材料导出/单元 PPT 导出补限流 + html 导出逐单元 llm_task_slot（与 POST /api/export 同口径）
- F2 robots.txt 流式抓取 + 64KB 截断（防巨型 robots 内存放大）
- F6 _search_web 进程级抓取槽（跨 pipeline/材料流共享上界）
- F7 build_ics UID 按日稳定（重导出幂等，不再产生重复日历事件）
- F8 文件名清洗剔除控制字符（zip 条目名/下载文件名）
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from pathlib import Path

from app.db.base import SessionLocal

# conftest 的 autouse _no_network_search 会把模块属性 _search_web 换成 noop——
# 这里要在收集期绑定原函数以测其内部（抓取槽）逻辑。
import app.tools.search_resources as _sr_mod

_REAL_SEARCH_WEB = _sr_mod._search_web

_REAL_SKILLS = str(Path(__file__).resolve().parents[2] / "skills")


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()


def _seed(units: int = 2) -> dict:
    from app.core.storage import write_unit_content
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=1, title="导出会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id, version=1,
            content={"intent": {"topic": "导出主题"}, "stages": []},
        )
        db.add(plan)
        db.flush()
        for i in range(units):
            u = models.StudyUnit(plan_id=plan.id, stage_index=i, title=f"第{i + 1}单元", order=i)
            db.add(u)
            db.flush()
            path = write_unit_content(sess.id, u.id, f"# 第{i + 1}单元\n\n内容{i}")
            db.add(models.StudyMaterial(unit_id=u.id, content_md_path=str(path), size=10))
        db.commit()
        return {"plan_id": plan.id, "unit_id": u.id}
    finally:
        db.close()


# --- F1：GET 导出端点限流（与 POST /api/export 共用 export:{uid} 键） ---


def test_export_get_endpoints_rate_limited(client, auth_headers):
    """GET /plans/{id}/materials/export 与 GET /materials/{unit_id}/export 此前零限流——
    限流检查在归属查询之前，共用 12/min 预算。"""
    _mk_admin()
    # 12 次打满预算（404 也计数：限流先于归属查询）
    for _ in range(12):
        assert client.get("/api/plans/999999/materials/export", headers=auth_headers).status_code == 404
    # 第 13 次：同键两端口径一致 -> 429
    r = client.get("/api/plans/999999/materials/export", headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    # 单元 PPT 导出共享同一 export:{uid} 预算
    r2 = client.get("/api/materials/999999/export", headers=auth_headers)
    assert r2.status_code == 429


def test_unit_export_rate_limited_alone(client, auth_headers):
    _mk_admin()
    for _ in range(12):
        assert client.get("/api/materials/999999/export", headers=auth_headers).status_code == 404
    assert client.get("/api/materials/999999/export", headers=auth_headers).status_code == 429


# --- F1：html 导出逐单元 llm_task_slot（此前绕过全局 LLM 背压） ---


def test_html_zip_export_uses_llm_slot_per_unit(client, auth_headers, monkeypatch):
    from app.core import settings as settings_mod
    import app.api.plans as plans_mod

    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    orig = plans_mod.llm_task_slot
    calls = []

    def spy():
        calls.append(1)
        return orig()

    monkeypatch.setattr(plans_mod, "llm_task_slot", spy)
    _mk_admin()
    seeded = _seed(2)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(zf.namelist()) == 2
    assert len(calls) == 2  # 每单元一次槽获取（每单元 2 次 LLM：build + review）


def test_unit_export_uses_llm_slot(client, auth_headers, monkeypatch):
    from app.core import settings as settings_mod
    from app.core import concurrency as conc

    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    orig = conc.llm_task_slot
    calls = []

    def spy():
        calls.append(1)
        return orig()

    monkeypatch.setattr(conc, "llm_task_slot", spy)
    _mk_admin()
    seeded = _seed(1)
    r = client.get(f"/api/materials/{seeded['unit_id']}/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert calls  # 单元 PPT 导出的 LLM 两阶段已入全局背压槽


# --- F2：robots.txt 流式 + 64KB 截断 ---


class _FakeRobotsResp:
    def __init__(self, owner: "_FakeRobotsClient"):
        self.status_code = 200
        self._owner = owner

    async def aiter_bytes(self, _n: int):
        for chunk in self._owner.chunks:
            self._owner.consumed += len(chunk)
            yield chunk


class _FakeRobotsCM:
    def __init__(self, resp: _FakeRobotsResp):
        self._resp = resp

    async def __aenter__(self) -> _FakeRobotsResp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeRobotsClient:
    """鸭子类型 httpx.AsyncClient：仅实现 _robots_allows 用到的 stream()。"""

    def __init__(self, body: bytes):
        self.chunks = [body[i : i + 8192] for i in range(0, len(body), 8192)]
        self.consumed = 0

    def stream(self, method: str, url: str, timeout=None):
        return _FakeRobotsCM(_FakeRobotsResp(self))


def _clear_robots_cache(host: str) -> None:
    from app.tools.verify_url import _robots_cache

    _robots_cache.pop(host, None)


def test_robots_fetch_truncated_at_cap():
    """>64KB 的巨型 robots：按 64KB 前缀截断解析（Disallow 仍生效），不读全量。"""
    from app.tools.verify_url import _ROBOTS_MAX_BYTES, _robots_allows

    pad = b"# " + b"x" * 100 + b"\n" * 1
    body = b"User-agent: *\nDisallow: /blocked\n\n" + pad * 4000  # ~400KB
    assert len(body) > _ROBOTS_MAX_BYTES * 3
    client = _FakeRobotsClient(body)
    host = "huge-robots.example.com"
    _clear_robots_cache(host)
    allowed = asyncio.run(_robots_allows(client, f"https://{host}/blocked/page"))
    assert allowed is False  # 前缀中的 Disallow 规则生效
    assert client.consumed <= _ROBOTS_MAX_BYTES + 8192  # 截断即停（余量一个 chunk）
    assert client.consumed < len(body)
    _clear_robots_cache(host)


def test_robots_fetch_allows_normal():
    from app.tools.verify_url import _robots_allows

    body = b"User-agent: *\nDisallow: /private\n"
    client = _FakeRobotsClient(body)
    host = "normal-robots.example.com"
    _clear_robots_cache(host)
    allowed = asyncio.run(_robots_allows(client, f"https://{host}/public/page"))
    assert allowed is True
    assert client.consumed == len(body)
    _clear_robots_cache(host)


# --- F6：_search_web 进程级抓取槽 ---


def test_search_web_global_slot_bounds_concurrency(monkeypatch):
    """跨调用共享的进程级上界：6 路并发 _search_web 在槽=2 下并发不超过 2。"""
    import app.tools.search_resources as sr
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "max_concurrent_search_fetches", 2)
    state = {"cur": 0, "max": 0}

    async def fake_engine(query: str, count: int = 5, key: str = ""):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)
        state["cur"] -= 1
        return [{"title": query, "url": "https://example.com/r"}]

    monkeypatch.setitem(sr._ENGINE_FUNCS, "bing_web", fake_engine)

    async def run():
        await asyncio.gather(*[_REAL_SEARCH_WEB(f"q{i}", "bing_web") for i in range(6)])

    asyncio.run(run())
    assert state["max"] == 2  # 有界且真实排队（6 任务 > 槽 2）


# --- F7：ics UID 按日稳定 ---


def test_ics_uid_stable_across_reschedule():
    """同方案同日 -> 同 UID（重导出幂等）；同日多条目 UID 互异。"""
    from app.tools.build_ics import build_ics

    content = {
        "title": "T",
        "schedule": [
            {"date": "2026-01-05", "tasks": ["a"]},
            {"date": "2026-01-06", "tasks": ["b"]},
        ],
    }
    ics1, e1, _ = build_ics(7, content)
    uids1 = {ln for ln in ics1.splitlines() if ln.startswith("UID:")}
    assert e1 == 2

    # 重生成：头部插入新日期（原条目下标平移——旧实现 UID 会全变）
    content["schedule"].insert(0, {"date": "2026-01-01", "tasks": ["x"]})
    ics2, _, _ = build_ics(7, content)
    uids2 = {ln for ln in ics2.splitlines() if ln.startswith("UID:")}
    assert uids1 <= uids2, "原日期的 UID 必须保持不变（重导出不产生重复日历事件）"

    # 同日两条目：UID 互异且可复现
    dup = {"schedule": [{"date": "2026-02-01", "tasks": ["m"]}, {"date": "2026-02-01", "tasks": ["n"]}]}
    ics3, e3, _ = build_ics(9, dup)
    assert e3 == 2
    uids3 = [ln for ln in ics3.splitlines() if ln.startswith("UID:")]
    assert len(set(uids3)) == 2
    ics3b, _, _ = build_ics(9, dup)
    assert [ln for ln in ics3b.splitlines() if ln.startswith("UID:")] == uids3


# --- F8：文件名清洗剔除控制字符 ---


def test_safe_download_names_strip_control_chars():
    from app.api.materials import _safe_unit_filename
    from app.api.plans import _safe_download_name

    bad = "标题\n带换行\x00与制表\t符"
    for cleaner in (_safe_download_name, _safe_unit_filename):
        out = cleaner(bad)
        assert not re.search(r"[\x00-\x1f\x7f]", out), out
        assert "\n" not in out and "\x00" not in out
        assert "标题" in out and "符" in out  # 正文保留
