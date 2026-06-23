"""Shelf endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_shelves() -> dict[str, str]:
    """List shelves."""
    return {"status": "todo"}
