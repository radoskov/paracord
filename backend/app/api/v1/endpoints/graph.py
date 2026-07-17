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
from app.services.app_config import (
    effective_ai_scope_job_threshold,
    effective_citation_graph_node_cap,
    effective_topic_graph_node_cap,
)
from app.services.citation_graph import build_citation_graph
from app.services.scope_resolution import count_scope_works
from app.services.topic_graph import build_topic_graph
from app.workers.queue import enqueue_analysis_graph

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
    color_by: Literal["none", "shelf", "rack", "tag", "topic", "status", "year"] = "none"
    # Separate caps (2026-07-16) on external REFERENCE nodes (cited-but-not-in-library) and external
    # CITING nodes (papers that cite the scope), each distributed across the scope papers.
    max_external: int = Field(default=50, ge=0, le=500)
    max_external_citing: int = Field(default=50, ge=0, le=500)
    # Include the fetched incoming-citation data (papers that cite the scope) as citing nodes/edges.
    include_citing: bool = True


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
    citation_count: int | None = None
    color_group: str | None = None
    # ALL membership groups for shelf/rack/tag color-by (2+ → color-wheel node in the UI).
    color_groups: list[str] | None = None
    warning: bool = False


class GraphEdgeRead(BaseModel):
    source: str
    target: str
    weight: int
    resolution: str
    relation: str = "reference"  # "reference" | "citing" (2026-07-16)


class CitationGraphResponse(BaseModel):
    nodes: list[GraphNodeRead] = []
    edges: list[GraphEdgeRead] = []
    summary: dict = {}
    # L-a: the scope was above the background-job threshold — poll the job, then fetch
    # GET /jobs/{job_id}/result for this same payload shape.
    queued: bool = False
    job_id: str | None = None


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
    # L-a: a scope above the background-job threshold computes on the worker (same builder, same
    # cap); the client polls the job and fetches the stored result. Queue-down falls back inline.
    try:
        scope_size = count_scope_works(
            db,
            payload.scope.type,
            payload.scope.id,
            visible_ids=access.visible_work_condition(db, actor),
            work_ids=work_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope_size > effective_ai_scope_job_threshold(db):
        job_id = enqueue_analysis_graph(
            "citation",
            {
                "scope_type": payload.scope.type,
                "scope_id": str(payload.scope.id) if payload.scope.id else None,
                "work_ids": [str(w) for w in work_ids] if work_ids else None,
                "node_mode": payload.node_mode,
                "collapse_versions": payload.collapse_versions,
                "color_by": payload.color_by,
                "max_external": payload.max_external,
                "max_external_citing": payload.max_external_citing,
                "include_citing": payload.include_citing,
            },
            actor_user_id=str(actor.id),
        )
        if job_id is not None:
            return CitationGraphResponse(queued=True, job_id=job_id)
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
            actor=actor,
            max_external=payload.max_external,
            max_external_citing=payload.max_external_citing,
            include_citing=payload.include_citing,
            max_nodes=effective_citation_graph_node_cap(db),
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
    citation_count: int | None = None
    # ALL privacy-filtered membership names per kind ({shelf|rack|tag: [names]}) for client-side
    # membership coloring incl. color-wheel nodes.
    memberships: dict[str, list[str]] | None = None


class TopicGraphEdgeRead(BaseModel):
    source: str
    target: str
    weight: float


class TopicGraphResponse(BaseModel):
    nodes: list[TopicGraphNodeRead] = []
    edges: list[TopicGraphEdgeRead] = []
    summary: dict = {}
    queued: bool = False
    job_id: str | None = None


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
        scope_size = count_scope_works(
            db,
            payload.scope.type,
            payload.scope.id,
            visible_ids=access.visible_work_condition(db, actor),
            work_ids=work_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope_size > effective_ai_scope_job_threshold(db):
        job_id = enqueue_analysis_graph(
            "topic",
            {
                "scope_type": payload.scope.type,
                "scope_id": str(payload.scope.id) if payload.scope.id else None,
                "work_ids": [str(w) for w in work_ids] if work_ids else None,
            },
            actor_user_id=str(actor.id),
        )
        if job_id is not None:
            return TopicGraphResponse(queued=True, job_id=job_id)
    try:
        graph = build_topic_graph(
            db,
            scope_type=payload.scope.type,
            scope_id=payload.scope.id,
            work_ids=work_ids,
            embedding_model=payload.embedding_model,
            visible_ids=access.visible_work_ids(db, actor),
            actor=actor,
            k=max(1, min(payload.k, 20)),
            min_similarity=max(0.0, min(payload.min_similarity, 1.0)),
            max_nodes=effective_topic_graph_node_cap(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return TopicGraphResponse(
        nodes=[TopicGraphNodeRead(**vars(node)) for node in graph.nodes],
        edges=[TopicGraphEdgeRead(**vars(edge)) for edge in graph.edges],
        summary=graph.summary,
    )
