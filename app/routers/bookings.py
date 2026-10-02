from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Response, status

from app.config import get_settings
from app.deps import CurrentUser, DbSession, PageParams
from app.models import Booking, BookingStatus, PaymentStatus
from app.schemas import Page
from app.schemas.bookings import BookingCreate, BookingOut, BookingScope, CancelRequest, PaymentConfirm
from app.services import bookings as booking_service
from app.services import notify
from app.services import turfs as turf_service
from app.services.errors import Conflict, NotFound
from app.services.receipts import build_receipt_pdf

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate, user: CurrentUser, db: DbSession, tasks: BackgroundTasks) -> Booking:
    """Hold a slot. The booking stays `pending` until the turf owner confirms payment,
    and is released automatically if payment isn't confirmed within the hold period."""
    turf = turf_service.get_turf(db, payload.turf_id)
    booking = booking_service.create_booking(
        db,
        user=user,
        turf=turf,
        day=payload.date,
        start_time=payload.start_time,
        duration_hours=payload.duration_hours,
        notes=payload.notes,
        payment_method=payload.payment_method,
    )
    notify.booking_created(db, tasks, booking)
    db.commit()
    return booking


@router.get("/me", response_model=Page[BookingOut])
def my_bookings(
    user: CurrentUser,
    db: DbSession,
    pagination: PageParams,
    scope: BookingScope = "upcoming",
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
) -> Page[BookingOut]:
    items, total = booking_service.list_bookings(
        db,
        user_id=user.id,
        status=status_filter,
        upcoming={"upcoming": True, "past": False, "all": None}[scope],
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return Page(
        items=[BookingOut.model_validate(b) for b in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: int, user: CurrentUser, db: DbSession) -> Booking:
    booking = booking_service.get_booking(db, booking_id)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")  # don't reveal that other people's bookings exist
    return booking


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(
    booking_id: int,
    user: CurrentUser,
    db: DbSession,
    tasks: BackgroundTasks,
    payload: CancelRequest | None = None,
) -> Booking:
    """Customers can cancel until the cutoff before start. Owners/admins can cancel any active booking.
    Cancelling a paid booking marks the payment `refund_pending`."""
    booking = booking_service.get_booking(db, booking_id, for_update=True)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")
    booking_service.cancel(booking, user, reason=payload.reason if payload else None)
    by_customer = user.id == booking.user_id
    notify.booking_cancelled(db, tasks, booking, cancelled_by_customer=by_customer)
    db.commit()
    return booking


@router.post("/{booking_id}/confirm-payment", response_model=BookingOut)
def confirm_payment(
    booking_id: int, payload: PaymentConfirm, user: CurrentUser, db: DbSession, tasks: BackgroundTasks
) -> Booking:
    """Turf owner or admin confirms they received the payment (cash, UPI, bank transfer, card).
    This moves the booking from `pending` to `confirmed`."""
    booking = booking_service.get_booking(db, booking_id, for_update=True)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")
    booking_service.confirm_payment(
        booking, user, method=payload.method, reference=payload.reference, amount=payload.amount
    )
    notify.payment_confirmed(db, tasks, booking)
    db.commit()
    return booking


@router.post("/{booking_id}/mark-refunded", response_model=BookingOut)
def mark_refunded(booking_id: int, user: CurrentUser, db: DbSession) -> Booking:
    booking = booking_service.get_booking(db, booking_id, for_update=True)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")
    booking_service.mark_refunded(booking, user)
    notify.refund_marked(db, booking)
    db.commit()
    return booking


@router.post("/{booking_id}/complete", response_model=BookingOut)
def complete_booking(booking_id: int, user: CurrentUser, db: DbSession) -> Booking:
    booking = booking_service.get_booking(db, booking_id, for_update=True)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")
    booking_service.complete(booking, user)
    db.commit()
    return booking


@router.get(
    "/{booking_id}/receipt",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "PDF receipt"}},
)
def download_receipt(booking_id: int, user: CurrentUser, db: DbSession) -> Response:
    booking = booking_service.get_booking(db, booking_id)
    if not booking_service.can_view(user, booking):
        raise NotFound("Booking not found")
    if booking.payment_status == PaymentStatus.unpaid:
        raise Conflict("A receipt is available once the payment has been confirmed")
    pdf = build_receipt_pdf(booking, booking.turf, booking.user, get_settings().tz)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="receipt-{booking.reference}.pdf"'},
    )
