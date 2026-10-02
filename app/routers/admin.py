"""Platform admin: verify turfs, block users, manage amenities, see everything."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.deps import AdminUser, DbSession, PageParams
from app.models import (
    Amenity,
    Booking,
    BookingStatus,
    NotificationType,
    PaymentStatus,
    Turf,
    User,
    UserRole,
)
from app.schemas import Page
from app.schemas.bookings import BookingOut
from app.schemas.misc import AdminDashboard
from app.schemas.turfs import AmenityCreate, AmenityOut, TurfDetail
from app.schemas.users import UserOut
from app.services import bookings as booking_service
from app.services.notify import add_notification
from app.services.turfs import escape_like

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard", response_model=AdminDashboard)
def dashboard(_: AdminUser, db: DbSession) -> AdminDashboard:
    def count(model, *conditions) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    revenue = db.scalar(
        select(func.coalesce(func.sum(Booking.paid_amount), 0)).where(Booking.payment_status == PaymentStatus.paid)
    )
    return AdminDashboard(
        users=count(User, User.role == UserRole.user),
        owners=count(User, User.role == UserRole.owner),
        blocked_users=count(User, User.is_active.is_(False)),
        turfs=count(Turf, Turf.is_active.is_(True)),
        unverified_turfs=count(Turf, Turf.is_active.is_(True), Turf.is_verified.is_(False)),
        bookings=count(Booking),
        pending_payments=count(Booking, Booking.status == BookingStatus.pending),
        revenue_total=revenue,
    )


# --- Users -------------------------------------------------------------------


@router.get("/users", response_model=Page[UserOut])
def list_users(
    _: AdminUser,
    db: DbSession,
    pagination: PageParams,
    role: UserRole | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Name, email or phone")] = None,
) -> Page[UserOut]:
    filters = []
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        filters.append(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                (User.first_name + " " + User.last_name).ilike(pattern, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    users = db.scalars(
        select(User)
        .where(*filters)
        .order_by(User.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return Page(
        items=[UserOut.model_validate(u) for u in users],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


def _target_user(db: DbSession, user_id: int, admin: User) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id or user.role == UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admins cannot be blocked or unblocked here")
    return user


@router.post("/users/{user_id}/block", response_model=UserOut)
def block_user(user_id: int, admin: AdminUser, db: DbSession) -> User:
    """Blocked users are rejected on their next request, even with a valid token."""
    user = _target_user(db, user_id, admin)
    user.is_active = False
    db.commit()
    return user


@router.post("/users/{user_id}/unblock", response_model=UserOut)
def unblock_user(user_id: int, admin: AdminUser, db: DbSession) -> User:
    user = _target_user(db, user_id, admin)
    user.is_active = True
    db.commit()
    return user


# --- Turfs -------------------------------------------------------------------


@router.get("/turfs", response_model=Page[TurfDetail])
def list_turfs(
    _: AdminUser,
    db: DbSession,
    pagination: PageParams,
    verified: bool | None = None,
    active: bool | None = True,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[TurfDetail]:
    """Use `verified=false` for the verification queue."""
    filters = []
    if verified is not None:
        filters.append(Turf.is_verified.is_(verified))
    if active is not None:
        filters.append(Turf.is_active.is_(active))
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        filters.append(or_(Turf.name.ilike(pattern, escape="\\"), Turf.location.ilike(pattern, escape="\\")))
    total = db.scalar(select(func.count()).select_from(Turf).where(*filters)) or 0
    turfs = db.scalars(
        select(Turf)
        .options(selectinload(Turf.amenities), selectinload(Turf.images))
        .where(*filters)
        .order_by(Turf.created_at.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return Page(
        items=[TurfDetail.model_validate(t) for t in turfs],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


def _set_verified(db: DbSession, turf_id: int, verified: bool) -> Turf:
    turf = db.scalar(
        select(Turf).options(selectinload(Turf.amenities), selectinload(Turf.images)).where(Turf.id == turf_id)
    )
    if turf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turf not found")
    if turf.is_verified != verified:
        turf.is_verified = verified
        add_notification(
            db,
            user_id=turf.owner_id,
            title=f"{turf.name} is {'now live' if verified else 'hidden from search'}",
            message=(
                "Your turf was verified and now appears in search."
                if verified
                else "Your turf was unverified by an admin and no longer appears in search."
            ),
            type=NotificationType.info if verified else NotificationType.alert,
            turf_id=turf.id,
        )
    db.commit()
    return turf


@router.post("/turfs/{turf_id}/verify", response_model=TurfDetail)
def verify_turf(turf_id: int, _: AdminUser, db: DbSession) -> Turf:
    return _set_verified(db, turf_id, True)


@router.post("/turfs/{turf_id}/unverify", response_model=TurfDetail)
def unverify_turf(turf_id: int, _: AdminUser, db: DbSession) -> Turf:
    return _set_verified(db, turf_id, False)


# --- Amenities ---------------------------------------------------------------


@router.post("/amenities", response_model=AmenityOut, status_code=status.HTTP_201_CREATED)
def create_amenity(payload: AmenityCreate, _: AdminUser, db: DbSession) -> Amenity:
    amenity = Amenity(name=payload.name)
    db.add(amenity)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That amenity already exists") from None
    return amenity


@router.delete("/amenities/{amenity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_amenity(amenity_id: int, _: AdminUser, db: DbSession) -> None:
    amenity = db.get(Amenity, amenity_id)
    if amenity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Amenity not found")
    db.delete(amenity)
    db.commit()


# --- Bookings & maintenance --------------------------------------------------


@router.get("/bookings", response_model=Page[BookingOut])
def list_all_bookings(
    _: AdminUser,
    db: DbSession,
    pagination: PageParams,
    turf_id: int | None = None,
    user_id: int | None = None,
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
    payment_status: PaymentStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[BookingOut]:
    items, total = booking_service.list_bookings(
        db,
        turf_id=turf_id,
        user_id=user_id,
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


@router.post("/maintenance/expire-holds")
def expire_holds(_: AdminUser, db: DbSession) -> dict[str, int]:
    """Release all unpaid holds that have run out. Holds are also released lazily whenever
    someone books the same turf, so this is only needed to keep listings tidy (e.g. from a cron job)."""
    released = booking_service.expire_stale_holds(db, booking_service.utcnow())
    db.commit()
    return {"released": released}
