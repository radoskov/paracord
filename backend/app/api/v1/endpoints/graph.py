"""Citation graph endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.api.scope_params import resolve_scope_or_404
from app.db.session import get_db
from app.models.user import User
from app.services import access
from app.services.citation_graph import build_citation_graph
from app.services.topic_graph import build_topic_graph

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)


class GraphScope(BaseModel):
    # Kept in sync with citation_graph.ScopeType. For ``saved_filter`` (Phase B7) ``id`` is the
    # saved-filter id; the endpoint loads it (owned-by-actor 404), resolves + clamps it, and passes
    # the resulting work ids into build_citation_graph.
    type: Literal[
        "library",
        "shelf",
        "rack",
        "search_result",
        "selected_papers",
        "import_batch",
        "saved_filter",
    ]
    id: uuid.UUID | None = None
    # Explicit work set for ``search_result``/``selected_papers`` (the frontend runs the search and
    # passes the resulting ids). Clamped to the caller's visible set in build_citation_graph, so an
    # attacker's arbitrary ids only ever intersect what they may already see.
    work_ids: list[uuid.UUID] | None = None


class CitationGraphRequest(BaseModel):
    scope: GraphScope
    node_mode: Literal["local_only", "include_external"] = "local_only"
    collapse_versions: bool = False
    # §8.9 depth: categorical node coloring from existing library data (SEE-clamped; external nodes
    # are never colored). Node *sizing* (degree/pagerank/betweenness) is a pure client re-style — all
    # three centrality metrics ship on every node, so the frontend switches size without a refetch.
    color_by: Literal["none", "shelf", "tag", "topic", "status"] = "none"
    # Cap on external (cited-but-not-in-library) nodes; the most-cited ones are kept (item 1).
    max_external: int = Field(default=50, ge=0, le=500)


class GraphNodeRead(BaseModel):
    id: str
    label: str
    type: str
    work_id: uuid.UUID | None = None
    year: int | None = None
    doi: str | None = None
    # §8.9 depth encodings (see app.services.citation_graph.GraphNode).
    degree: int = 0
    pagerank: float = 0.0
    betweenness: float = 0.0
    color_group: str | None = None
    warning: bool = False


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
def citation_graph(
    payload: CitationGraphRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> CitationGraphResponse:
    """Build a scoped citation graph (nodes = works, edges = bibliography citations).

    Access control: hidden works never appear as nodes/edges, and a shelf/rack scope requires SEE
    on that container.
    """
    work_ids = resolve_scope_or_404(
        db,
        actor,
        scope_type=payload.scope.type,
        scope_id=payload.scope.id,
        work_ids=payload.scope.work_ids,
    )
    try:
        graph = build_citation_graph(
            db,
            scope_type=payload.scope.type,
            scope_id=payload.scope.id,
            work_ids=work_ids,
            node_mode=payload.node_mode,
            collapse_versions=payload.collapse_versions,
            compute_metrics=True,
            color_by=payload.color_by,
            visible_ids=access.visible_work_ids(db, actor),
            max_external=payload.max_external,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CitationGraphResponse(
        nodes=[GraphNodeRead(**vars(node)) for node in graph.nodes],
        edges=[GraphEdgeRead(**vars(edge)) for edge in graph.edges],
        summary=graph.summary,
    )


class TopicGraphRequest(BaseModel):
    scope: GraphScope
    embedding_model: str | None = None
    k: int = 6
    min_similarity: float = 0.30


class TopicGraphNodeRead(BaseModel):
    id: str
    label: str
    work_id: uuid.UUID
    year: int | None = None


class TopicGraphEdgeRead(BaseModel):
    source: str
    target: str
    weight: float


class TopicGraphResponse(BaseModel):
    nodes: list[TopicGraphNodeRead]
    edges: list[TopicGraphEdgeRead]
    summary: dict


@router.post("/topic", response_model=TopicGraphResponse)
def topic_graph(
    payload: TopicGraphRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> TopicGraphResponse:
    """Build a scoped embedding-similarity graph (nodes = papers, edges = semantic similarity, #6).

    Access control mirrors the citation graph: hidden papers never appear, and a shelf/rack scope
    requires SEE on that container. Edge weight is cosine similarity (inverted semantic distance);
    edges are kNN-sparsified."""
    work_ids = resolve_scope_or_404(
        db,
        actor,
        scope_type=payload.scope.type,
        scope_id=payload.scope.id,
        work_ids=payload.scope.work_ids,
    )
    try:
        graph = build_topic_graph(
            db,
            scope_type=payload.scope.type,
            scope_id=payload.scope.id,
            work_ids=work_ids,
            embedding_model=payload.embedding_model,
            visible_ids=access.visible_work_ids(db, actor),
            k=max(1, min(payload.k, 20)),
            min_similarity=max(0.0, min(payload.min_similarity, 1.0)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return TopicGraphResponse(
        nodes=[TopicGraphNodeRead(**vars(node)) for node in graph.nodes],
        edges=[TopicGraphEdgeRead(**vars(edge)) for edge in graph.edges],
        summary=graph.summary,
    )
