"""Booking lifecycle: create, confirm payment (manual), cancel, refund, complete.

State machine:
    pending --(owner/admin confirms payment)--> confirmed --(after end time)--> completed
    pending --(hold timer runs out)--> expired
    pending | confirmed --(customer / owner / admin cancels)--> cancelled

Double-booking is prevented by the database (exclusion constraint on active bookings),
not just by an application-level check, so concurrent requests cannot both succeed.
"""

import secrets
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingStatus,
    PaymentStatus,
    Turf,
    TurfStatus,
    User,
    UserRole,
)
from app.observability import (
    BOOKING_CONFLICTS,
    BOOKINGS_CANCELLED,
    BOOKINGS_CREATED,
    HOLDS_EXPIRED,
    PAYMENTS_CONFIRMED,
)
from app.services import slots
from app.services.errors import Conflict, Forbidden, NotFound, RuleViolation

_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid misreads
EXCLUSION_VIOLATION = "23P01"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_reference() -> str:
    return "TS-" + "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(8))


def active_hold_filter(now: datetime):
    """SQL filter for bookings that currently block their slot."""
    return or_(
        Booking.status == BookingStatus.confirmed,
        and_(
            Booking.status == BookingStatus.pending,
            or_(Booking.hold_expires_at.is_(None), Booking.hold_expires_at > now),
        ),
    )


def expire_stale_holds(db: Session, now: datetime, turf_id: int | None = None) -> int:
    """Release unpaid bookings whose hold has run out. Returns the number released."""
    stmt = (
        update(Booking)
        .where(
            Booking.status == BookingStatus.pending,
            Booking.hold_expires_at.is_not(None),
            Booking.hold_expires_at <= now,
        )
        .values(
            status=BookingStatus.expired,
            cancellation_reason="Payment was not confirmed before the hold expired",
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    if turf_id is not None:
        stmt = stmt.where(Booking.turf_id == turf_id)
    released = db.execute(stmt).rowcount or 0
    if released:
        HOLDS_EXPIRED.inc(released)
    return released


def busy_ranges(
    db: Session, turf_id: int, start: datetime, end: datetime, now: datetime
) -> list[tuple[datetime, datetime]]:
    rows = db.execute(
        select(Booking.start_at, Booking.end_at)
        .where(
            Booking.turf_id == turf_id,
            Booking.start_at < end,
            Booking.end_at > start,
            active_hold_filter(now),
        )
        .order_by(Booking.start_at)
    ).all()
    return [(row.start_at, row.end_at) for row in rows]


def availability(db: Session, turf: Turf, day: date, duration_hours: int, now: datetime | None = None):
    settings = get_settings()
    now = now or utcnow()
    window = slots.opening_window(day, turf.opening_time, turf.closing_time, settings.tz)
    busy = busy_ranges(db, turf.id, window.start, window.end, now)
    starts = slots.available_starts(
        window,
        timedelta(hours=duration_hours),
        busy,
        step_minutes=settings.booking_slot_minutes,
        now=now,
    )
    return window, busy, starts


def create_booking(
    db: Session,
    *,
    user: User,
    turf: Turf,
    day: date,
    start_time: time,
    duration_hours: int,
    notes: str = "",
    payment_method: str | None = None,
    now: datetime | None = None,
) -> Booking:
    settings = get_settings()
    now = now or utcnow()

    if not (turf.is_active and turf.is_verified):
        raise NotFound("Turf not found")
    if turf.status != TurfStatus.open:
        raise RuleViolation(f"This turf is currently '{turf.status}' and is not taking bookings")
    if not turf.min_booking_hours <= duration_hours <= turf.max_booking_hours:
        raise RuleViolation(
            f"Bookings at this turf must be {turf.min_booking_hours}-{turf.max_booking_hours} hours long"
        )

    window = slots.opening_window(day, turf.opening_time, turf.closing_time, settings.tz)
    start_at = slots.resolve_start(day, start_time, window, settings.tz)
    end_at = slots.validate_slot(
        start_at,
        timedelta(hours=duration_hours),
        window,
        step_minutes=settings.booking_slot_minutes,
        now=now,
        max_days_ahead=settings.booking_max_days_ahead,
    )

    unpaid = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.user_id == user.id,
            Booking.status == BookingStatus.pending,
            or_(Booking.hold_expires_at.is_(None), Booking.hold_expires_at > now),
        )
    )
    if unpaid >= settings.max_pending_bookings_per_user:
        raise RuleViolation(f"You already have {unpaid} unpaid bookings. Pay for or cancel one before booking again.")

    expire_stale_holds(db, now, turf.id)

    hold = settings.booking_hold_minutes
    booking = Booking(
        reference=new_reference(),
        turf_id=turf.id,
        user_id=user.id,
        start_at=start_at,
        end_at=end_at,
        status=BookingStatus.pending,
        payment_status=PaymentStatus.unpaid,
        payment_method=payment_method,
        total_amount=(turf.price_per_hour * duration_hours).quantize(Decimal("0.01")),
        notes=notes,
        # An unpaid hold never outlives the slot itself.
        hold_expires_at=min(now + timedelta(minutes=hold), start_at) if hold > 0 else None,
    )
    db.add(booking)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
            BOOKING_CONFLICTS.inc()
            raise Conflict("That slot is no longer available. Please choose another time.") from None
        raise
    BOOKINGS_CREATED.inc()
    return booking


# --- Access control ----------------------------------------------------------


def can_manage(user: User, booking: Booking) -> bool:
    return user.role == UserRole.admin or booking.turf.owner_id == user.id


def can_view(user: User, booking: Booking) -> bool:
    return booking.user_id == user.id or can_manage(user, booking)


def get_booking(db: Session, booking_id: int, *, for_update: bool = False) -> Booking:
    stmt = select(Booking).options(joinedload(Booking.turf), joinedload(Booking.user)).where(Booking.id == booking_id)
    if for_update:
        # Row lock so two staff members (or the expiry job) can't change the same booking at once.
        stmt = stmt.with_for_update(of=Booking)
    booking = db.execute(stmt).unique().scalar_one_or_none()
    if booking is None:
        raise NotFound("Booking not found")
    return booking


def require_manager(user: User, booking: Booking) -> None:
    if not can_manage(user, booking):
        raise Forbidden("Only the turf owner or an admin can do this")


# --- Transitions -------------------------------------------------------------


def confirm_payment(
    booking: Booking,
    actor: User,
    *,
    method: str,
    reference: str | None,
    amount: Decimal | None,
    now: datetime | None = None,
) -> Booking:
    require_manager(actor, booking)
    if booking.payment_status == PaymentStatus.paid:
        raise Conflict("Payment for this booking is already confirmed")
    if booking.status != BookingStatus.pending:
        raise Conflict(f"Cannot confirm payment for a booking that is {booking.status}")
    booking.status = BookingStatus.confirmed
    booking.payment_status = PaymentStatus.paid
    booking.payment_method = method
    booking.payment_reference = reference
    booking.paid_amount = amount if amount is not None else booking.total_amount
    booking.paid_at = now or utcnow()
    booking.payment_confirmed_by_id = actor.id
    booking.hold_expires_at = None
    PAYMENTS_CONFIRMED.inc()
    return booking


def cancel(booking: Booking, actor: User, *, reason: str | None, now: datetime | None = None) -> Booking:
    settings = get_settings()
    now = now or utcnow()
    is_manager = can_manage(actor, booking)
    if not (is_manager or booking.user_id == actor.id):
        raise Forbidden("You cannot cancel this booking")
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise Conflict(f"This booking is already {booking.status}")
    if booking.end_at <= now:
        raise RuleViolation("This booking has already ended")
    if not is_manager:
        cutoff = timedelta(minutes=settings.cancellation_cutoff_minutes)
        if booking.start_at - now < cutoff:
            raise RuleViolation(
                f"Bookings can be cancelled up to {settings.cancellation_cutoff_minutes} minutes "
                "before the start time. Please contact the turf owner."
            )
    booking.status = BookingStatus.cancelled
    booking.cancelled_at = now
    booking.cancelled_by_id = actor.id
    booking.cancellation_reason = reason or (
        "Cancelled by the turf" if is_manager and booking.user_id != actor.id else "Cancelled by customer"
    )
    booking.hold_expires_at = None
    if booking.payment_status == PaymentStatus.paid:
        booking.payment_status = PaymentStatus.refund_pending
    BOOKINGS_CANCELLED.labels("staff" if is_manager and booking.user_id != actor.id else "customer").inc()
    return booking


def mark_refunded(booking: Booking, actor: User) -> Booking:
    require_manager(actor, booking)
    if booking.payment_status != PaymentStatus.refund_pending:
        raise Conflict("This booking has no refund pending")
    booking.payment_status = PaymentStatus.refunded
    return booking


def complete(booking: Booking, actor: User, now: datetime | None = None) -> Booking:
    require_manager(actor, booking)
    if booking.status != BookingStatus.confirmed:
        raise Conflict(f"Only confirmed bookings can be completed (this one is {booking.status})")
    if booking.end_at > (now or utcnow()):
        raise RuleViolation("A booking can only be marked completed after it ends")
    booking.status = BookingStatus.completed
    return booking


# --- Queries -----------------------------------------------------------------


def list_bookings(
    db: Session,
    *,
    owner_id: int | None = None,
    user_id: int | None = None,
    turf_id: int | None = None,
    status: str | None = None,
    payment_status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    upcoming: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Booking], int]:
    """Filterable booking list. `owner_id` limits results to that owner's turfs."""
    tz = get_settings().tz
    now = utcnow()
    filters = []
    if owner_id is not None:
        filters.append(Booking.turf_id.in_(select(Turf.id).where(Turf.owner_id == owner_id)))
    if user_id is not None:
        filters.append(Booking.user_id == user_id)
    if turf_id is not None:
        filters.append(Booking.turf_id == turf_id)
    if status is not None:
        filters.append(Booking.status == status)
    if payment_status is not None:
        filters.append(Booking.payment_status == payment_status)
    if date_from is not None:
        filters.append(Booking.start_at >= datetime.combine(date_from, time.min, tzinfo=tz))
    if date_to is not None:
        filters.append(Booking.start_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz))
    if upcoming is True:
        filters += [Booking.end_at > now, Booking.status.in_(ACTIVE_BOOKING_STATUSES)]
        order = [Booking.start_at.asc(), Booking.id]
    elif upcoming is False:
        filters.append(or_(Booking.end_at <= now, Booking.status.not_in(ACTIVE_BOOKING_STATUSES)))
        order = [Booking.start_at.desc(), Booking.id.desc()]
    else:
        order = [Booking.start_at.desc(), Booking.id.desc()]

    total = db.scalar(select(func.count()).select_from(Booking).where(*filters)) or 0
    items = list(
        db.scalars(
            select(Booking)
            .options(joinedload(Booking.turf), joinedload(Booking.user))
            .where(*filters)
            .order_by(*order)
            .offset(offset)
            .limit(limit)
        ).unique()
    )
    return items, total
