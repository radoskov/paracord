"""Bibliography and citation export endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.export import ExportRequest, ExportResponse
from app.services import access, csl
from app.services.export_service import export_bibliography, media_for
from app.services.saved_filters import (
    get_owned_saved_filter,
    resolve_saved_filter_work_ids,
)

router = APIRouter()
DB_DEP = Depends(get_db)


@router.get("/styles")
def list_citation_styles() -> list[dict[str, str]]:
    """List the citation styles available for the ``styled`` export format.

    Returned as ``[{"value", "label"}, ...]`` so the frontend can populate the style selector
    dynamically instead of hard-coding the list.
    """
    return csl.available_styles()


# Export is a read operation: any authenticated user (reader+) may export, but only the works they
# may SEE are included (and a shelf/rack scope requires SEE on that container).
EXPORT_DEP = Depends(require_authenticated_user)


@router.post("", response_model=ExportResponse)
def export_scope(
    payload: ExportRequest,
    db: Session = DB_DEP,
    actor: User = EXPORT_DEP,
) -> ExportResponse:
    """Export citations for a work, shelf, rack, search result, selection, or saved filter.

    Access control: the export is filtered to papers the caller may SEE; a shelf/rack scope
    requires SEE on that container. A ``saved_filter`` scope (Phase B7) loads the caller's own
    filter (404 on a missing/foreign one), resolves it to work ids clamped to the caller's visible
    set, and exports those.
    """
    scope_id = payload.scope_id or payload.target_id
    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    saved_filter_work_ids: list[uuid.UUID] | None = None
    if payload.scope_type == "saved_filter":
        if not scope_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="scope_id is required"
            )
        try:
            filter_id = uuid.UUID(scope_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found"
            ) from exc
        saved = get_owned_saved_filter(db, actor, filter_id)
        if saved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
        saved_filter_work_ids = resolve_saved_filter_work_ids(db, actor, saved)
    try:
        content = export_bibliography(
            db,
            scope_type=payload.scope_type,
            scope_id=scope_id,
            work_ids=payload.work_ids,
            saved_filter_work_ids=saved_filter_work_ids,
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
