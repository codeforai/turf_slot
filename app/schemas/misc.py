"""Schemas for reviews, notifications and dashboards."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.enums import NotificationType
from app.schemas import LocalDatetime, ORMModel


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ReviewUpdate(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None


class ReviewAuthor(ORMModel):
    id: int
    first_name: str


class ReviewOut(ORMModel):
    id: int
    turf_id: int
    rating: int
    comment: str
    author: ReviewAuthor = Field(validation_alias="user")
    created_at: LocalDatetime
    updated_at: LocalDatetime


class NotificationOut(ORMModel):
    id: int
    title: str
    message: str
    type: NotificationType
    turf_id: int | None
    booking_id: int | None
    is_read: bool
    created_at: LocalDatetime


class UnreadCount(BaseModel):
    unread: int


class BroadcastCreate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]
    type: Literal["info", "offer", "alert"] = "info"
    turf_id: int | None = Field(default=None, description="Limit to customers of one turf; omit for all your turfs")


class BroadcastResult(BaseModel):
    recipients: int


class OwnerDashboard(BaseModel):
    turfs: int
    active_turfs: int
    bookings_today: int
    pending_payments: int
    upcoming_confirmed: int
    refunds_pending: int
    revenue_this_month: Decimal
    revenue_total: Decimal


class AdminDashboard(BaseModel):
    users: int
    owners: int
    blocked_users: int
    turfs: int
    unverified_turfs: int
    bookings: int
    pending_payments: int
    revenue_total: Decimal
