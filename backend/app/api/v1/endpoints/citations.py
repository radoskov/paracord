"""Citation context endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/contexts")
def citation_contexts(scope_type: str = "library", scope_id: str | None = None) -> dict[str, str | None]:
    """Return citation contexts for a scope."""
    return {"status": "todo", "scope_type": scope_type, "scope_id": scope_id}
