"""iter 372 R11：限流矩阵终检回归——生成族起始频控 + kb 一键建会话与 /sessions 共桶。

全仓写端点 × limiter.check 矩阵扫描（R11-a）发现两处昂贵路径无频控：
- plans generate/retry/clarify/regenerate/materials：并发配额只约束「同时运行数」，
  快败循环（error 终态→立即重试）每轮 _load_parsed_materials + 状态写入，无频次上界；
- kb POST /generate 建会话（另加逐文档复制资料+文件写，单次成本高于普通建会话），
  却不受 POST /sessions 的 sess_create 桶约束——不共桶可叠加绕出 60/min。
其余未挂限流的写端点均为幂等单行写（认证+属主校验覆盖），矩阵结论见 stage.md R11。
"""

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)


def _mk_user(name: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, create_token(u.id, name)
    finally:
        db.close()


def test_generate_family_rate_limited_shared_bucket():
    uid, tok = _mk_user("rate_gen_a")
    h = {"Authorization": f"Bearer {tok}"}
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="频控")
        db.add(sess)
        db.commit()
        db.refresh(sess)
        sid = sess.id
    finally:
        db.close()

    # 12/min：前 12 次放行（无 provider key -> 每次快败 error 终态，即被保护的快败循环形态）
    for i in range(12):
        r = client.post("/api/plans/generate", json={"session_id": sid}, headers=h)
        assert r.status_code == 200, (i, r.status_code, r.text[:200])
    # 第 13 次 -> 429
    r = client.post("/api/plans/generate", json={"session_id": sid}, headers=h)
    assert r.status_code == 429, r.status_code

    # 同桶跨族：materials 的 LLM 新生成路径（refresh=True 绕过幂等缓存）也被同一桶挡住
    db = SessionLocal()
    try:
        plan = models.Plan(session_id=sid, version=1, content={"topic": "T"})
        db.add(plan)
        db.commit()
        db.refresh(plan)
        pid = plan.id
    finally:
        db.close()
    r = client.post(f"/api/plans/{pid}/materials", json={"refresh": True}, headers=h)
    assert r.status_code == 429, r.status_code

    # 分用户隔离：另一用户不受影响
    uid_b, tok_b = _mk_user("rate_gen_b")
    db = SessionLocal()
    try:
        sess_b = models.Session(user_id=uid_b, title="B")
        db.add(sess_b)
        db.commit()
        db.refresh(sess_b)
        sid_b = sess_b.id
    finally:
        db.close()
    r = client.post("/api/plans/generate", json={"session_id": sid_b},
                    headers={"Authorization": f"Bearer {tok_b}"})
    assert r.status_code == 200, r.status_code


def test_kb_generate_shares_sess_create_bucket(tmp_path):
    uid, tok = _mk_user("rate_kb")
    h = {"Authorization": f"Bearer {tok}"}
    parsed = tmp_path / "parsed.txt"
    parsed.write_text("知识库频控测试正文", encoding="utf-8")
    db = SessionLocal()
    try:
        doc = models.KbDocument(
            user_id=uid, title="kb 频控", kind=models.MaterialKind.text,
            source="测试", status="parsed", parsed_text_path=str(parsed),
            content_hash="hash_rate_kb_372",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    # 先经 POST /api/sessions 耗尽 sess_create 桶（30/min）
    for i in range(30):
        r = client.post("/api/sessions", json={"title": "x"}, headers=h)
        assert r.status_code in (200, 201), (i, r.status_code)
    # kb /generate 同桶 -> 立即 429（若为独立桶则会放行，此断言即失效）
    r = client.post("/api/kb/generate", json={"document_ids": [doc_id]}, headers=h)
    assert r.status_code == 429, r.status_code
