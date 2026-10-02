from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, computed_field

from app.config import get_settings
from app.enums import BookingStatus, PaymentMethod, PaymentStatus
from app.schemas import LocalDatetime, ORMModel
from app.schemas.users import UserMini


class BookingCreate(BaseModel):
    turf_id: int
    date: date
    start_time: time = Field(description="Local start time, e.g. 18:00")
    duration_hours: int = Field(ge=1, le=12)
    payment_method: PaymentMethod | None = Field(
        default=None, description="How the customer intends to pay; the owner confirms receipt"
    )
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class PaymentConfirm(BaseModel):
    method: PaymentMethod
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = Field(
        default=None, description="UPI transaction ID, bank reference, etc."
    )
    amount: Decimal | None = Field(
        default=None, ge=0, max_digits=10, decimal_places=2, description="Defaults to the booking total"
    )


class CancelRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None


class TurfMini(ORMModel):
    id: int
    slug: str
    name: str
    location: str
    address: str


class BookingOut(ORMModel):
    id: int
    reference: str
    turf: TurfMini
    customer: UserMini = Field(validation_alias="user")
    start_at: LocalDatetime
    end_at: LocalDatetime
    status: BookingStatus
    payment_status: PaymentStatus
    payment_method: PaymentMethod | None
    payment_reference: str | None
    total_amount: Decimal
    paid_amount: Decimal | None
    paid_at: LocalDatetime | None
    hold_expires_at: LocalDatetime | None
    notes: str
    cancellation_reason: str | None
    created_at: LocalDatetime

    @computed_field
    @property
    def duration_hours(self) -> float:
        return (self.end_at - self.start_at).total_seconds() / 3600

    @computed_field
    @property
    def local_date(self) -> date:
        return self.start_at.astimezone(get_settings().tz).date()


BookingScope = Literal["upcoming", "past", "all"]


class PaymentsSummary(BaseModel):
    count: int
    total_collected: Decimal
    refunds_pending: Decimal
    items: list[BookingOut]
