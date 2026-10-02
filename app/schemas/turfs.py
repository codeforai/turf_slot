from datetime import date, time
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.enums import SportType, SurfaceType, TurfStatus
from app.schemas import LocalDatetime, ORMModel

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
Text255 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=255)]


class AmenityCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=50)]


class AmenityOut(ORMModel):
    id: int
    name: str


class TurfImageOut(ORMModel):
    id: int
    url: str


class _TurfRules(BaseModel):
    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        lo = getattr(self, "min_booking_hours", None)
        hi = getattr(self, "max_booking_hours", None)
        if lo is not None and hi is not None and hi < lo:
            raise ValueError("max_booking_hours must be >= min_booking_hours")
        lat_given = "latitude" in self.model_fields_set
        lng_given = "longitude" in self.model_fields_set
        lat = getattr(self, "latitude", None)
        lng = getattr(self, "longitude", None)
        if lat_given != lng_given or (lat is None) != (lng is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class TurfCreate(_TurfRules):
    name: Text200
    sport_type: SportType
    description: Annotated[str, StringConstraints(max_length=5000)] = ""
    location: Text255 = Field(description="Area or city, e.g. 'Kakkanad, Kochi'")
    address: Text255
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    length_m: int = Field(gt=0, le=1000)
    width_m: int = Field(gt=0, le=1000)
    surface_type: SurfaceType
    capacity: int = Field(gt=0, le=1000)
    price_per_hour: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    min_booking_hours: int = Field(default=1, ge=1, le=12)
    max_booking_hours: int = Field(default=4, ge=1, le=12)
    opening_time: time
    closing_time: time = Field(description="May be earlier than opening_time for turfs open past midnight")
    amenity_ids: list[int] = []


class TurfUpdate(_TurfRules):
    name: Text200 | None = None
    sport_type: SportType | None = None
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    location: Text255 | None = None
    address: Text255 | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    length_m: int | None = Field(default=None, gt=0, le=1000)
    width_m: int | None = Field(default=None, gt=0, le=1000)
    surface_type: SurfaceType | None = None
    capacity: int | None = Field(default=None, gt=0, le=1000)
    price_per_hour: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    min_booking_hours: int | None = Field(default=None, ge=1, le=12)
    max_booking_hours: int | None = Field(default=None, ge=1, le=12)
    opening_time: time | None = None
    closing_time: time | None = None
    status: TurfStatus | None = None
    amenity_ids: list[int] | None = None


class TurfSummary(ORMModel):
    id: int
    slug: str
    name: str
    sport_type: SportType
    location: str
    price_per_hour: Decimal
    rating_avg: Decimal
    review_count: int
    cover_image_url: str | None
    status: TurfStatus
    amenities: list[AmenityOut]
    distance_km: float | None = None


class TurfDetail(TurfSummary):
    owner_id: int
    description: str
    address: str
    latitude: Decimal | None
    longitude: Decimal | None
    length_m: int
    width_m: int
    surface_type: SurfaceType
    capacity: int
    min_booking_hours: int
    max_booking_hours: int
    opening_time: time
    closing_time: time
    images: list[TurfImageOut]
    is_verified: bool
    is_active: bool
    created_at: LocalDatetime


class BusyRange(BaseModel):
    start_at: LocalDatetime
    end_at: LocalDatetime


class Availability(BaseModel):
    turf_id: int
    date: date
    duration_hours: int
    slot_minutes: int
    opens_at: LocalDatetime
    closes_at: LocalDatetime
    available_start_times: list[LocalDatetime]
    booked: list[BusyRange]
