"""SQLAlchemy ORM models.

The schema itself (including CHECK constraints and the booking no-overlap exclusion
constraint) is defined in the Alembic migrations under migrations/versions/.
"""

from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (  # noqa: F401  (re-exported for convenience)
    ACTIVE_BOOKING_STATUSES,
    BookingStatus,
    NotificationType,
    PaymentMethod,
    PaymentStatus,
    SportType,
    SurfaceType,
    TurfStatus,
    UserRole,
)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


turf_amenities = Table(
    "turf_amenities",
    Base.metadata,
    Column("turf_id", BigInteger, ForeignKey("turfs.id", ondelete="CASCADE"), primary_key=True),
    Column("amenity_id", BigInteger, ForeignKey("amenities.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str] = mapped_column(String(15), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(500))
    profile_image_key: Mapped[str | None] = mapped_column(String(300))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    reset_code_hash: Mapped[str | None] = mapped_column(String(128))
    reset_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_attempts: Mapped[int] = mapped_column(Integer, default=0)
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    turfs: Mapped[list["Turf"]] = relationship(back_populates="owner")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Amenity(Base):
    __tablename__ = "amenities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Turf(Base):
    __tablename__ = "turfs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    sport_type: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(255))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    length_m: Mapped[int] = mapped_column(Integer)
    width_m: Mapped[int] = mapped_column(Integer)
    surface_type: Mapped[str] = mapped_column(String(20))
    capacity: Mapped[int] = mapped_column(Integer)
    price_per_hour: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_booking_hours: Mapped[int] = mapped_column(SmallInteger, default=1)
    max_booking_hours: Mapped[int] = mapped_column(SmallInteger, default=4)
    opening_time: Mapped[time] = mapped_column(Time)
    closing_time: Mapped[time] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(20), default=TurfStatus.open)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(500))
    cover_image_key: Mapped[str | None] = mapped_column(String(300))
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0"))
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    owner: Mapped[User] = relationship(back_populates="turfs")
    amenities: Mapped[list[Amenity]] = relationship(secondary=turf_amenities, order_by=Amenity.name)
    images: Mapped[list["TurfImage"]] = relationship(
        back_populates="turf", cascade="all, delete-orphan", order_by="TurfImage.id"
    )


class TurfImage(Base):
    __tablename__ = "turf_images"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    turf_id: Mapped[int] = mapped_column(ForeignKey("turfs.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_at()

    turf: Mapped[Turf] = relationship(back_populates="images")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    turf_id: Mapped[int] = mapped_column(ForeignKey("turfs.id", ondelete="RESTRICT"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.pending)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    notes: Mapped[str] = mapped_column(Text, default="")
    payment_status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.unpaid)
    payment_method: Mapped[str | None] = mapped_column(String(20))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    turf: Mapped[Turf] = relationship()
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    turf_id: Mapped[int] = mapped_column(ForeignKey("turfs.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    user: Mapped[User] = relationship()


class Favourite(Base):
    __tablename__ = "favourites"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    turf_id: Mapped[int] = mapped_column(ForeignKey("turfs.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = _created_at()

    turf: Mapped[Turf] = relationship()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20), default=NotificationType.info)
    turf_id: Mapped[int | None] = mapped_column(ForeignKey("turfs.id", ondelete="SET NULL"))
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created_at()
