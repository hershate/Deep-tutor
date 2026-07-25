"""全案自学材料 zip 导出测试（iter 99）。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.db.base import SessionLocal

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
    from app.core.storage import materials_dir, write_unit_content
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
        (materials_dir(sess.id) / "index.md").write_text("# 路线图", encoding="utf-8")
        return {"plan_id": plan.id}
    finally:
        db.close()


def test_export_zip_contents(client, auth_headers):
    _mk_admin()
    seeded = _seed(2)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and "filename*=UTF-8''" in cd  # 中文名 RFC 5987

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["00-路线图.md", "01-第1单元.md", "02-第2单元.md"]
    assert zf.read("00-路线图.md").decode("utf-8") == "# 路线图"
    assert "内容0" in zf.read("01-第1单元.md").decode("utf-8")
    assert "内容1" in zf.read("02-第2单元.md").decode("utf-8")


def test_export_zip_404_when_no_units(client, auth_headers):
    _mk_admin()
    seeded = _seed(0)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export", headers=auth_headers)
    assert r.status_code == 404


def test_export_zip_requires_auth(client):
    _mk_admin()
    seeded = _seed(1)
    assert client.get(f"/api/plans/{seeded['plan_id']}/materials/export").status_code == 401


def test_export_md_contents(client, auth_headers):
    """iter 354 BUG3：format=md -> 单个 Markdown 文件（index + 各单元拼接）。"""
    _mk_admin()
    seeded = _seed(2)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=md", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and "filename*=UTF-8''" in cd and ".md" in cd
    text = r.content.decode("utf-8")
    assert "导出主题 — 自学材料" in text  # 标题（来自 intent.topic）
    assert "# 路线图" in text  # index 文档拼入
    assert "## 单元 1：第1单元" in text and "内容0" in text
    assert "## 单元 2：第2单元" in text and "内容1" in text


def test_export_pdf_or_degrade(client, auth_headers):
    """iter 354 BUG3：format=pdf -> weasyprint 可用则 application/pdf，否则降级 md + X-Export-Warning。"""
    from app.tools.export_pdf import pdf_available

    _mk_admin()
    seeded = _seed(2)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=pdf", headers=auth_headers)
    assert r.status_code == 200, r.text
    if pdf_available():
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"
        assert ".pdf" in r.headers["content-disposition"]
    else:
        # R9 降级：weasyprint 缺失 -> md + 警告头（与 exports.py _export_pdf 同模式）
        assert r.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "X-Export-Warning" in r.headers
        assert "导出主题 — 自学材料" in r.content.decode("utf-8")


def test_export_html_zip_contents(client, auth_headers, monkeypatch):
    """iter 359：format=html -> 每单元一个自包含 HTML 幻灯片打包 zip（借助 ppt-slide-builder）。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)  # base.html 模板
    _mk_admin()
    seeded = _seed(2)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and "html" in cd  # ASCII 回退名 materials-<id>-html.zip
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["01-第1单元.html", "02-第2单元.html"]
    h1 = zf.read("01-第1单元.html").decode("utf-8")
    assert "<html" in h1  # 自包含 HTML 文档
    assert "第1单元" in h1  # 单元标题（标题页）
    assert "内容0" in h1  # 单元正文
    assert 'id="slide-1"' in h1  # 幻灯片结构


def test_export_html_zip_with_llm(client, auth_headers, monkeypatch):
    """iter 359 续：format=html + LLM -> 每单元走 build+review 两阶段，zip 含审校产物。"""
    from app.api import plans as plans_mod
    from app.core import settings as settings_mod
    from app.llm.base import LLMResponse, LLMUsage

    monkeypatch.setattr(settings_mod.settings, "skills_dir", _REAL_SKILLS)
    _mk_admin()
    seeded = _seed(2)

    class _P:
        def __init__(self, responses):
            self._r = list(responses)

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return self._r.pop(0) if self._r else LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))

    def _spec(n: int, label: str) -> str:
        # 用 % 格式化：JSON 花括号是字面量，仅 %s/%d 为占位（.format 会误读 { "title" }）
        return (
            '{"title":"第%d单元","theme":{},"transition":"trans-fade","slides":['
            '{"title":"第%d单元","content_html":"<p>%s%d</p>","layout":"center","animation":"anim-fade-in"}]}'
            % (n, n, label, n)
        )

    # 顺序：u1-build, u1-review, u2-build, u2-review（串行）
    responses = [
        LLMResponse(content=_spec(1, "草稿"), usage=LLMUsage(model="claude-sonnet-5")),
        LLMResponse(content=_spec(1, "审校"), usage=LLMUsage(model="claude-sonnet-5")),
        LLMResponse(content=_spec(2, "草稿"), usage=LLMUsage(model="claude-sonnet-5")),
        LLMResponse(content=_spec(2, "审校"), usage=LLMUsage(model="claude-sonnet-5")),
    ]
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _P(responses))

    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=html", headers=auth_headers)
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    h1 = zf.read("01-第1单元.html").decode("utf-8")
    h2 = zf.read("02-第2单元.html").decode("utf-8")
    assert "审校1" in h1 and "草稿1" not in h1  # 采用 review 产物
    assert "审校2" in h2 and "草稿2" not in h2


def test_export_invalid_format_400(client, auth_headers):
    """iter 354 BUG3：不支持格式 -> 400。"""
    _mk_admin()
    seeded = _seed(1)
    r = client.get(f"/api/plans/{seeded['plan_id']}/materials/export?format=docx", headers=auth_headers)
    assert r.status_code == 400


def test_export_zip_isolation_other_user(client, auth_headers):
    """他人方案 -> 404（防枚举）。"""
    from app.db import models
    from app.db.models import User

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
        db.commit()
        pid = plan.id
    finally:
        db.close()
    r = client.get(f"/api/plans/{pid}/materials/export", headers=auth_headers)
    assert r.status_code == 404
