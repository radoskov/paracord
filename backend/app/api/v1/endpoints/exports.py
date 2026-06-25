"""Bibliography and citation export endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.schemas.export import ExportRequest, ExportResponse
from app.services.export_service import export_bibliography

router = APIRouter()
DB_DEP = Depends(get_db)
EXPORT_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR, Role.READER))


@router.post("", response_model=ExportResponse)
def export_scope(
    payload: ExportRequest,
    db: Session = DB_DEP,
    _=EXPORT_DEP,
) -> ExportResponse:
    """Export citations for a work, shelf, rack, search result, or selection."""
    scope_id = payload.scope_id or payload.target_id
    try:
        content = export_bibliography(
            db,
            scope_type=payload.scope_type,
            scope_id=scope_id,
            output_format=payload.format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    extension = "bib" if payload.format == "bibtex" else "txt"
    content_type = "application/x-bibtex" if payload.format == "bibtex" else "text/plain"
    return ExportResponse(
        filename=f"{payload.scope_type}-export.{extension}",
        content_type=content_type,
        content=content,
    )
