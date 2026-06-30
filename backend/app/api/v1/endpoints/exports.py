"""Bibliography and citation export endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.export import ExportRequest, ExportResponse
from app.services import access
from app.services.export_service import export_bibliography, media_for

router = APIRouter()
DB_DEP = Depends(get_db)
# Export is a read operation: any authenticated user (reader+) may export, but only the works they
# may SEE are included (and a shelf/rack scope requires SEE on that container).
EXPORT_DEP = Depends(require_authenticated_user)


@router.post("", response_model=ExportResponse)
def export_scope(
    payload: ExportRequest,
    db: Session = DB_DEP,
    actor: User = EXPORT_DEP,
) -> ExportResponse:
    """Export citations for a work, shelf, rack, search result, or selection.

    Access control: the export is filtered to papers the caller may SEE; a shelf/rack scope
    requires SEE on that container.
    """
    scope_id = payload.scope_id or payload.target_id
    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    try:
        content = export_bibliography(
            db,
            scope_type=payload.scope_type,
            scope_id=scope_id,
            work_ids=payload.work_ids,
            output_format=payload.format,
            style=payload.style,
            citation_keys=payload.citation_keys,
            actor_user_id=actor.id,
            visible_ids=access.visible_work_ids(db, actor),
        )
        extension, content_type = media_for(payload.format)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return ExportResponse(
        filename=f"{payload.scope_type}-export.{extension}",
        content_type=content_type,
        content=content,
    )
