"""Test setup: a real PostgreSQL database (constraints like the booking exclusion
constraint can't be faked with SQLite), migrated with Alembic exactly as in production.

Set TEST_DATABASE_URL to a disposable database whose name contains "test".
"""

import itertools
import os
import tempfile
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/turfslot_test")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
        "DATABASE_URL": TEST_DB,
        "EMAIL_BACKEND": "memory",
        "PASSWORD_HASH_N": "1024",  # fast hashing in tests only
        "STORAGE_BACKEND": "local",
        "MEDIA_DIR": tempfile.mkdtemp(prefix="turfslot-media-"),
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "BOOKING_HOLD_MINUTES": "120",
        "MAX_PENDING_BOOKINGS_PER_USER": "3",
    }
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Amenity, Booking, PaymentStatus, Turf, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import email  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Kolkata")
PASSWORD = "Passw0rd!"
TABLES = "notifications, favourites, reviews, bookings, turf_images, turf_amenities, turfs, amenities, users"
_seq = itertools.count(1)


def future_day(days: int = 3) -> date:
    return (datetime.now(TZ) + timedelta(days=days)).date()


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, dtime(hour, minute), tzinfo=TZ)


@pytest.fixture(scope="session", autouse=True)
def database():
    db_name = make_url(TEST_DB).database or ""
    if "test" not in db_name:
        pytest.exit(f"Refusing to run tests against database '{db_name}' (name must contain 'test')")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")  # the downgrade path must work too
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_db(database):
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    email.outbox.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def login(client: TestClient, email_address: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/auth/login", data={"username": email_address, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class Factory:
    def __init__(self, db, client):
        self.db = db
        self.client = client

    def user(self, role: str = "user", **overrides) -> tuple[User, dict[str, str]]:
        n = next(_seq)
        user = User(
            first_name=overrides.pop("first_name", f"Test{n}"),
            last_name="User",
            email=overrides.pop("email", f"{role}{n}@example.com"),
            phone=overrides.pop("phone", f"9{n:09d}"),
            password_hash=hash_password(PASSWORD),
            role=role,
            **overrides,
        )
        self.db.add(user)
        self.db.commit()
        return user, login(self.client, user.email)

    def amenity(self, name: str) -> Amenity:
        amenity = Amenity(name=name)
        self.db.add(amenity)
        self.db.commit()
        return amenity

    def turf(self, owner: User, **overrides) -> Turf:
        n = next(_seq)
        values = {
            "name": f"Arena {n}",
            "slug": f"arena-{n}",
            "sport_type": "football",
            "description": "",
            "location": "Kakkanad, Kochi",
            "address": "Infopark Road",
            "length_m": 40,
            "width_m": 20,
            "surface_type": "artificial",
            "capacity": 14,
            "price_per_hour": Decimal("1000.00"),
            "min_booking_hours": 1,
            "max_booking_hours": 4,
            "opening_time": dtime(6),
            "closing_time": dtime(23),
            "is_verified": True,
        }
        amenities = overrides.pop("amenities", [])
        values.update(overrides)
        turf = Turf(owner_id=owner.id, **values)
        turf.amenities = amenities
        self.db.add(turf)
        self.db.commit()
        return turf

    def booking(self, user: User, turf: Turf, start_at: datetime, hours: int = 1, **overrides) -> Booking:
        """Insert a booking directly (bypassing booking rules) for time-sensitive scenarios."""
        paid = overrides.pop("paid", False)
        values = {
            "reference": f"TS-T{next(_seq):07d}",
            "status": "confirmed" if paid else "pending",
            "payment_status": PaymentStatus.paid if paid else PaymentStatus.unpaid,
            "total_amount": turf.price_per_hour * hours,
            "paid_amount": turf.price_per_hour * hours if paid else None,
        }
        values.update(overrides)
        booking = Booking(
            turf_id=turf.id,
            user_id=user.id,
            start_at=start_at,
            end_at=start_at + timedelta(hours=hours),
            **values,
        )
        self.db.add(booking)
        self.db.commit()
        return booking


@pytest.fixture
def factory(db, client) -> Factory:
    return Factory(db, client)
