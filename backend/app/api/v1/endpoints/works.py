"""Work endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_works() -> dict[str, str]:
    """List/search works."""
    return {"status": "todo"}
