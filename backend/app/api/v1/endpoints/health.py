"""Health endpoints."""

from fastapi import APIRouter

from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return basic service health."""
    return HealthResponse(status="ok")
