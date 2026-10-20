"""JWT 与短信验证码。

短信当前为开发桩：SMS_DEV_MODE=true 时直接返回固定验证码，
不调用任何短信网关。生产环境 TODO: 接入腾讯云/阿里云短信，
把 _send_via_provider 换成真实调用。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.core.config import settings

# 开发桩：内存存储验证码（生产换 Redis）
_code_store: dict[str, tuple[str, datetime]] = {}
_last_sent: dict[str, datetime] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def send_sms_code(phone: str) -> dict:
    """发送验证码。返回 {"dev_code": ...} 仅在开发模式，用于联调。"""
    now = _now()
    last = _last_sent.get(phone)
    if last and (now - last).total_seconds() < settings.SMS_RESEND_INTERVAL_SECONDS:
        raise ValueError("发送过于频繁，请稍后再试")

    if settings.SMS_DEV_MODE:
        code = settings.DEV_SMS_CODE
    else:
        code = _send_via_provider(phone)  # 真实网关

    _code_store[phone] = (code, now + timedelta(seconds=settings.SMS_CODE_TTL_SECONDS))
    _last_sent[phone] = now
    result: dict = {"ok": True}
    if settings.SMS_DEV_MODE:
        result["dev_code"] = code  # 开发模式直接返回，方便联调
    return result


def verify_sms_code(phone: str, code: str) -> bool:
    item = _code_store.get(phone)
    if not item:
        return False
    expected, expire_at = item
    if _now() > expire_at:
        _code_store.pop(phone, None)
        return False
    if expected != code.strip():
        return False
    _code_store.pop(phone, None)  # 一次性使用
    return True


def _send_via_provider(phone: str) -> str:
    # TODO: 接入真实短信网关（腾讯云/阿里云），返回下发的验证码
    raise NotImplementedError("请配置真实短信网关")


def create_access_token(user_id: int) -> str:
    expire = _now() + timedelta(days=settings.JWT_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
