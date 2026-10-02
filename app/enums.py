"""Enumerations shared by the ORM models and the API schemas."""

from enum import StrEnum


class UserRole(StrEnum):
    user = "user"
    owner = "owner"
    admin = "admin"


class SportType(StrEnum):
    football = "football"
    cricket = "cricket"
    tennis = "tennis"
    badminton = "badminton"
    multi = "multi"


class SurfaceType(StrEnum):
    natural = "natural"
    artificial = "artificial"
    clay = "clay"
    hard = "hard"
    synthetic = "synthetic"


class TurfStatus(StrEnum):
    open = "open"
    closed = "closed"
    maintenance = "maintenance"


class BookingStatus(StrEnum):
    pending = "pending"  # slot held, waiting for payment confirmation
    confirmed = "confirmed"  # payment confirmed by owner/admin
    cancelled = "cancelled"
    completed = "completed"
    expired = "expired"  # unpaid hold released automatically


ACTIVE_BOOKING_STATUSES = (BookingStatus.pending, BookingStatus.confirmed)


class PaymentStatus(StrEnum):
    unpaid = "unpaid"
    paid = "paid"
    refund_pending = "refund_pending"
    refunded = "refunded"


class PaymentMethod(StrEnum):
    cash = "cash"
    upi = "upi"
    bank_transfer = "bank_transfer"
    card = "card"


class NotificationType(StrEnum):
    info = "info"
    offer = "offer"
    alert = "alert"
    booking = "booking"
