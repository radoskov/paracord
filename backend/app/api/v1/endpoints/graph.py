"""Citation graph endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.citation_graph import build_citation_graph

router = APIRouter()
DB_DEP = Depends(get_db)


class GraphScope(BaseModel):
    type: Literal["library", "shelf", "rack"]
    id: uuid.UUID | None = None


class CitationGraphRequest(BaseModel):
    scope: GraphScope
    node_mode: Literal["local_only", "include_external"] = "local_only"


class GraphNodeRead(BaseModel):
    id: str
    label: str
    type: str
    work_id: uuid.UUID | None = None
    year: int | None = None
    doi: str | None = None


class GraphEdgeRead(BaseModel):
    source: str
    target: str
    weight: int
    resolution: str


class CitationGraphResponse(BaseModel):
    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]
    summary: dict


@router.post("/citation", response_model=CitationGraphResponse)
def citation_graph(payload: CitationGraphRequest, db: Session = DB_DEP) -> CitationGraphResponse:
    """Build a scoped citation graph (nodes = works, edges = bibliography citations)."""
    try:
        graph = build_citation_graph(
            db,
            scope_type=payload.scope.type,
            scope_id=payload.scope.id,
            node_mode=payload.node_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CitationGraphResponse(
        nodes=[GraphNodeRead(**vars(node)) for node in graph.nodes],
        edges=[GraphEdgeRead(**vars(edge)) for edge in graph.edges],
        summary=graph.summary,
    )
