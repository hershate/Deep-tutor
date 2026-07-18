"""全案自学材料 zip 导出测试（iter 99）。"""

from __future__ import annotations

import io
import zipfile

from app.db.base import SessionLocal


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
