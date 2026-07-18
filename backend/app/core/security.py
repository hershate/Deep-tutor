"""密码哈希与 JWT（P4 多用户，iter 57）。

- 密码：pbkdf2_hmac(sha256, 600k 轮，err.md C16 对齐 OWASP 2023 建议)，
  存储格式 `pbkdf2$iterations$salt_hex$hash_hex`（轮数内嵌，旧 hash 校验不受影响）；
  校验用 hmac.compare_digest 常量时间比较（防时序攻击）。
- JWT：HS256，payload {sub: user_id, username, iat, exp}。
  secret 取 settings.jwt_secret；空 -> data_dir/.jwt_secret 持久化随机串
  （err.md C9：不再回退 AUTH_TOKEN——否则持 token 者可伪造任意用户/管理员 JWT）。
  持久化失败 -> 进程级随机（重启后 token 失效，main lifespan 告警）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

import jwt

from app.core.settings import settings

_ITERATIONS = 600_000  # err.md C16：OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议下限
_fallback_secret: str = ""
_secret_lock = threading.Lock()  # 首次生成/读取持久化密钥的竞态防护


def hash_password(password: str) -> str:
    """密码 -> pbkdf2 存储串。"""
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码（格式错误/空串 -> False，不抛异常）。"""
    if not stored:
        return False
    try:
        scheme, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        h = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(h.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


_dummy_hash: str = ""


def dummy_verify(password: str) -> None:
    """对伪 hash 执行一次完整 pbkdf2 校验（结果丢弃）。

    err.md A8：登录时用户不存在也走同等耗时路径，消除时序侧信道（用户名枚举）。
    """
    global _dummy_hash
    if not _dummy_hash:
        _dummy_hash = hash_password("dummy-password-for-timing-flattening")
    verify_password(password, _dummy_hash)


def _secret() -> str:
    """JWT HMAC secret：jwt_secret -> 持久化随机串（.jwt_secret）-> 进程级随机（兜底）。

    err.md C9：不再回退 AUTH_TOKEN——AUTH_TOKEN 是客户端持有的认证凭证，
    兼作 JWT 签名密钥意味着任何持 token 者可离线伪造任意用户（含管理员）的 JWT。
    """
    global _fallback_secret
    if settings.jwt_secret:
        return settings.jwt_secret
    if _fallback_secret:
        return _fallback_secret
    with _secret_lock:
        if _fallback_secret:  # double-checked：等锁期间另一线程已生成
            return _fallback_secret
        persisted = _load_or_create_persisted_secret()
        _fallback_secret = persisted or secrets.token_hex(32)
        return _fallback_secret


def _load_or_create_persisted_secret() -> str:
    """从 data_dir/.jwt_secret 读取；不存在则生成并落盘（0600）。失败 -> 空串。"""
    from pathlib import Path

    try:
        path = Path(settings.data_dir) / ".jwt_secret"
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
        path.parent.mkdir(parents=True, exist_ok=True)
        val = secrets.token_hex(32)
        path.write_text(val, encoding="utf-8")
        try:
            path.chmod(0o600)  # POSIX 生效；Windows 忽略
        except OSError:
            pass
        return val
    except OSError:
        return ""


def create_token(user_id: int, username: str) -> str:
    """签发 JWT（有效期 settings.jwt_expire_days 天）。"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + settings.jwt_expire_days * 86400,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """解析 JWT；签名错误/过期/格式错误 -> None（不抛异常）。"""
    if not token:
        return None
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def ensure_admin_user(db) -> None:
    """启动收敛（P4 iter 57）：确保管理员账号存在。

    - 恰一个无 username 的旧默认用户（单用户时代数据）-> 升级为管理员
      （username=settings.admin_username；既有 sessions 等数据随 user_id 归管理员）。
    - 无该 username 用户且设置了 admin_password -> 创建管理员。
    - admin_password 为空 -> 不设密码（JWT 登录不可用，AUTH_TOKEN 回退仍可用），由调用方告警。
    幂等；不抛异常（失败仅回滚，启动不中断）。
    """
    from app.db.models import User

    try:
        admin = db.query(User).filter_by(username=settings.admin_username).first()
        if admin is not None:
            changed = False
            if not admin.is_admin:
                admin.is_admin = True
                changed = True
            # err.md C8：ADMIN_PASSWORD 轮换/后补时应收敛到现有管理员
            # （此前命中即 return，改密码永不生效；空密码账号后补密码也无法登录）
            if settings.admin_password and not verify_password(
                settings.admin_password, admin.password_hash
            ):
                admin.password_hash = hash_password(settings.admin_password)
                admin.tokens_valid_after = time.time()  # 改密吊销旧 JWT（err.md A9）
                changed = True
            if changed:
                db.commit()
            return
        users = db.query(User).all()
        if len(users) == 1 and not users[0].username:
            u = users[0]
            u.username = settings.admin_username
            u.is_admin = True
            if settings.admin_password:
                u.password_hash = hash_password(settings.admin_password)
            db.commit()
            return
        if settings.admin_password:
            db.add(
                User(
                    name=settings.admin_username,
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    is_admin=True,
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
