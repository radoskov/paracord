"""FastAPI application entrypoint for PaRacORD."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.security import assert_no_guest_roles

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """On startup, re-enqueue any extractions owed but lost to a dead Redis (D7 recovery sweep).

    Guarded so a failing/absent queue never blocks the API from serving; idempotent (deterministic
    job id) so it is safe even when several API workers run this in parallel.
    """
    try:
        from app.workers.recovery import sweep_owed_extractions

        result = sweep_owed_extractions()
        if result.get("considered"):
            logger.info("Startup recovery sweep: %s", result)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a recovery hiccup
        logger.warning("Startup recovery sweep skipped: %s", exc)
    yield


def create_app() -> FastAPI:
    """Create and configure the PaRacORD API application."""
    settings = get_settings()
    # Fail fast if a guest/anonymous role was configured — there is no guest access.
    assert_no_guest_roles(settings.allowed_roles)
    app = FastAPI(
        title="PaRacORD API",
        version="0.0.0",
        description="Local-first scientific paper library and literature graph API.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
