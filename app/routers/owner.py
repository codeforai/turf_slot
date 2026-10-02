"""Turf owner endpoints: manage turfs, see bookings and payments, message customers.

Admins can use these too (acting as an owner of the turfs they created); platform-wide
moderation lives under /admin.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from sqlalchemy import distinct, func, insert, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import get_settings
from app.deps import DbSession, OwnerUser, PageParams
from app.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingStatus,
    Notification,
    PaymentStatus,
    Turf,
    TurfImage,
    User,
    UserRole,
)
from app.schemas import Page
from app.schemas.bookings import BookingOut, PaymentsSummary
from app.schemas.misc import BroadcastCreate, BroadcastResult, OwnerDashboard
from app.schemas.turfs import TurfCreate, TurfDetail, TurfImageOut, TurfUpdate
from app.services import bookings as booking_service
from app.services import storage
from app.services import turfs as turf_service
from app.services.errors import Conflict, RuleViolation

router = APIRouter(prefix="/owner", tags=["owner"])

MAX_IMAGES_PER_TURF = 10


def _owned_turf_ids(user: User):
    return select(Turf.id).where(Turf.owner_id == user.id)


def _load_detail(db: Session, turf_id: int) -> Turf:
    return db.scalar(
        select(Turf)
        .options(selectinload(Turf.amenities), selectinload(Turf.images))
        .where(Turf.id == turf_id)
        .execution_options(populate_existing=True)
    )


@router.get("/dashboard", response_model=OwnerDashboard)
def dashboard(user: OwnerUser, db: DbSession) -> OwnerDashboard:
    tz = get_settings().tz
    now = booking_service.utcnow()
    today = datetime.combine(now.astimezone(tz).date(), time.min, tzinfo=tz)
    month_start = today.replace(day=1)
    owned = _owned_turf_ids(user)
    in_owned = Booking.turf_id.in_(owned)

    def count(*conditions) -> int:
        return db.scalar(select(func.count()).select_from(Booking).where(in_owned, *conditions)) or 0

    def revenue(*conditions) -> Decimal:
        value = db.scalar(
            select(func.coalesce(func.sum(Booking.paid_amount), 0)).where(
                in_owned, Booking.payment_status == PaymentStatus.paid, *conditions
            )
        )
        return Decimal(value)

    return OwnerDashboard(
        turfs=db.scalar(select(func.count()).select_from(Turf).where(Turf.owner_id == user.id)) or 0,
        active_turfs=db.scalar(
            select(func.count()).select_from(Turf).where(Turf.owner_id == user.id, Turf.is_active.is_(True))
        )
        or 0,
        bookings_today=count(
            Booking.start_at >= today,
            Booking.start_at < today + timedelta(days=1),
            Booking.status.in_([*ACTIVE_BOOKING_STATUSES, BookingStatus.completed]),
        ),
        pending_payments=count(booking_service.active_hold_filter(now), Booking.status == BookingStatus.pending),
        upcoming_confirmed=count(Booking.status == BookingStatus.confirmed, Booking.start_at > now),
        refunds_pending=count(Booking.payment_status == PaymentStatus.refund_pending),
        revenue_this_month=revenue(Booking.paid_at >= month_start),
        revenue_total=revenue(),
    )


# --- Turfs -------------------------------------------------------------------


@router.get("/turfs", response_model=list[TurfDetail])
def list_my_turfs(user: OwnerUser, db: DbSession) -> list[Turf]:
    return list(
        db.scalars(
            select(Turf)
            .options(selectinload(Turf.amenities), selectinload(Turf.images))
            .where(Turf.owner_id == user.id)
            .order_by(Turf.created_at.desc())
        )
    )


@router.post("/turfs", response_model=TurfDetail, status_code=status.HTTP_201_CREATED)
def create_turf(payload: TurfCreate, user: OwnerUser, db: DbSession) -> Turf:
    """New turfs are hidden from search until an admin verifies them."""
    data = payload.model_dump(exclude={"amenity_ids"})
    turf = Turf(
        **data,
        owner_id=user.id,
        slug=turf_service.unique_slug(db, payload.name, payload.location),
        is_verified=user.role == UserRole.admin,
    )
    turf.amenities = turf_service.load_amenities(db, payload.amenity_ids)
    db.add(turf)
    db.commit()
    return _load_detail(db, turf.id)


@router.get("/turfs/{turf_id}", response_model=TurfDetail)
def get_my_turf(turf_id: int, user: OwnerUser, db: DbSession) -> Turf:
    turf_service.get_managed_turf(db, turf_id, user)
    return _load_detail(db, turf_id)


@router.patch("/turfs/{turf_id}", response_model=TurfDetail)
def update_turf(turf_id: int, payload: TurfUpdate, user: OwnerUser, db: DbSession) -> Turf:
    turf = turf_service.get_managed_turf(db, turf_id, user)
    changes = payload.model_dump(exclude_unset=True)
    amenity_ids = changes.pop("amenity_ids", None)
    for field, value in changes.items():
        if value is None and field not in ("latitude", "longitude"):
            continue  # null means "no change" for required fields
        setattr(turf, field, value)
    if turf.max_booking_hours < turf.min_booking_hours:
        raise RuleViolation("max_booking_hours must be >= min_booking_hours")
    if amenity_ids is not None:
        turf.amenities = turf_service.load_amenities(db, amenity_ids)
    db.commit()
    return _load_detail(db, turf.id)


@router.delete("/turfs/{turf_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_turf(turf_id: int, user: OwnerUser, db: DbSession) -> None:
    """Soft delete: the turf disappears from search but its booking history is kept.
    Refused while it still has upcoming active bookings."""
    turf = turf_service.get_managed_turf(db, turf_id, user)
    upcoming = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.turf_id == turf.id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            Booking.end_at > booking_service.utcnow(),
        )
    )
    if upcoming:
        raise Conflict(f"This turf has {upcoming} upcoming booking(s). Cancel them first.")
    turf.is_active = False
    db.commit()


@router.put("/turfs/{turf_id}/cover-image", response_model=TurfDetail)
def set_cover_image(turf_id: int, file: UploadFile, user: OwnerUser, db: DbSession) -> Turf:
    turf = turf_service.get_managed_turf(db, turf_id, user)
    data, ext = storage.read_image(file)
    stored = storage.save_image(data, ext, f"turfs/{turf.id}")
    old_key = turf.cover_image_key
    turf.cover_image_url, turf.cover_image_key = stored.url, stored.key
    db.commit()
    storage.delete_image(old_key)
    return _load_detail(db, turf.id)


@router.post("/turfs/{turf_id}/images", response_model=TurfImageOut, status_code=status.HTTP_201_CREATED)
def add_gallery_image(turf_id: int, file: UploadFile, user: OwnerUser, db: DbSession) -> TurfImage:
    turf = turf_service.get_managed_turf(db, turf_id, user)
    existing = db.scalar(select(func.count()).select_from(TurfImage).where(TurfImage.turf_id == turf.id))
    if existing >= MAX_IMAGES_PER_TURF:
        raise RuleViolation(f"A turf can have at most {MAX_IMAGES_PER_TURF} gallery images")
    data, ext = storage.read_image(file)
    stored = storage.save_image(data, ext, f"turfs/{turf.id}")
    image = TurfImage(turf_id=turf.id, url=stored.url, storage_key=stored.key)
    db.add(image)
    db.commit()
    return image


@router.delete("/turfs/{turf_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_gallery_image(turf_id: int, image_id: int, user: OwnerUser, db: DbSession) -> None:
    turf = turf_service.get_managed_turf(db, turf_id, user)
    image = db.get(TurfImage, image_id)
    if image is None or image.turf_id != turf.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Image not found")
    key = image.storage_key
    db.delete(image)
    db.commit()
    storage.delete_image(key)


# --- Bookings & payments -----------------------------------------------------


@router.get("/bookings", response_model=Page[BookingOut])
def list_turf_bookings(
    user: OwnerUser,
    db: DbSession,
    pagination: PageParams,
    turf_id: int | None = None,
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
    payment_status: PaymentStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[BookingOut]:
    """Bookings across your turfs. Use `status=pending` to see payments waiting for confirmation."""
    items, total = booking_service.list_bookings(
        db,
        owner_id=user.id,
        turf_id=turf_id,
        status=status_filter,
        payment_status=payment_status,
        date_from=date_from,
        date_to=date_to,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return Page(
        items=[BookingOut.model_validate(b) for b in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/payments", response_model=PaymentsSummary)
def payments(
    user: OwnerUser,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaymentsSummary:
    """Payments collected on your turfs (by booking date), with totals."""
    tz = get_settings().tz
    filters = [
        Booking.turf_id.in_(_owned_turf_ids(user)),
        Booking.payment_status != PaymentStatus.unpaid,
    ]
    if date_from:
        filters.append(Booking.start_at >= datetime.combine(date_from, time.min, tzinfo=tz))
    if date_to:
        filters.append(Booking.start_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz))

    def total_for(payment_status: PaymentStatus) -> Decimal:
        value = db.scalar(
            select(func.coalesce(func.sum(Booking.paid_amount), 0)).where(
                *filters, Booking.payment_status == payment_status
            )
        )
        return Decimal(value)

    count = db.scalar(select(func.count()).select_from(Booking).where(*filters)) or 0
    items = db.scalars(
        select(Booking)
        .options(joinedload(Booking.turf), joinedload(Booking.user))
        .where(*filters)
        .order_by(Booking.paid_at.desc().nulls_last(), Booking.id.desc())
        .limit(limit)
    ).unique()
    return PaymentsSummary(
        count=count,
        total_collected=total_for(PaymentStatus.paid),
        refunds_pending=total_for(PaymentStatus.refund_pending),
        items=[BookingOut.model_validate(b) for b in items],
    )


# --- Customer messages -------------------------------------------------------


@router.post("/notifications", response_model=BroadcastResult, status_code=status.HTTP_201_CREATED)
def broadcast(payload: BroadcastCreate, user: OwnerUser, db: DbSession) -> BroadcastResult:
    """Send an in-app message (offer, alert, info) to customers who have booked your turfs."""
    if payload.turf_id is not None:
        turf_service.get_managed_turf(db, payload.turf_id, user)
        turf_ids = select(Turf.id).where(Turf.id == payload.turf_id)
    else:
        turf_ids = _owned_turf_ids(user)
    recipients = list(
        db.scalars(
            select(distinct(Booking.user_id)).where(
                Booking.turf_id.in_(turf_ids),
                Booking.status.in_([BookingStatus.confirmed, BookingStatus.completed]),
                Booking.user_id != user.id,
            )
        )
    )
    if recipients:
        db.execute(
            insert(Notification),
            [
                {
                    "user_id": uid,
                    "title": payload.title,
                    "message": payload.message,
                    "type": payload.type,
                    "turf_id": payload.turf_id,
                }
                for uid in recipients
            ],
        )
        db.commit()
    return BroadcastResult(recipients=len(recipients))
