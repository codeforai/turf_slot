"""In-app notifications and the emails that accompany booking events."""

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, Notification, NotificationType
from app.services.email import send_email


def add_notification(
    db: Session,
    *,
    user_id: int,
    title: str,
    message: str,
    type: str = NotificationType.info,
    turf_id: int | None = None,
    booking_id: int | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        title=title[:120],
        message=message,
        type=type,
        turf_id=turf_id,
        booking_id=booking_id,
    )
    db.add(notification)
    return notification


def _when(booking: Booking) -> str:
    tz = get_settings().tz
    start = booking.start_at.astimezone(tz)
    end = booking.end_at.astimezone(tz)
    return f"{start:%a %d %b %Y}, {start:%H:%M}-{end:%H:%M}"


def booking_created(db: Session, tasks: BackgroundTasks, booking: Booking) -> None:
    turf, customer = booking.turf, booking.user
    when = _when(booking)
    hold = (
        f" Your slot is held until {booking.hold_expires_at.astimezone(get_settings().tz):%H:%M on %d %b}."
        if booking.hold_expires_at
        else ""
    )
    add_notification(
        db,
        user_id=customer.id,
        title=f"Booking {booking.reference} received",
        message=f"{turf.name}, {when}. Amount due: Rs {booking.total_amount}. "
        f"Pay the turf and they will confirm your booking.{hold}",
        type=NotificationType.booking,
        turf_id=turf.id,
        booking_id=booking.id,
    )
    add_notification(
        db,
        user_id=turf.owner_id,
        title=f"New booking request {booking.reference}",
        message=f"{customer.full_name} requested {turf.name}, {when}. "
        f"Confirm once you receive Rs {booking.total_amount}.",
        type=NotificationType.booking,
        turf_id=turf.id,
        booking_id=booking.id,
    )
    tasks.add_task(
        send_email,
        turf.owner.email,
        f"New booking request {booking.reference} - {turf.name}",
        f"{customer.full_name} ({customer.phone}) requested {turf.name} on {when}.\n"
        f"Amount: Rs {booking.total_amount}\n"
        f"Confirm the payment from your owner dashboard once received.",
    )


def payment_confirmed(db: Session, tasks: BackgroundTasks, booking: Booking) -> None:
    turf, customer = booking.turf, booking.user
    when = _when(booking)
    add_notification(
        db,
        user_id=customer.id,
        title=f"Booking {booking.reference} confirmed",
        message=f"Payment of Rs {booking.paid_amount} received. See you at {turf.name}, {when}.",
        type=NotificationType.booking,
        turf_id=turf.id,
        booking_id=booking.id,
    )
    tasks.add_task(
        send_email,
        customer.email,
        f"Booking confirmed: {turf.name} ({booking.reference})",
        f"Hi {customer.first_name},\n\nYour booking at {turf.name} on {when} is confirmed.\n"
        f"Amount paid: Rs {booking.paid_amount}\nAddress: {turf.address}\n\n"
        f"You can download your receipt from the app.",
    )


def booking_cancelled(db: Session, tasks: BackgroundTasks, booking: Booking, cancelled_by_customer: bool) -> None:
    turf, customer = booking.turf, booking.user
    when = _when(booking)
    refund = " A refund is pending." if booking.payment_status == "refund_pending" else ""
    if cancelled_by_customer:
        add_notification(
            db,
            user_id=turf.owner_id,
            title=f"Booking {booking.reference} cancelled",
            message=f"{customer.full_name} cancelled {turf.name}, {when}.{refund}",
            type=NotificationType.booking,
            turf_id=turf.id,
            booking_id=booking.id,
        )
    else:
        add_notification(
            db,
            user_id=customer.id,
            title=f"Booking {booking.reference} cancelled by the turf",
            message=f"{turf.name}, {when}. Reason: {booking.cancellation_reason}.{refund}",
            type=NotificationType.alert,
            turf_id=turf.id,
            booking_id=booking.id,
        )
        tasks.add_task(
            send_email,
            customer.email,
            f"Booking cancelled: {turf.name} ({booking.reference})",
            f"Hi {customer.first_name},\n\nYour booking at {turf.name} on {when} was cancelled.\n"
            f"Reason: {booking.cancellation_reason}\n{refund.strip()}",
        )


def refund_marked(db: Session, booking: Booking) -> None:
    add_notification(
        db,
        user_id=booking.user_id,
        title=f"Refund processed for {booking.reference}",
        message=f"The turf has marked your refund of Rs {booking.paid_amount} as paid.",
        type=NotificationType.booking,
        turf_id=booking.turf_id,
        booking_id=booking.id,
    )
