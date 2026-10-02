from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.config import get_settings

T = TypeVar("T")


def _to_local(value: datetime) -> str:
    return value.astimezone(get_settings().tz).isoformat()


# Datetimes are stored in UTC and returned in the app timezone (e.g. 2026-10-05T18:00:00+05:30).
LocalDatetime = Annotated[datetime, PlainSerializer(_to_local, return_type=str)]


class ORMModel(BaseModel):
    # validate_by_name: FastAPI dumps response models to dicts (by field name) and validates them
    # again, so fields read from a differently named ORM attribute (validation_alias) must also
    # accept their own name.
    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Message(BaseModel):
    detail: str
