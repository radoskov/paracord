"""FastAPI application entrypoint for PaRacORD."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from starlette.concurrency import run_in_threadpool

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
    # F2 — recover the downstream stages (chunk/embed) for works extracted but never indexed, derived
    # from state (extracted file + missing chunks/embedding). Best-effort; deterministic ids coalesce.
    try:
        from app.workers.recovery import sweep_owed_downstream

        downstream = sweep_owed_downstream()
        if downstream.get("chunk") or downstream.get("embed"):
            logger.info("Startup downstream recovery: %s", downstream)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a recovery hiccup
        logger.warning("Startup downstream recovery skipped: %s", exc)
    # D3 — plaintext-transport warning: session JWTs and agent bearer tokens cross the network
    # unencrypted when the server is reachable beyond loopback over http. Not fatal (that would
    # break existing LAN deployments), but loud, and silenceable only by explicit opt-in.
    try:
        settings = get_settings()
        loopback = settings.bind_host in ("127.0.0.1", "::1", "localhost")
        plaintext = settings.public_base_url.startswith("http://")
        if plaintext and not loopback and not settings.allow_insecure_http:
            logger.warning(
                "SECURITY (D3): serving plaintext HTTP on a non-loopback bind (%s) — session and "
                "agent tokens are sniffable on this network. Put a TLS proxy in front (see "
                "INSTALL.md 'TLS on the LAN') or set PARACORD_ALLOW_INSECURE_HTTP=true to "
                "acknowledge the risk and silence this warning.",
                settings.bind_host,
            )
    except Exception:  # noqa: BLE001 - a warning must never break startup
        pass
    # S13/S14 — consolidate duplicate canonical references once per startup (owner decision:
    # unconditional). Best-effort + coalesced (deterministic job id); a dead Redis just skips it.
    # Contradictions are never auto-folded — they land in Admin → Reference dupes for review.
    try:
        from app.workers.queue import enqueue_reference_consolidation

        job_id = enqueue_reference_consolidation()
        if job_id:
            logger.info("Startup reference consolidation enqueued: %s", job_id)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a consolidation hiccup
        logger.warning("Startup reference consolidation skipped: %s", exc)
    # F3a — optionally re-run a full reference→work rematch on startup (owner toggle) so the stored
    # resolution stays fresh across deploys. Best-effort + coalesced (deterministic job id), so it is
    # safe to run from several API workers; a dead Redis just skips it (enqueue returns None).
    try:
        from app.db.session import SessionLocal
        from app.services.app_config import effective_reference_rescan_on_startup

        with SessionLocal() as db:
            do_rescan = effective_reference_rescan_on_startup(db)
        if do_rescan:
            from app.workers.queue import enqueue_reference_rescan

            logger.info("Startup reference rescan enqueued: %s", enqueue_reference_rescan())
    except Exception as exc:  # noqa: BLE001 - startup must not fail on a rescan hiccup
        logger.warning("Startup reference rescan skipped: %s", exc)
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

    @app.middleware("http")
    async def _error_envelope(request: Request, call_next):
        """Request-id tagging + a descriptive error envelope for otherwise-unhandled exceptions.

        Every request gets an id (incoming ``X-Request-ID`` or a fresh one) that is echoed on the
        response and embedded in error details, so a UI error message can be matched to the exact
        server-log traceback.

        This is deliberately a MIDDLEWARE registered BEFORE (= wrapped by) CORSMiddleware, not an
        ``@app.exception_handler(Exception)``: Starlette routes an ``Exception`` handler to the
        outermost ServerErrorMiddleware, whose response bypasses CORS — the browser then blocks
        the response and fetch() reports only "NetworkError", hiding the real cause (exactly what
        made the NUL-byte upload failure undiagnosable from the UI). Here the JSON error passes
        through CORS like any normal response.

        Two tiers:
        * ``sqlalchemy.DataError`` (bad values for a column: NUL bytes, over-length, out-of-range)
          → 400 with the database's own first-line reason — client input, not a server fault;
        * anything else → 500 with the exception class + message. This intentionally exposes the
          exception text: PaRacORD is a self-hosted, LAN-scoped tool where diagnosability beats
          secrecy (owner decision, 2026-07-17). The full traceback goes to the server log tagged
          with the request id.
        """
        rid = (request.headers.get("x-request-id") or "").strip()[:32] or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except DataError as exc:
            logger.exception(
                "DataError on %s %s [request %s]", request.method, request.url.path, rid
            )
            reason = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
            return JSONResponse(
                status_code=400,
                content={
                    "detail": f"The database rejected the data: {reason} [request {rid}]",
                    "request_id": rid,
                },
                headers={"X-Request-ID": rid},
            )
        except Exception as exc:  # noqa: BLE001 - the whole point: no error leaves undescribed
            logger.exception(
                "Unhandled %s on %s %s [request %s]",
                type(exc).__name__,
                request.method,
                request.url.path,
                rid,
            )
            message = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
            described = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
            return JSONResponse(
                status_code=500,
                content={
                    "detail": (
                        f"Internal server error ({described}) [request {rid}] — the full "
                        "traceback is in the server logs."
                    ),
                    "request_id": rid,
                },
                headers={"X-Request-ID": rid},
            )
        response.headers["X-Request-ID"] = rid
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    from app.services.app_config import BatchTooLargeError

    @app.exception_handler(BatchTooLargeError)
    async def _batch_too_large(_request: Request, exc: BatchTooLargeError) -> JSONResponse:
        """A client import batch over ``max_batch_items`` is rejected with 413 (D1)."""
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    from app.errors import DomainError

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        """Domain errors raised by services map to their HTTP status here (S4) — one handler
        instead of per-endpoint try/except, and services stay framework-free."""
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        """Shared Redis rate limiting (D1). Fails open by default; fails closed with 503 when
        ``PARACORD_PRODUCTION_REQUIRE_REDIS`` is set and Redis is unreachable (E1)."""
        if is_websocket_or_exempt(request):
            return await call_next(request)
        scheme, _, raw_token = (request.headers.get("authorization") or "").partition(" ")
        token = raw_token.strip() if scheme.lower() == "bearer" else None
        ip = request.client.host if request.client else None
        # OFF the event loop: check() reads the effective ceilings from the DB on cache expiry
        # (sync SQLAlchemy). Run inline it once froze the WHOLE loop: under a request burst the
        # pool was momentarily empty, the loop blocked on the pool checkout, and every in-flight
        # threadpool request (each holding a connection) froze mid-response — none could return
        # its connection through the frozen loop, deadlocking the API permanently (2026-07-17).
        decision = await run_in_threadpool(rate_limit.check, token=token, ip=ip)
        if not decision.allowed:
            if decision.scope == "unavailable":
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": (
                            "Rate limiting is unavailable (Redis unreachable) and the server "
                            "requires it; retry shortly."
                        )
                    },
                    headers={"Retry-After": str(decision.retry_after)},
                )
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
