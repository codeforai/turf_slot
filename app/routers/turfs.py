from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import CurrentUser, DbSession, PageParams
from app.models import Amenity, Booking, BookingStatus, Review, SportType, Turf
from app.schemas import Page
from app.schemas.misc import ReviewCreate, ReviewOut
from app.schemas.turfs import AmenityOut, Availability, BusyRange, TurfDetail, TurfSummary
from app.services import bookings as booking_service
from app.services import turfs as turf_service

router = APIRouter(tags=["turfs"])


@router.get("/amenities", response_model=list[AmenityOut])
def list_amenities(db: DbSession) -> list[Amenity]:
    return list(db.scalars(select(Amenity).order_by(Amenity.name)))


@router.get("/turfs", response_model=Page[TurfSummary])
def search_turfs(
    db: DbSession,
    pagination: PageParams,
    q: Annotated[str | None, Query(max_length=100, description="Matches name, area or address")] = None,
    sport_type: Annotated[list[SportType] | None, Query()] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    amenity: Annotated[list[int] | None, Query(description="Turf must have all of these amenity ids")] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=100)] = 25,
    sort: turf_service.SortOption | None = None,
) -> Page[TurfSummary]:
    """Search verified turfs. With `lat`/`lng`, results are limited to `radius_km` and sorted by distance."""
    results, total = turf_service.search_turfs(
        db,
        q=q,
        sport_types=[s.value for s in sport_type] if sport_type else None,
        min_price=min_price,
        max_price=max_price,
        amenity_ids=amenity,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        sort=sort,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    items = []
    for turf, distance in results:
        item = TurfSummary.model_validate(turf)
        item.distance_km = round(distance, 2) if distance is not None else None
        items.append(item)
    return Page(items=items, total=total, page=pagination.page, page_size=pagination.page_size)


def _detail_query():
    return select(Turf).options(selectinload(Turf.amenities), selectinload(Turf.images))


@router.get("/turfs/slug/{slug}", response_model=TurfDetail)
def get_turf_by_slug(slug: str, db: DbSession) -> Turf:
    turf = db.scalar(_detail_query().where(Turf.slug == slug, Turf.is_active.is_(True), Turf.is_verified.is_(True)))
    if turf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turf not found")
    return turf


@router.get("/turfs/{turf_id}", response_model=TurfDetail)
def get_turf(turf_id: int, db: DbSession) -> Turf:
    turf = db.scalar(_detail_query().where(Turf.id == turf_id, Turf.is_active.is_(True), Turf.is_verified.is_(True)))
    if turf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turf not found")
    return turf


@router.get("/turfs/{turf_id}/availability", response_model=Availability)
def get_availability(
    turf_id: int,
    db: DbSession,
    day: Annotated[date, Query(alias="date", description="Local date, YYYY-MM-DD")],
    duration_hours: Annotated[int, Query(ge=1, le=12)] = 1,
) -> Availability:
    """Start times that are free for the requested duration. Unpaid holds that have expired count as free."""
    turf = turf_service.get_turf(db, turf_id)
    window, busy, starts = booking_service.availability(db, turf, day, duration_hours)
    return Availability(
        turf_id=turf.id,
        date=day,
        duration_hours=duration_hours,
        slot_minutes=get_settings().booking_slot_minutes,
        opens_at=window.start,
        closes_at=window.end,
        available_start_times=starts,
        booked=[BusyRange(start_at=s, end_at=e) for s, e in busy],
    )


@router.get("/turfs/{turf_id}/reviews", response_model=Page[ReviewOut])
def list_reviews(turf_id: int, db: DbSession, pagination: PageParams) -> Page[ReviewOut]:
    turf_service.get_turf(db, turf_id)
    total = db.scalar(select(func.count()).select_from(Review).where(Review.turf_id == turf_id)) or 0
    reviews = db.scalars(
        select(Review)
        .options(selectinload(Review.user))
        .where(Review.turf_id == turf_id)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return Page(
        items=[ReviewOut.model_validate(r) for r in reviews],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/turfs/{turf_id}/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def create_review(turf_id: int, payload: ReviewCreate, user: CurrentUser, db: DbSession) -> Review:
    """Only customers who have played at the turf (a paid booking that has ended) can review it."""
    turf_service.get_turf(db, turf_id)
    played = db.scalar(
        select(Booking.id)
        .where(
            Booking.turf_id == turf_id,
            Booking.user_id == user.id,
            Booking.status.in_([BookingStatus.confirmed, BookingStatus.completed]),
            Booking.end_at <= datetime.now(UTC),
        )
        .limit(1)
    )
    if played is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can review a turf after you have played there")
    review = Review(turf_id=turf_id, user_id=user.id, rating=payload.rating, comment=payload.comment)
    db.add(review)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You have already reviewed this turf") from None
    turf_service.recompute_rating(db, turf_id)
    db.commit()
    db.refresh(review)
    return review
