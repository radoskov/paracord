"""Visualization endpoints (D38, Track C P2).

``GET /viz/{view_type}`` resolves a scope + axis/encoding params into a normalized ``VizPayload``
(see :mod:`app.services.visualization`). Auth and visibility mirror the citation-graph endpoints:
the actor must SEE the scope container, and every scope is clamped to the actor's visible works, so
a hidden paper never surfaces as a node or edge.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.services import access, visualization
from app.services.saved_filters import (
    get_owned_saved_filter,
    resolve_saved_filter_work_ids,
)
from app.services.visualization import VizScope, available_view_types, get_viz

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)

_SCOPE_TYPES = (
    "library",
    "shelf",
    "rack",
    "search_result",
    "selected_papers",
    "import_batch",
    "saved_filter",
)


class VizAxisModel(BaseModel):
    key: str
    label: str


class VizNodeModel(BaseModel):
    id: str
    x: float | None = None
    y: float | None = None
    size: float | None = None
    color_group: str | None = None
    shape: str
    label: str
    meta: dict


class VizEdgeModel(BaseModel):
    source: str
    target: str
    weight: float


class VizPayloadResponse(BaseModel):
    view_type: str
    nodes: list[VizNodeModel]
    axes: dict[str, VizAxisModel] | None = None
    edges: list[VizEdgeModel] | None = None
    legend: dict | None = None
    notes: list[str] = []
    axis_options: list[VizAxisModel] | None = None
    # P5a: stacked time-series (topic river) / labelled matrix (similarity heatmap). See
    # app.services.visualization.VizPayload for the shapes.
    series: dict | None = None
    matrix: dict | None = None
    # B2: {reindexable: int, needs_text:[{work_id, title}]} — some papers aren't indexed for the
    # model; splits "reindex to include" from "attach a PDF & extract" + lists the file-less papers.
    reindex_hint: dict | None = None


class VizViewTypesResponse(BaseModel):
    view_types: list[str]


@router.get("/", response_model=VizViewTypesResponse)
def list_view_types(actor: User = AUTH_DEP) -> VizViewTypesResponse:
    """List the registered visualization view types (for the view-type selector)."""
    return VizViewTypesResponse(view_types=available_view_types())


@router.get("/{view_type}", response_model=VizPayloadResponse)
def get_visualization(
    view_type: str,
    scope_type: str = Query("library"),
    scope_id: uuid.UUID | None = Query(None),
    work_ids: list[uuid.UUID] | None = Query(None),
    x_axis: str | None = Query(None),
    y_axis: str | None = Query(None),
    size_by: str | None = Query(None),
    color_by: str | None = Query(None),
    edge_context: str | None = Query(None),
    focus_work_id: uuid.UUID | None = Query(None),
    include_edges: bool = Query(False),
    embedding_model: str | None = Query(None),
    layout: str | None = Query(None),
    current_year: int | None = Query(None),
    max_nodes: int | None = Query(None, ge=1, le=visualization.MAX_NODES),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> VizPayloadResponse:
    """Build a visualization payload for ``view_type`` over the given scope.

    Access control: a shelf/rack scope requires SEE on that container (404 otherwise), and the
    payload's nodes/edges are clamped to the caller's visible works.
    """
    if scope_type not in _SCOPE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported scope: {scope_type}"
        )
    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")

    resolved_work_ids = work_ids
    if scope_type == "saved_filter":
        if scope_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="scope id is required"
            )
        saved = get_owned_saved_filter(db, actor, scope_id)
        if saved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
        resolved_work_ids = resolve_saved_filter_work_ids(db, actor, saved)

    scope = VizScope(type=scope_type, id=scope_id, work_ids=resolved_work_ids)
    params = {
        "x_axis": x_axis,
        "y_axis": y_axis,
        "size_by": size_by,
        "color_by": color_by,
        "edge_context": edge_context,
        "focus_work_id": focus_work_id,
        "include_edges": include_edges,
        "embedding_model": embedding_model,
        "layout": layout,
        "current_year": current_year,
        "max_nodes": max_nodes,
    }
    try:
        payload = get_viz(db, actor, view_type, scope, params)
    except ValueError as exc:
        # Unknown view type or unknown axis -> a 404 for the former, 400 for the latter.
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if detail.startswith("Unknown visualization view type")
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from exc

    return VizPayloadResponse(
        view_type=payload.view_type,
        nodes=[VizNodeModel(**vars(node)) for node in payload.nodes],
        axes=(
            {axis: VizAxisModel(**spec) for axis, spec in payload.axes.items()}
            if payload.axes
            else None
        ),
        edges=(
            [VizEdgeModel(**vars(edge)) for edge in payload.edges]
            if payload.edges is not None
            else None
        ),
        legend=payload.legend,
        notes=payload.notes,
        axis_options=(
            [VizAxisModel(**opt) for opt in payload.axis_options]
            if payload.axis_options is not None
            else None
        ),
        series=payload.series,
        matrix=payload.matrix,
        reindex_hint=payload.reindex_hint,
    )
