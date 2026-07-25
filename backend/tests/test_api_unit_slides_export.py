"""GET /materials/{unit_id}/export?format=html 测试（iter 359：自学单元逐个导出 HTML-PPT）。

确定性兜底（无 provider）与 LLM 路径（monkeypatch get_provider）均不联网。
模板经 monkeypatch skills_dir 指向仓库 vendored 的 ppt-slide-builder base.html
（同 test_api_export slides 测试口径）。
"""

from __future__ import annotations

from pathlib import Path

from app.db.base import SessionLocal
from app.llm.base import LLMResponse, LLMUsage

_REAL_SKILLS = str(Path(__file__).resolve().parents[2] / "skills")

_UNIT_MD = "### 概述\n\n这是单元概述。\n\n### 核心概念\n\n- 概念甲\n- 概念乙\n"


def _mk_admin() -> None:
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()


def _seed_unit(*, content: str = _UNIT_MD, title: str = "测试单元") -> dict:
    """建会话+方案+单个自学单元（带内容文件），返回 unit_id / plan_id / session_id。"""
    from app.core.storage import write_unit_content
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=1, title="单元导出会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id, version=1, content={"intent": {"topic": "导出主题"}, "stages": []}
        )
        db.add(plan)
        db.flush()
        u = models.StudyUnit(plan_id=plan.id, stage_index=0, title=title, order=0)
        db.add(u)
        db.flush()
        path = write_unit_content(sess.id, u.id, content)
        db.add(models.StudyMaterial(unit_id=u.id, content_md_path=str(path), size=len(content)))
        db.commit()
        return {"unit_id": u.id, "plan_id": plan.id, "session_id": sess.id}
    finally:
        db.close()


class _SlidesProvider:
    """单元幻灯片 LLM provider（返回 SlideSpec JSON）；duck-typed 最小实现。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        return self._r.pop(0) if self._r else LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))


def _no_provider(*a, **k):
    raise RuntimeError("ANTHROPIC_API_KEY 未配置")


def test_export_unit_html_deterministic(client, auth_headers, monkeypatch):
    """无 provider（无 key）-> 确定性兜底 default_spec_from_unit，200 + text/html + 单元内容。"""
    from app.api import materials as materials_mod
    from app.core import settings as settings_mod

    _mk_admin()
    seeded = _seed_unit()
    monkeypatch.setattr(materials_mod, "get_provider", _no_provider)
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)

    r = client.get(f"/api/materials/{seeded['unit_id']}/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and "filename*=UTF-8''" in cd and ".html" in cd
    html = r.content.decode("utf-8")
    assert "<html" in html
    assert "测试单元" in html  # 单元标题（标题页 + presentation title）
    assert "概念甲" in html  # 小节正文（确定性切分渲染）
    assert 'id="slide-1"' in html
    # 无 provider 走确定性是预期路径，不算降级 -> 无 X-Export-Warning
    assert "X-Export-Warning" not in r.headers


def test_export_unit_html_with_llm(client, auth_headers, monkeypatch):
    """有 provider + LLM 两阶段（build 草稿 + review 审校）-> 200 + html 含审校产物。"""
    from app.api import materials as materials_mod
    from app.core import settings as settings_mod

    _mk_admin()
    seeded = _seed_unit()
    draft_json = (
        '{"title":"测试单元","theme":{"--bg":"#0a0e1a","--accent":"#3b82f6"},'
        '"transition":"trans-fade","slides":['
        '{"title":"测试单元","content_html":"<p>草稿内容</p>","layout":"center","animation":"anim-fade-in"}]}'
    )
    reviewed_json = (
        '{"title":"测试单元","theme":{"--bg":"#0a0e1a","--accent":"#3b82f6"},'
        '"transition":"trans-fade","slides":['
        '{"title":"测试单元","content_html":"<p>审校后内容</p>","layout":"center","animation":"anim-fade-in"},'
        '{"title":"要点","content_html":"<ul><li>审校补充</li></ul>","layout":"left","animation":"anim-slide-up"}]}'
    )
    monkeypatch.setattr(
        materials_mod,
        "get_provider",
        lambda *a, **k: _SlidesProvider(
            [
                LLMResponse(content=draft_json, usage=LLMUsage(model="claude-sonnet-5")),
                LLMResponse(content=reviewed_json, usage=LLMUsage(model="claude-sonnet-5")),
            ]
        ),
    )
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)

    r = client.get(f"/api/materials/{seeded['unit_id']}/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    html = r.content.decode("utf-8")
    assert "审校后内容" in html  # 采用 review 产物
    assert "审校补充" in html
    assert "草稿内容" not in html  # 草稿已被审校替换
    assert "X-Export-Warning" not in r.headers  # 两阶段皆 ok 不降级


def test_export_unit_html_llm_degrade_warns(client, auth_headers, monkeypatch):
    """有 provider 但 LLM 持续非法 -> 降级确定性 + X-Export-Warning（区别于无 key 的预期降级）。"""
    from app.api import materials as materials_mod
    from app.core import settings as settings_mod

    _mk_admin()
    seeded = _seed_unit()
    monkeypatch.setattr(
        materials_mod,
        "get_provider",
        lambda *a, **k: _SlidesProvider([LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5"))]),
    )
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)

    r = client.get(f"/api/materials/{seeded['unit_id']}/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "X-Export-Warning" in r.headers
    html = r.content.decode("utf-8")
    assert "概念甲" in html  # 降级确定性切分仍渲染单元内容


def test_export_unit_invalid_format_400(client, auth_headers, monkeypatch):
    from app.core import settings as settings_mod

    _mk_admin()
    seeded = _seed_unit()
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    r = client.get(f"/api/materials/{seeded['unit_id']}/export?format=docx", headers=auth_headers)
    assert r.status_code == 400


def test_export_unit_not_found(client, auth_headers):
    _mk_admin()
    r = client.get("/api/materials/999999/export?format=html", headers=auth_headers)
    assert r.status_code == 404


def test_export_unit_requires_auth(client):
    _mk_admin()
    seeded = _seed_unit()
    assert client.get(f"/api/materials/{seeded['unit_id']}/export").status_code == 401


def test_export_unit_isolation_other_user(client, auth_headers):
    """他人单元 -> 404（防枚举）。"""
    from app.db import models
    from app.db.models import User

    _mk_admin()
    db = SessionLocal()
    try:
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add(other)
        db.flush()
        sess = models.Session(user_id=other.id, title="他人会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "t"}})
        db.add(plan)
        db.flush()
        u = models.StudyUnit(plan_id=plan.id, stage_index=0, title="他人单元", order=0)
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()
    r = client.get(f"/api/materials/{uid}/export?format=html", headers=auth_headers)
    assert r.status_code == 404


def test_export_unit_no_materials_still_renders(client, auth_headers, monkeypatch):
    """单元无 StudyMaterial 行（无内容文件）-> 仍 200（仅标题页，不崩）。"""
    from app.db import models
    from app.core import settings as settings_mod

    _mk_admin()
    db = SessionLocal()
    try:
        sess = models.Session(user_id=1, title="空单元会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "t"}})
        db.add(plan)
        db.flush()
        u = models.StudyUnit(plan_id=plan.id, stage_index=0, title="空内容单元", order=0)
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()
    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    r = client.get(f"/api/materials/{uid}/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "空内容单元" in r.content.decode("utf-8")
