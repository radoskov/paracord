"""FastAPI application entrypoint for PaRacORD."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.security import assert_no_guest_roles


def create_app() -> FastAPI:
    """Create and configure the PaRacORD API application."""
    settings = get_settings()
    # Fail fast if a guest/anonymous role was configured — there is no guest access.
    assert_no_guest_roles(settings.allowed_roles)
    app = FastAPI(
        title="PaRacORD API",
        version="0.0.0",
        description="Local-first scientific paper library and literature graph API.",
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
