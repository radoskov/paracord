"""Rack endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_racks() -> dict[str, str]:
    """List racks."""
    return {"status": "todo"}
