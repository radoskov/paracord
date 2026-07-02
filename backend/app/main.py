"""FastAPI application entrypoint for PaRacORD."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.security import assert_no_guest_roles
from app.services import rate_limit

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup housekeeping. Each step is guarded so it never blocks the API from serving.

    * D11 — idempotently place any loose paper onto the default shelf, closing the rolling-deploy
      window where new-code workers create papers before migration 0037 has run on an older DB.
    * D7 — re-enqueue extractions owed but lost to a dead Redis (deterministic job id → safe to run
      from several API workers in parallel).
    """
    try:
        from app.db.session import SessionLocal
        from app.services.default_shelf import backfill_loose_papers_onto_default

        with SessionLocal() as db:
            placed = backfill_loose_papers_onto_default(db)
            db.commit()
        if placed:
            logger.info("Startup: placed %d loose paper(s) on the default shelf (D11)", placed)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a backfill hiccup / race
        logger.warning("Startup default-shelf backfill skipped: %s", exc)
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

    from app.services.app_config import BatchTooLargeError

    @app.exception_handler(BatchTooLargeError)
    async def _batch_too_large(_request: Request, exc: BatchTooLargeError) -> JSONResponse:
        """A client import batch over ``max_batch_items`` is rejected with 413 (D1)."""
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        """Shared Redis rate limiting (D1). Fails open — a dead Redis never blocks requests."""
        if is_websocket_or_exempt(request):
            return await call_next(request)
        scheme, _, raw_token = (request.headers.get("authorization") or "").partition(" ")
        token = raw_token.strip() if scheme.lower() == "bearer" else None
        ip = request.client.host if request.client else None
        decision = rate_limit.check(token=token, ip=ip)
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Rate limit exceeded; slow down and retry shortly."
                        if decision.scope == "client"
                        else "The server is busy (global rate limit); retry shortly."
                    )
                },
                headers={"Retry-After": str(decision.retry_after)},
            )
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")
    return app


def is_websocket_or_exempt(request: Request) -> bool:
    """True when a request must skip rate limiting (non-HTTP scope or an exempt path)."""
    return request.scope.get("type") != "http" or rate_limit.is_exempt(request.url.path)


app = create_app()
