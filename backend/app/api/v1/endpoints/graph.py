"""Citation graph endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def graph(scope_type: str = "library", scope_id: str | None = None) -> dict[str, str | None]:
    """Return scoped citation graph nodes and edges."""
    return {"status": "todo", "scope_type": scope_type, "scope_id": scope_id}
