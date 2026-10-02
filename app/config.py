"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-do-not-use-in-production"  # noqa: S105 (rejected in production)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General ---
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "TurfSlot API"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = ""  # comma-separated list of allowed origins

    # --- Database ---
    database_url: str = "postgresql+psycopg://turfslot:turfslot@localhost:5432/turfslot"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15_000  # 0 disables

    # --- Auth ---
    secret_key: str = INSECURE_DEFAULT_SECRET
    access_token_expire_minutes: int = 60
    password_hash_n: int = 2**15  # scrypt CPU/memory cost (32 MiB per hash with r=8)
    password_reset_code_minutes: int = 15
    password_reset_max_attempts: int = 5

    # --- Booking rules ---
    timezone: str = "Asia/Kolkata"
    booking_slot_minutes: int = 30
    booking_hold_minutes: int = 120  # unpaid bookings are released after this; 0 = never
    booking_max_days_ahead: int = 60
    cancellation_cutoff_minutes: int = 60  # customers can cancel up to this long before start
    max_pending_bookings_per_user: int = 3

    # --- Email ---
    email_backend: Literal["console", "smtp", "memory"] = "console"
    email_from: str = "TurfSlot <no-reply@turfslot.local>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    # --- File storage ---
    storage_backend: Literal["local", "cloudinary"] = "local"
    media_dir: str = "media"
    cloudinary_url: str = ""  # cloudinary://<api_key>:<api_secret>@<cloud_name>
    max_upload_mb: int = 5

    # --- Observability ---
    metrics_enabled: bool = True
    metrics_token: str = ""  # if set, /metrics requires "Authorization: Bearer <token>"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Render/Heroku-style URLs start with postgres:// or postgresql://; SQLAlchemy needs the driver.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @model_validator(mode="after")
    def _check_production_safety(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of at least 32 characters in production")
            if self.storage_backend == "cloudinary" and not self.cloudinary_url:
                raise ValueError("CLOUDINARY_URL is required when STORAGE_BACKEND=cloudinary")
            if self.email_backend == "smtp" and not self.smtp_host:
                raise ValueError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        ZoneInfo(self.timezone)  # fail fast on an invalid timezone name
        if 60 % self.booking_slot_minutes != 0:
            raise ValueError("BOOKING_SLOT_MINUTES must divide 60 evenly (e.g. 15, 30, 60)")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
