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

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work import Work
from app.services.topic_modeling import _ordered_works, _paper_dense_vectors, _scope_works

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
        works = list(
            db.scalars(select(Work).where(Work.id.in_(work_ids), Work.merged_into_id.is_(None)))
        )
    else:
        # ``_scope_works`` already drops merged shadows (Batch D) for every scope.
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
    all_works = _resolve_works(
        db, scope_type=scope_type, scope_id=scope_id, work_ids=work_ids, visible_ids=visible_ids
    )
    dropped = max(0, len(all_works) - MAX_NODES)
    all_works = all_works[:MAX_NODES]
    if not all_works:
        return TopicGraph(summary={"node_count": 0, "edge_count": 0, "used_embeddings": False})

    vectors, works, model, unindexed = _paper_dense_vectors(db, all_works, embedding_model)
    if vectors is None:
        # Only hash-BOW available → no real semantic space; be honest instead of lexical-in-disguise.
        return TopicGraph(
            nodes=[
                TopicGraphNode(
                    id=str(w.id), label=w.canonical_title or "(untitled)", work_id=w.id, year=w.year
                )
                for w in all_works
            ],
            summary={
                "node_count": len(all_works),
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
    edges = _knn_edges(works, vectors, k=k, min_similarity=min_similarity)
    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "used_embeddings": True,
        "embedding_model": model,
        "dropped_nodes": dropped,
        # D19: papers with no pre-indexed chunk vectors for this model are skipped, not embedded
        # inline on the read path; report the count so the UI can prompt a reindex.
        "unindexed_works": unindexed,
    }
    if unindexed:
        summary["note"] = (
            f"{unindexed} papers not indexed for this model — reindex to include them."
        )
    return TopicGraph(nodes=nodes, edges=edges, summary=summary)


def _knn_edges(
    works: list[Work],
    vectors: list[dict[int, float]],
    *,
    k: int,
    min_similarity: float,
) -> list[TopicGraphEdge]:
    """kNN cosine-similarity edges via a single numpy matrix op (D20).

    Normalize the paper vectors once, take the full cosine matrix ``M @ M.T``, then for each paper
    keep its top-``k`` neighbours above ``min_similarity``. Undirected edges are deduped by an
    ordered id pair, keeping the max similarity — same output as the prior O(n²) pure-Python loop.
    """
    n = len(works)
    if n < 2:
        return []
    dim = len(vectors[0])
    matrix = np.array([[vec.get(i, 0.0) for i in range(dim)] for vec in vectors], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0  # a zero vector stays zero → cosine 0, matching _cosine's guard
    normalized = matrix / norms
    sims = normalized @ normalized.T
    np.fill_diagonal(sims, -np.inf)  # exclude self; sorts last so it never enters the top-k

    edge_weight: dict[tuple[str, str], float] = {}
    for i in range(n):
        # Stable sort so ties break by ascending neighbour index, exactly like the prior loop.
        order = np.argsort(-sims[i], kind="stable")[:k]
        for j in order:
            sim = float(sims[i, j])
            if sim < min_similarity:
                break
            a, b = sorted((str(works[i].id), str(works[int(j)].id)))
            key = (a, b)
            if sim > edge_weight.get(key, 0.0):
                edge_weight[key] = sim
    return [
        TopicGraphEdge(source=a, target=b, weight=round(w, 4)) for (a, b), w in edge_weight.items()
    ]
