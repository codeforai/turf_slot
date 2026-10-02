"""Turf search, slugs, ownership checks and rating aggregates."""

import math
import re
import secrets
from decimal import Decimal
from typing import Literal

from sqlalchemy import Float, Select, cast, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Amenity, Review, Turf, User, UserRole, turf_amenities
from app.services.errors import Forbidden, NotFound, RuleViolation

SortOption = Literal["newest", "price_asc", "price_desc", "rating", "distance"]
EARTH_RADIUS_KM = 6371.0


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:200] or "turf"


def unique_slug(db: Session, name: str, location: str) -> str:
    base = slugify(f"{name} {location}")
    slug = base
    while db.scalar(select(Turf.id).where(Turf.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


def load_amenities(db: Session, ids: list[int]) -> list[Amenity]:
    unique_ids = set(ids)
    if not unique_ids:
        return []
    amenities = list(db.scalars(select(Amenity).where(Amenity.id.in_(unique_ids))))
    if len(amenities) != len(unique_ids):
        missing = unique_ids - {a.id for a in amenities}
        raise RuleViolation(f"Unknown amenity id(s): {sorted(missing)}")
    return amenities


def get_turf(db: Session, turf_id: int, *, public: bool = True) -> Turf:
    turf = db.get(Turf, turf_id)
    if turf is None or (public and not (turf.is_active and turf.is_verified)):
        raise NotFound("Turf not found")
    return turf


def get_managed_turf(db: Session, turf_id: int, user: User) -> Turf:
    turf = db.get(Turf, turf_id)
    if turf is None:
        raise NotFound("Turf not found")
    if user.role != UserRole.admin and turf.owner_id != user.id:
        raise Forbidden("You can only manage your own turfs")
    return turf


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _distance_km(lat: float, lng: float):
    """Haversine great-circle distance from (lat, lng) to each turf, computed in SQL."""
    turf_lat = cast(Turf.latitude, Float)
    turf_lng = cast(Turf.longitude, Float)
    a = func.power(func.sin(func.radians(turf_lat - lat) / 2), 2) + func.cos(func.radians(lat)) * func.cos(
        func.radians(turf_lat)
    ) * func.power(func.sin(func.radians(turf_lng - lng) / 2), 2)
    return EARTH_RADIUS_KM * 2 * func.asin(func.least(1.0, func.sqrt(a)))


def search_turfs(
    db: Session,
    *,
    q: str | None,
    sport_types: list[str] | None,
    min_price: Decimal | None,
    max_price: Decimal | None,
    amenity_ids: list[int] | None,
    lat: float | None,
    lng: float | None,
    radius_km: float,
    sort: SortOption | None,
    offset: int,
    limit: int,
) -> tuple[list[tuple[Turf, float | None]], int]:
    filters = [Turf.is_active.is_(True), Turf.is_verified.is_(True)]
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        filters.append(
            or_(
                Turf.name.ilike(pattern, escape="\\"),
                Turf.location.ilike(pattern, escape="\\"),
                Turf.address.ilike(pattern, escape="\\"),
            )
        )
    if sport_types:
        filters.append(Turf.sport_type.in_(sport_types))
    if min_price is not None:
        filters.append(Turf.price_per_hour >= min_price)
    if max_price is not None:
        filters.append(Turf.price_per_hour <= max_price)
    if amenity_ids:
        wanted = set(amenity_ids)
        has_all = (
            select(turf_amenities.c.turf_id)
            .where(turf_amenities.c.amenity_id.in_(wanted))
            .group_by(turf_amenities.c.turf_id)
            .having(func.count(func.distinct(turf_amenities.c.amenity_id)) == len(wanted))
        )
        filters.append(Turf.id.in_(has_all))

    distance = None
    if (lat is None) != (lng is None):
        raise RuleViolation("Provide both lat and lng to search by location")
    if lat is not None and lng is not None:
        distance = _distance_km(lat, lng)
        # Cheap bounding-box prefilter (can use the coordinates index), then the exact distance.
        dlat = radius_km / 111.0
        dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        filters += [
            Turf.latitude.is_not(None),
            Turf.latitude.between(lat - dlat, lat + dlat),
            Turf.longitude.between(lng - dlng, lng + dlng),
            distance <= radius_km,
        ]

    sort = sort or ("distance" if distance is not None else "newest")
    if sort == "distance" and distance is None:
        raise RuleViolation("Sorting by distance needs lat and lng")
    order_by = {
        "newest": [Turf.created_at.desc(), Turf.id.desc()],
        "price_asc": [Turf.price_per_hour.asc(), Turf.id],
        "price_desc": [Turf.price_per_hour.desc(), Turf.id],
        "rating": [Turf.rating_avg.desc(), Turf.review_count.desc(), Turf.id],
        "distance": [distance.asc(), Turf.id] if distance is not None else [],
    }[sort]

    total = db.scalar(select(func.count()).select_from(Turf).where(*filters)) or 0
    columns = [Turf, distance.label("distance_km")] if distance is not None else [Turf]
    stmt: Select = (
        select(*columns)
        .options(selectinload(Turf.amenities))
        .where(*filters)
        .order_by(*order_by)
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    results = [(row[0], float(row[1]) if distance is not None else None) for row in rows]
    return results, total


def recompute_rating(db: Session, turf_id: int) -> None:
    avg, count = db.execute(
        select(func.coalesce(func.avg(Review.rating), 0), func.count(Review.id)).where(Review.turf_id == turf_id)
    ).one()
    db.execute(
        update(Turf)
        .where(Turf.id == turf_id)
        .values(rating_avg=Decimal(str(avg)).quantize(Decimal("0.01")), review_count=count)
        .execution_options(synchronize_session=False)
    )
