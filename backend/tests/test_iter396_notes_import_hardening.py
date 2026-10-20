"""iter 396 回归：自由笔记导入路径加固（配额竞态 + zip 解压内存放大 + 导出同名）。

对应本轮 notes.py 的四处修复：

1. **N1 配额竞态**——``POST /api/notes/import`` 此前在函数开头数一次 ``existing``
   就用 ``existing + imported`` 判定上限，全程不持 ``quota_slot``、不在提交前复查：
   两个并发 import（或 import 与并发 create_note）各自看到同一 ``existing``
   -> 实际落库可达 ~2 倍上限（500 条上限形同虚设）。现把「计数 + 批量插入 + 提交」
   放进同一把 ``quota_slot("note:{user.id}")``，计数建立在已提交的可见状态上。
2. **N3 解压内存放大**——``zf.read(name)`` 走 ``ZipExtFile._read1(MAX_N=1<<31)``，
   zlib 会把整个 deflate 成员一次性展开（**与中央目录声明的 file_size 无关**，
   声明值可谎报）。实测：97KB 请求体 -> 峰值分配 204.8MiB；分块读后 0.3MiB。
3. **N6 超总预算不停手**——达到解压总量上限后不再遍历并解压后续条目。
4. **N4 导出同名条目**——重名去重后仍可能撞名（另一篇标题恰为 "<id>-<stem>"），
   解压时后者覆盖前者 -> 该篇笔记静默丢失。
"""

from __future__ import annotations

import io
import struct
import tracemalloc
import zipfile
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _md_bytes(title: str, body: str = "内容") -> bytes:
    return f"# {title}\n\n{body}".encode("utf-8")


def _zip_bytes(entries: list[tuple[str, bytes]], *, compress: bool = True) -> bytes:
    buf = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _zip_with_lying_size(entry_name: str, payload: bytes, declared: int) -> bytes:
    """构造中央目录**谎报** uncompressed size 的 zip。

    攻击者可在中央目录里把 file_size 写小——服务端若拿声明值当「这条多大」的依据，
    就绕过了单条大小判定，而实际解压量由 deflate 成员内容决定。
    """
    raw = bytearray(_zip_bytes([(entry_name, payload)]))
    eocd = raw.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    struct.pack_into("<I", raw, cd_offset + 24, declared)
    return bytes(raw)


def _ensure_user(client, auth_headers) -> int:
    """先发一次已认证请求（AUTH_TOKEN 回退会懒建默认用户），返回用户 id。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    client.get("/api/notes", headers=auth_headers)
    db = SessionLocal()
    try:
        u = db.query(User).order_by(User.id).first()
        assert u is not None, "已认证请求后应存在用户"
        return u.id
    finally:
        db.close()


def _seed_notes(uid: int, count: int) -> None:
    """直接写库预置笔记（绕开配额端点，只为构造「已接近上限」的前置状态）。"""
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        db.add_all([models.Note(user_id=uid, title=f"预置-{i}", content="x") for i in range(count)])
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. 配额竞态闭合
# ---------------------------------------------------------------------------


def test_import_quota_atomic_under_concurrency(client, auth_headers):
    """并发导入不得突破 500 条上限（修复前：490 + 10 + 10 = 510）。"""
    from app.db import models
    from app.db.base import SessionLocal

    uid = _ensure_user(client, auth_headers)
    _seed_notes(uid, 490)

    def _import_ten() -> int:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for i in range(10):
                zf.writestr(f"n{i}.md", _md_bytes(f"并发-{i}"))
        buf.seek(0)
        r = client.post(
            "/api/notes/import",
            files=[("files", ("notes.zip", buf, "application/zip"))],
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        return r.json()["imported"]

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: _import_ten(), range(2)))

    db = SessionLocal()
    try:
        total = db.query(models.Note).filter_by(user_id=db.query(models.User).first().id).count()
    finally:
        db.close()
    assert total <= 500, f"并发导入突破笔记配额：落库 {total} 条（导入数 {results}）"
    assert total == 500, f"应恰好填满到 500，实际 {total}（导入数 {results}）"


def test_import_respects_existing_quota(client, auth_headers):
    """已满 500 条时导入不落库，且报告 quota_full。"""
    from app.db import models
    from app.db.base import SessionLocal

    uid = _ensure_user(client, auth_headers)
    _seed_notes(uid, 500)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("n.md", _md_bytes("再多一篇"))
    buf.seek(0)
    r = client.post(
        "/api/notes/import",
        files=[("files", ("notes.zip", buf, "application/zip"))],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["imported"] == 0
    assert body["quota_full"] is True

    db = SessionLocal()
    try:
        total = db.query(models.Note).filter_by(user_id=uid).count()
    finally:
        db.close()
    assert total == 500, total


# ---------------------------------------------------------------------------
# 2. zip 解压内存放大封死
# ---------------------------------------------------------------------------


def test_zip_lying_size_does_not_amplify_memory():
    """谎报声明值的条目：解析峰值内存必须受控（修复前 ~205MiB）。

    直接对纯解析助手 ``_parse_import_payloads`` 计量（无 DB、无网络）——
    峰值分配取自 tracemalloc，与旧实现（zf.read）的 204.8MiB 对比。
    """
    from app.api.notes import _parse_import_payloads

    # 100MB 零字节（deflate 后约 100KB），中央目录谎报 1000 字节
    payload = b"\0" * 100_000_000
    data = _zip_with_lying_size("n.md", payload, 1000)

    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    rows, skipped = _parse_import_payloads([("notes.zip", data)])
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mib = (peak - base) / 1024 / 1024
    assert peak_mib < 16, f"解析峰值内存 {peak_mib:.1f} MiB 过大（修复前 ~205MiB）"
    assert rows == [], "谎报声明值的大条目应被按实际字节判为超限而跳过"
    assert skipped == 1, skipped


def test_zip_oversized_entry_skipped_by_actual_bytes(client, auth_headers):
    """超单条上限的条目按**实际**解压字节跳过（不信声明值），且不影响其余条目。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.md", b"\0" * 2_000_000)  # 实际 2MB > 100KB 单条上限
        zf.writestr("ok.md", _md_bytes("正常条目"))
    buf.seek(0)
    r = client.post(
        "/api/notes/import",
        files=[("files", ("notes.zip", buf, "application/zip"))],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["imported"] == 1, body
    assert body["skipped"] == 1, body
    titles = {n["title"] for n in client.get("/api/notes", headers=auth_headers).json()["items"]}
    assert "正常条目" in titles


def test_zip_total_byte_cap_stops_early():
    """超总解压预算即停：余下条目不解压，且计入 skipped。"""
    from app.api.notes import _IMPORT_TOTAL_BYTES_CAP, _parse_import_payloads

    entries = [(f"n{i}.md", b"A" * 100_000) for i in range(100)]  # 共 10MB > 5MB 上限
    data = _zip_bytes(entries)

    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    rows, skipped = _parse_import_payloads([("notes.zip", data)])
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_read = sum(len(r[1]) for r in rows)  # rows 只含 title/content，不含原始字节
    assert len(rows) <= _IMPORT_TOTAL_BYTES_CAP // 100_000 + 1, len(rows)
    assert skipped == 100 - len(rows), f"跳过的条目数应与未导入数一致：skipped={skipped} rows={len(rows)}"
    assert (peak - base) / 1024 / 1024 < 32, "超预算后仍在大量解压"
    assert total_read >= 0


# ---------------------------------------------------------------------------
# 3. 导出 zip 条目名唯一
# ---------------------------------------------------------------------------


def test_export_zip_entry_names_are_unique(client, auth_headers):
    """重名笔记导出的 zip 内条目名必须唯一（解压时后者覆盖前者 = 笔记丢失）。"""
    from app.db import models
    from app.db.base import SessionLocal

    uid = _ensure_user(client, auth_headers)
    db = SessionLocal()
    try:
        # 两篇同名 + 一篇标题恰为 "<id>-<stem>"（构造去重后再次撞名的场景）
        n1 = models.Note(user_id=uid, title="同名", content="a")
        n2 = models.Note(user_id=uid, title="同名", content="b")
        db.add_all([n1, n2])
        db.flush()
        db.add(models.Note(user_id=uid, title=f"{n1.id}-同名", content="c"))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/notes/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert len(names) == len(set(names)), f"导出 zip 存在同名条目：{names}"
