"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import engine
from app.observability import ObservabilityMiddleware, configure_logging
from app.routers import admin, auth, bookings, health, me_extras, owner, turfs, users
from app.services.errors import ServiceError

logger = logging.getLogger("turfslot")

DESCRIPTION = """
Backend for **TurfSlot**, a sports turf booking platform.

* **Customers** search turfs, check availability, book slots, pay the turf directly
  (cash/UPI/bank transfer) and download receipts.
* **Turf owners** list turfs, confirm payments, manage bookings and message customers.
* **Admins** verify turfs, moderate users and reviews.

Use **POST /auth/login** (or the *Authorize* button) with your email as the username.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "starting",
        extra={"environment": settings.environment, "version": settings.app_version},
    )
    yield
    engine.dispose()
    logger.info("stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    for router in (
        health.router,
        auth.router,
        users.router,
        turfs.router,
        bookings.router,
        me_extras.favourites_router,
        me_extras.notifications_router,
        me_extras.reviews_router,
        owner.router,
        admin.router,
    ):
        app.include_router(router)

    if settings.storage_backend == "local":
        media = Path(settings.media_dir)
        media.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=media), name="media")

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    # Added last so it is the outermost layer and measures everything.
    app.add_middleware(ObservabilityMiddleware)
    return app


app = create_app()
