"""iter 370 B18：插件门面 MemoryNamespace.create_thread 与 REST /threads 硬化对齐的回归。

REST 侧（app/api/memory.py，iter 365/369）已硬化：kind 白名单 / title≤256 / model≤128 /
500 条配额 / 30 次每分钟频控 / session_id 归属校验。门面此前无任何校验——插件可绕过
全部约束写入越权或超长数据（String 列不限长，超长 title 随列表全量回传）。
"""

import pytest

from app.db import models
from app.db.base import SessionLocal
from app.plugins.api import MemoryNamespace


def _mk_user(name: str) -> int:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def test_create_thread_rejects_bad_kind_title_model():
    ns = MemoryNamespace()
    uid = _mk_user("facade_bad")
    with pytest.raises(ValueError, match="kind"):
        ns.create_thread(uid, kind="evil")
    with pytest.raises(ValueError, match="256"):
        ns.create_thread(uid, title="长" * 257)
    with pytest.raises(ValueError, match="128"):
        ns.create_thread(uid, model="m" * 129)
    with pytest.raises(ValueError, match="正整数"):
        ns.create_thread(0)


def test_create_thread_session_ownership():
    ns = MemoryNamespace()
    uid = _mk_user("facade_owner")
    other = _mk_user("facade_other")
    db = SessionLocal()
    try:
        s = models.Session(user_id=other, title="他人的会话")
        db.add(s)
        db.commit()
        db.refresh(s)
        sid = s.id
    finally:
        db.close()
    with pytest.raises(ValueError, match="不属于"):
        ns.create_thread(uid, session_id=sid)


def test_create_thread_ok_and_rate_limit():
    ns = MemoryNamespace()
    uid = _mk_user("facade_ok")
    out = ns.create_thread(uid, title="插件建的", kind="chat", model="claude-sonnet-5")
    assert out["title"] == "插件建的" and out["kind"] == "chat"
    # 30/分钟频控：上面已用 1 次，再造 29 次应全部成功、第 31 次触发
    for _ in range(29):
        ns.create_thread(uid)
    with pytest.raises(RuntimeError, match="rate_limited"):
        ns.create_thread(uid)


def test_save_memory_limits_and_thread_ownership():
    ns = MemoryNamespace()
    uid = _mk_user("facade_mem")
    other = _mk_user("facade_mem_other")
    th = ns.create_thread(other)  # 他人会话
    with pytest.raises(ValueError, match="字符"):
        ns.save_memory(uid, "")
    with pytest.raises(ValueError, match="字符"):
        ns.save_memory(uid, "x" * 20001)
    with pytest.raises(ValueError, match="32"):
        ns.save_memory(uid, "内容", kind="k" * 33)
    # 跨用户注入：把记忆挂到他人 thread —— 必须拒绝
    with pytest.raises(ValueError, match="不属于"):
        ns.save_memory(uid, "注入", scope="thread", thread_id=th["id"])
    out = ns.save_memory(uid, "用户偏好简洁", kind="fact", scope="user")
    assert out["scope"] == "user"


def test_save_memory_quota_and_rate():
    ns = MemoryNamespace()
    uid = _mk_user("facade_mem2")
    db = SessionLocal()
    try:
        for _ in range(2000):
            db.add(models.Memory(user_id=uid, content="占位", kind="note", scope="user"))
        db.commit()
    finally:
        db.close()
    with pytest.raises(RuntimeError, match="配额"):
        ns.save_memory(uid, "超额")


def test_add_message_limits():
    ns = MemoryNamespace()
    uid = _mk_user("facade_msg")
    t = ns.create_thread(uid)
    out = ns.add_message(t["id"], uid, "assistant", "自动回复")
    assert out["role"] == "assistant"
    with pytest.raises(ValueError, match="字符"):
        ns.add_message(t["id"], uid, "user", "x" * 200_001)
    with pytest.raises(ValueError, match="block"):
        ns.add_message(t["id"], uid, "user", [{"type": "text", "text": "a"}] * 65)
    with pytest.raises(ValueError, match="block"):
        ns.add_message(t["id"], uid, "user", ["不是dict"] * 3)
    with pytest.raises(ValueError, match="content"):
        ns.add_message(t["id"], uid, "user", 12345)
    # 频控：60/分钟，令牌在参数校验前扣减——上面 5 次调用（1 成功 + 4 校验失败）已消耗 5 个
    for _ in range(55):
        ns.add_message(t["id"], uid, "user", "ok")
    with pytest.raises(RuntimeError, match="rate_limited"):
        ns.add_message(t["id"], uid, "user", "超频")


def test_create_thread_quota_500():
    ns = MemoryNamespace()
    uid = _mk_user("facade_quota")
    db = SessionLocal()
    try:
        for _ in range(500):
            db.add(models.Thread(user_id=uid, title="占位"))
        db.commit()
    finally:
        db.close()
    with pytest.raises(RuntimeError, match="配额"):
        ns.create_thread(uid)
