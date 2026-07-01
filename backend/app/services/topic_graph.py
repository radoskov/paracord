"""Embedding-similarity ("topic") graph: papers linked by how close they are in meaning (#6).

Nodes are papers; an edge connects two papers whose paper-level embedding vectors are similar, with
weight = cosine similarity (i.e. inverted semantic distance). Edges are kNN-sparsified (each paper
keeps its few most-similar neighbours above a threshold) so the graph is readable rather than a
dense clique. Reuses the dense paper vectors from ``topic_modeling`` (mean-pooled stored chunk
vectors when available, else freshly embedded text) so a real embedding model / multimode drives it.

Access control: only papers the caller may SEE become nodes (the scope + ``visible_ids`` filter).
Requires a real embedding model; with only the hash-BOW baseline it returns an empty graph plus a
note (dense semantics aren't available), rather than a misleading lexical graph.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work import Work
from app.services.topic_modeling import _cosine, _ordered_works, _paper_dense_vectors, _scope_works

# Cap the node set: the pairwise similarity is O(n^2 · dim); a personal library is small, but bound
# it so a huge scope can't stall the request. Dropped papers are reported in the summary.
MAX_NODES = 400
DEFAULT_K = 6
DEFAULT_MIN_SIMILARITY = 0.30


@dataclass
class TopicGraphNode:
    id: str
    label: str
    work_id: uuid.UUID
    year: int | None = None


@dataclass
class TopicGraphEdge:
    source: str
    target: str
    weight: float  # cosine similarity (inverted semantic distance)


@dataclass
class TopicGraph:
    nodes: list[TopicGraphNode] = field(default_factory=list)
    edges: list[TopicGraphEdge] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _resolve_works(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    work_ids: list[uuid.UUID] | None,
    visible_ids: set[uuid.UUID] | None,
) -> list[Work]:
    if work_ids is not None:
        works = list(db.scalars(select(Work).where(Work.id.in_(work_ids))))
    else:
        works = _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    if visible_ids is not None:
        works = [w for w in works if w.id in visible_ids]
    return _ordered_works(works)


def build_topic_graph(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    work_ids: list[uuid.UUID] | None = None,
    embedding_model: str | None = None,
    visible_ids: set[uuid.UUID] | None = None,
    k: int = DEFAULT_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> TopicGraph:
    """Build a kNN embedding-similarity graph over a scope's papers."""
    works = _resolve_works(
        db, scope_type=scope_type, scope_id=scope_id, work_ids=work_ids, visible_ids=visible_ids
    )
    dropped = max(0, len(works) - MAX_NODES)
    works = works[:MAX_NODES]
    if not works:
        return TopicGraph(summary={"node_count": 0, "edge_count": 0, "used_embeddings": False})

    vectors, model = _paper_dense_vectors(db, works, embedding_model)
    if vectors is None:
        # Only hash-BOW available → no real semantic space; be honest instead of lexical-in-disguise.
        return TopicGraph(
            nodes=[
                TopicGraphNode(
                    id=str(w.id), label=w.canonical_title or "(untitled)", work_id=w.id, year=w.year
                )
                for w in works
            ],
            summary={
                "node_count": len(works),
                "edge_count": 0,
                "used_embeddings": False,
                "dropped_nodes": dropped,
                "note": "No real embedding model active — enable one to see topic similarity edges.",
            },
        )

    nodes = [
        TopicGraphNode(
            id=str(w.id), label=w.canonical_title or "(untitled)", work_id=w.id, year=w.year
        )
        for w in works
    ]
    # kNN edges: for each paper keep its top-k most-similar neighbours above the threshold; collect
    # undirected edges (dedup by an ordered id pair, keeping the max similarity seen).
    n = len(works)
    edge_weight: dict[tuple[str, str], float] = {}
    for i in range(n):
        sims = [(j, _cosine(vectors[i], vectors[j])) for j in range(n) if j != i]
        sims.sort(key=lambda t: t[1], reverse=True)
        for j, sim in sims[:k]:
            if sim < min_similarity:
                break
            a, b = sorted((str(works[i].id), str(works[j].id)))
            key = (a, b)
            if sim > edge_weight.get(key, 0.0):
                edge_weight[key] = sim
    edges = [
        TopicGraphEdge(source=a, target=b, weight=round(w, 4)) for (a, b), w in edge_weight.items()
    ]
    return TopicGraph(
        nodes=nodes,
        edges=edges,
        summary={
            "node_count": len(nodes),
            "edge_count": len(edges),
            "used_embeddings": True,
            "embedding_model": model,
            "dropped_nodes": dropped,
        },
    )
