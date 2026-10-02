"""Password hashing, JWT access tokens and one-time codes.

Passwords are hashed with scrypt from the standard library (memory-hard, no native
dependencies). The stored format records its parameters, so the cost can be raised
later and old hashes upgraded on the next successful login.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings

_SCRYPT_R = 8
_SCRYPT_P = 1
_JWT_ALGORITHM = "HS256"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * n * r)


def hash_password(password: str) -> str:
    n = get_settings().password_hash_n
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, n, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = _scrypt(password, base64.b64decode(salt_b64), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_needs_rehash(stored: str) -> bool:
    try:
        return int(stored.split("$")[1]) != get_settings().password_hash_n
    except (IndexError, ValueError):
        return True


# Used to keep login timing similar whether or not the email exists.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-timing")
    verify_password(password, _DUMMY_HASH)


def create_access_token(user_id: int, role: str, token_version: int) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "ver": token_version,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM), expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError if the token is invalid or expired."""
    payload = jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_otp(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code), stored_hash)
