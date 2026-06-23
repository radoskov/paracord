"""FastAPI application entrypoint for PaperRacks."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the PaperRacks API application."""
    settings = get_settings()
    app = FastAPI(
        title="PaperRacks API",
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
