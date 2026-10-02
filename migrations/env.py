"""Alembic environment.

Migrations run on every deploy (see scripts/start.sh). A PostgreSQL advisory lock makes
sure that if several instances start at once, only one applies migrations while the
others wait, instead of racing each other.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers models on Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
MIGRATION_LOCK_KEY = 727_011_001  # arbitrary constant shared by all instances


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})")
        connection.commit()  # the session-level lock survives the commit
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
            connection.commit()
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})")
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
