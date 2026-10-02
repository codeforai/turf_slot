"""Database engine and session management."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _build_engine():
    settings = get_settings()
    options = "-c timezone=UTC"
    if settings.db_statement_timeout_ms > 0:
        options += f" -c statement_timeout={settings.db_statement_timeout_ms}"
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,  # drop dead connections (e.g. after a DB restart) instead of failing requests
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
        connect_args={"options": options},
    )


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
