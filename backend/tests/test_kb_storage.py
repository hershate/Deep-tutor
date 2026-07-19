"""知识库存储辅助（app.core.storage kb 族，iter 212）+ KbDocument 级联测试。"""

from __future__ import annotations

import pytest

from app.core import storage


# --- 路径防护（R20） ---


def test_kb_root_rejects_traversal():
    with pytest.raises(ValueError):
        storage._kb_root("../evil")
    with pytest.raises(ValueError):
        storage._kb_root("a/b")
    with pytest.raises(ValueError):
        storage.delete_kb_files("..")


def test_kb_root_inside_data_dir_lazy():
    root = storage._root()
    base = storage._kb_root(1)
    assert base == root / "kb" / "1"
    # _kb_root 本身不建目录（惰性，目录由 *_dir 助手创建）
    assert not base.exists()


# --- 读写 roundtrip ---


def test_kb_upload_roundtrip():
    path = storage.save_kb_upload(1, 5, b"hello", "PDF")
    assert path.name == "5.pdf"  # 扩展名清洗（小写、限字母数字）
    assert path.read_bytes() == b"hello"


def test_kb_parsed_roundtrip():
    path = storage.write_kb_parsed_text(1, 5, "解析文本")
    assert path.name == "5.txt"
    assert path.read_text(encoding="utf-8") == "解析文本"


# --- 删除 ---


def test_delete_kb_document_files():
    storage.save_kb_upload(1, 5, b"x", "txt")
    storage.write_kb_parsed_text(1, 5, "y")
    storage.save_kb_upload(1, 50, b"z", "txt")  # 同用户其他文档不受影响
    storage.delete_kb_document_files(1, 5)
    originals = storage._kb_root(1) / "originals"
    assert list(originals.glob("5.*")) == []  # "5.*" 不误中 "50.*"
    assert (originals / "50.txt").exists()
    assert not (storage._kb_root(1) / "parsed" / "5.txt").exists()
    storage.delete_kb_document_files(1, 5)  # 幂等：再删一次不报错


def test_delete_kb_files_removes_tree():
    storage.save_kb_upload(1, 5, b"x", "txt")
    storage.write_kb_parsed_text(1, 5, "y")
    storage.delete_kb_files(1)
    assert not storage._kb_root(1).exists()
    storage.delete_kb_files(2)  # 不存在时静默


# --- ORM 级联（删用户 -> kb_documents 行随之删除） ---


def test_user_delete_cascades_kb_documents():
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        u = models.User(name="kb_user", username="kb_user")
        db.add(u)
        db.flush()
        doc = models.KbDocument(
            user_id=u.id,
            title="t",
            kind=models.MaterialKind.file,
            ext="txt",
            source="a.txt",
            parsed_chars=3,
            content_hash="abc",
        )
        db.add(doc)
        db.commit()
        doc_id, uid = doc.id, u.id

        db.delete(u)
        db.commit()
        assert db.get(models.User, uid) is None
        assert db.get(models.KbDocument, doc_id) is None
    finally:
        db.close()
