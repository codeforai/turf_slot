"""Favourites, notifications and review edits for the signed-in user."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DbSession, PageParams
from app.models import Favourite, Notification, Review, Turf, UserRole
from app.schemas import Page
from app.schemas.misc import NotificationOut, ReviewOut, ReviewUpdate, UnreadCount
from app.schemas.turfs import TurfSummary
from app.services import turfs as turf_service

favourites_router = APIRouter(prefix="/favourites", tags=["favourites"])
notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])
reviews_router = APIRouter(prefix="/reviews", tags=["reviews"])


# --- Favourites --------------------------------------------------------------


@favourites_router.get("", response_model=list[TurfSummary])
def list_favourites(user: CurrentUser, db: DbSession) -> list[Turf]:
    return list(
        db.scalars(
            select(Turf)
            .join(Favourite, Favourite.turf_id == Turf.id)
            .options(selectinload(Turf.amenities))
            .where(Favourite.user_id == user.id, Turf.is_active.is_(True))
            .order_by(Favourite.created_at.desc())
        )
    )


@favourites_router.put("/{turf_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_favourite(turf_id: int, user: CurrentUser, db: DbSession) -> None:
    """Idempotent: favouriting twice is fine."""
    turf_service.get_turf(db, turf_id)
    db.execute(insert(Favourite).values(user_id=user.id, turf_id=turf_id).on_conflict_do_nothing())
    db.commit()


@favourites_router.delete("/{turf_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favourite(turf_id: int, user: CurrentUser, db: DbSession) -> None:
    db.execute(delete(Favourite).where(Favourite.user_id == user.id, Favourite.turf_id == turf_id))
    db.commit()


# --- Notifications -----------------------------------------------------------


@notifications_router.get("", response_model=Page[NotificationOut])
def list_notifications(
    user: CurrentUser, db: DbSession, pagination: PageParams, unread_only: bool = False
) -> Page[NotificationOut]:
    filters = [Notification.user_id == user.id]
    if unread_only:
        filters.append(Notification.is_read.is_(False))
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    items = db.scalars(
        select(Notification)
        .where(*filters)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return Page(
        items=[NotificationOut.model_validate(n) for n in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@notifications_router.get("/unread-count", response_model=UnreadCount)
def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
    )
    return UnreadCount(unread=count or 0)


@notifications_router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(user: CurrentUser, db: DbSession) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()


def _own_notification(db: DbSession, notification_id: int, user_id: int) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return notification


@notifications_router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: CurrentUser, db: DbSession) -> Notification:
    notification = _own_notification(db, notification_id, user.id)
    notification.is_read = True
    db.commit()
    return notification


@notifications_router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(notification_id: int, user: CurrentUser, db: DbSession) -> None:
    db.delete(_own_notification(db, notification_id, user.id))
    db.commit()


# --- Reviews -----------------------------------------------------------------


def _editable_review(db: DbSession, review_id: int, user: CurrentUser, *, allow_admin: bool) -> Review:
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Review not found")
    if review.user_id != user.id and not (allow_admin and user.role == UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only change your own review")
    return review


@reviews_router.patch("/{review_id}", response_model=ReviewOut)
def update_review(review_id: int, payload: ReviewUpdate, user: CurrentUser, db: DbSession) -> Review:
    review = _editable_review(db, review_id, user, allow_admin=False)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(review, field, value)
    db.flush()
    turf_service.recompute_rating(db, review.turf_id)
    db.commit()
    db.refresh(review)
    return review


@reviews_router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(review_id: int, user: CurrentUser, db: DbSession) -> None:
    """Authors can delete their review; admins can remove any review (moderation)."""
    review = _editable_review(db, review_id, user, allow_admin=True)
    turf_id = review.turf_id
    db.delete(review)
    db.flush()
    turf_service.recompute_rating(db, turf_id)
    db.commit()
