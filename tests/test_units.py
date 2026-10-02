"""Pure unit tests (no database): slot maths, password hashing, tokens."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import jwt
import pytest

from app.security import (
    create_access_token,
    decode_access_token,
    hash_otp,
    hash_password,
    password_needs_rehash,
    verify_otp,
    verify_password,
)
from app.services import slots
from app.services.errors import RuleViolation

TZ = ZoneInfo("Asia/Kolkata")
DAY = date(2030, 1, 15)
NOW = datetime(2030, 1, 15, 9, 0, tzinfo=TZ)


def window(opening=time(6), closing=time(23)):
    return slots.opening_window(DAY, opening, closing, TZ)


def test_overnight_window_ends_next_day():
    w = window(time(6), time(1))
    assert w.end == datetime(2030, 1, 16, 1, 0, tzinfo=TZ)
    assert slots.resolve_start(DAY, time(0, 30), w, TZ) == datetime(2030, 1, 16, 0, 30, tzinfo=TZ)


def test_available_starts_skip_busy_and_past_times():
    busy = [(datetime(2030, 1, 15, 18, 0, tzinfo=TZ), datetime(2030, 1, 15, 19, 0, tzinfo=TZ))]
    starts = [
        s.strftime("%H:%M")
        for s in slots.available_starts(window(), timedelta(hours=1), busy, step_minutes=30, now=NOW)
    ]
    assert starts[0] == "09:30"  # 09:00 is not in the future
    assert "17:30" not in starts and "18:00" not in starts and "18:30" not in starts
    assert "17:00" in starts and "19:00" in starts  # back-to-back slots are fine
    assert starts[-1] == "22:00"


@pytest.mark.parametrize(
    ("start", "hours", "message"),
    [
        (time(18, 15), 1, "boundary"),
        (time(22, 30), 1, "opening hours"),
        (time(5, 0), 1, "opening hours"),
        (time(8, 0), 1, "future"),
    ],
)
def test_validate_slot_rejects_bad_requests(start, hours, message):
    w = window()
    with pytest.raises(RuleViolation, match=message):
        slots.validate_slot(
            slots.resolve_start(DAY, start, w, TZ),
            timedelta(hours=hours),
            w,
            step_minutes=30,
            now=NOW,
            max_days_ahead=60,
        )


def test_validate_slot_rejects_far_future():
    w = slots.opening_window(DAY + timedelta(days=90), time(6), time(23), TZ)
    with pytest.raises(RuleViolation, match="advance"):
        slots.validate_slot(
            w.start + timedelta(hours=2), timedelta(hours=1), w, step_minutes=30, now=NOW, max_days_ahead=60
        )


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", "garbage")
    assert not password_needs_rehash(stored)
    assert password_needs_rehash(stored.replace("scrypt$1024$", "scrypt$2048$"))


def test_access_token_roundtrip_and_tamper():
    token, expires_in = create_access_token(7, "owner", 3)
    payload = decode_access_token(token)
    assert payload["sub"] == "7" and payload["role"] == "owner" and payload["ver"] == 3
    assert expires_in > 0
    forged = jwt.encode({**payload, "role": "admin"}, "not-the-real-secret-key-0123456789", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)


def test_expired_token_rejected():
    from app.config import get_settings

    expired = jwt.encode(
        {"sub": "1", "type": "access", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        get_settings().secret_key,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_otp_hashing():
    assert verify_otp("123456", hash_otp("123456"))
    assert not verify_otp("123457", hash_otp("123456"))
