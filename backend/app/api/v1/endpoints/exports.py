"""Bibliography and citation export endpoints."""

from fastapi import APIRouter

from app.schemas.export import ExportRequest, ExportResponse

router = APIRouter()


@router.post("", response_model=ExportResponse)
def export_scope(payload: ExportRequest) -> ExportResponse:
    """Export citations for a work, shelf, rack, search result, or selection."""
    return ExportResponse(filename="todo.txt", content_type="text/plain", content="TODO")
