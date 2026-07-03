"""Visualization data providers (D38, Track C P2).

An extensible **provider registry** turning a scope into a normalized :class:`VizPayload` that any
frontend renderer can consume. A view type is one registered provider; adding a view later
(P3-P5: embedding-cluster, co-citation, topic-river, heatmap) is a single :func:`register_viz`
call, not a plumbing change.

``VizPayload`` is the contract shared by every provider::

    {view_type, nodes:[{id, x, y, size, color_group, shape, label, meta}],
     edges:[{source, target, weight}]?, axes:{x:{key,label}, y:{key,label}}?,
     legend?, notes, axis_options?}

Access control is delegated to the existing machinery: the caller passes ``actor`` and every scope
is clamped to ``access.visible_work_ids`` (a reader never sees a hidden paper), and scope resolution
reuses the citation graph's ``_scope_works`` so no visibility rule is re-implemented here.

P2 ships one provider, ``temporal_map`` (the Litmaps-style scatter): both axes are independently
selectable from a shared option set, with size/color/shape encodings and an optional citation-edge
overlay. Heavy metrics are computed per request — fine at this scale; :data:`_METRIC_CACHE_NOTE`
marks where a scope-keyed cache would go for P3+.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.citation_graph import _scope_works, build_citation_graph
from app.services.topic_modeling import (
    DEFAULT_MAX_TOPICS,
    _centroid,
    _cluster_keywords,
    _doc_text,
    _kmeans,
    _ordered_works,
    _paper_dense_vectors,
    _tfidf,
    _tokenize,
)

# Node cap (mirrors the citation/topic graph's ``MAX_NODES=400`` concept). Bounds the per-request
# compute so a huge scope can't stall the endpoint; the truncation is reported in ``notes``.
MAX_NODES = 500

# Where a P3+ optimization would live: local-degree and similarity metrics are recomputed per
# request today (cheap for a personal library). A scope-keyed cache (scope, embedding-version)
# belongs here once embedding-cluster / summaries land on the same computed layer.
_METRIC_CACHE_NOTE = "metrics computed per request; add a scope-keyed cache in P3+"

# P3 embedding-cluster layout cache (the scope-keyed cache _METRIC_CACHE_NOTE flagged). Keyed by
# ``(scope signature, model)`` — the scope signature is the sorted set of placed work ids, so a
# changed vector set (a paper (re)indexed for the model) yields a new key. The stored ``vector_hash``
# guards against a same-scope/same-model vector change (values updated in place): a hash mismatch
# recomputes and overwrites, so the cache self-invalidates instead of serving a stale layout. An
# in-process dict is enough for a mostly single-user / few-LAN-user deployment.
_LAYOUT_CACHE: dict[tuple[tuple[str, ...], str | None], tuple[str, np.ndarray, list[int]]] = {}

# Shared axis option set (§2a). Both the X and Y dropdown draw from this; P3+ registers more by
# adding an entry plus a branch in ``_axis_values``.
AXIS_LABELS: dict[str, str] = {
    "year": "Publication year",
    "citation_count": "Citation count",
    "local_degree": "Local citation degree",
    "citation_velocity": "Citation velocity",
    "similarity_to_focus": "Similarity to focus",
    "topic_similarity_to_focus": "Topic similarity to focus",
}

DEFAULT_X_AXIS = "year"
DEFAULT_Y_AXIS = "local_degree"
DEFAULT_SIZE_BY = "local_degree"
DEFAULT_COLOR_BY = "status"


@dataclass
class VizScope:
    """A visualization scope, mirroring the citation-graph scope family."""

    type: str
    id: uuid.UUID | None = None
    work_ids: list[uuid.UUID] | None = None


@dataclass
class VizNode:
    id: str
    x: float | None
    y: float | None
    size: float | None
    color_group: str | None
    shape: str
    label: str
    meta: dict


@dataclass
class VizEdge:
    source: str
    target: str
    weight: float


@dataclass
class VizPayload:
    view_type: str
    nodes: list[VizNode] = field(default_factory=list)
    axes: dict[str, dict[str, str]] | None = None
    edges: list[VizEdge] | None = None
    legend: dict | None = None
    notes: list[str] = field(default_factory=list)
    # Available axis options for both dropdowns; ``None`` for non-axis views. Server-driven so P3+
    # can add an axis without a frontend change.
    axis_options: list[dict[str, str]] | None = None


VizProvider = Callable[[Session, User, VizScope, dict], VizPayload]

_PROVIDERS: dict[str, VizProvider] = {}


def register_viz(view_type: str) -> Callable[[VizProvider], VizProvider]:
    """Register a provider for ``view_type``. Adding a view is this one decorator, no plumbing."""

    def _decorator(fn: VizProvider) -> VizProvider:
        _PROVIDERS[view_type] = fn
        return fn

    return _decorator


def available_view_types() -> list[str]:
    """View types with a registered provider (for the frontend view-type selector)."""
    return sorted(_PROVIDERS)


def get_viz(db: Session, actor: User, view_type: str, scope: VizScope, params: dict) -> VizPayload:
    """Dispatch to the provider registered for ``view_type``.

    Raises ``ValueError`` for an unknown view type (the endpoint maps it to 404).
    """
    provider = _PROVIDERS.get(view_type)
    if provider is None:
        raise ValueError(f"Unknown visualization view type: {view_type}")
    return provider(db, actor, scope, params)


@dataclass
class _AxisContext:
    """Cross-work state the axis functions share (degree map, focus, current year, embeddings)."""

    current_year: int
    degree: dict[str, int]
    visible: set[uuid.UUID] | None
    focus_work_id: uuid.UUID | None
    focus_work: Work | None
    embedding_model: str | None


def _sparse_cosine(a: dict[int, float], b: dict[int, float]) -> float:
    """Cosine similarity of two index-keyed sparse vectors (0.0 if either is empty)."""
    if not a or not b:
        return 0.0
    keys = a.keys() & b.keys()
    dot = sum(a[i] * b[i] for i in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _similarity_axis(
    db: Session, works: list[Work], ctx: _AxisContext
) -> tuple[dict[uuid.UUID, float | None], str | None]:
    if ctx.focus_work_id is None:
        return ({w.id: None for w in works}, "similarity_to_focus unavailable: no focus paper set.")
    focus = ctx.focus_work
    if focus is None:
        return (
            {w.id: None for w in works},
            "similarity_to_focus unavailable: focus paper not found or not visible.",
        )
    target_works = list(works)
    if all(w.id != focus.id for w in works):
        target_works = [focus, *works]
    vectors, kept, _model, _unindexed = _paper_dense_vectors(db, target_works, ctx.embedding_model)
    if vectors is None:
        return (
            {w.id: None for w in works},
            "similarity_to_focus unavailable: no real embedding model active.",
        )
    vec_by_id: dict[uuid.UUID, dict[int, float]] = {kw.id: vectors[i] for i, kw in enumerate(kept)}
    focus_vec = vec_by_id.get(focus.id)
    if focus_vec is None:
        return (
            {w.id: None for w in works},
            "similarity_to_focus unavailable: focus paper is not indexed for this model.",
        )
    values: dict[uuid.UUID, float | None] = {}
    for work in works:
        wv = vec_by_id.get(work.id)
        values[work.id] = round(_sparse_cosine(focus_vec, wv), 4) if wv is not None else None
    return (values, None)


def _topic_similarity_axis(
    works: list[Work], ctx: _AxisContext
) -> tuple[dict[uuid.UUID, float | None], str | None]:
    if ctx.focus_work_id is None:
        return (
            {w.id: None for w in works},
            "topic_similarity_to_focus unavailable: no focus paper set.",
        )
    focus = ctx.focus_work
    focus_topics = {str(t).casefold() for t in (focus.topics or [])} if focus else set()
    if not focus_topics:
        return (
            {w.id: None for w in works},
            "topic_similarity_to_focus unavailable: focus paper has no topic terms.",
        )
    values: dict[uuid.UUID, float | None] = {}
    for work in works:
        terms = {str(t).casefold() for t in (work.topics or [])}
        if not terms:
            values[work.id] = None
            continue
        union = focus_topics | terms
        values[work.id] = round(len(focus_topics & terms) / len(union), 4) if union else None
    return (values, None)


def _axis_values(
    db: Session, key: str, works: list[Work], ctx: _AxisContext
) -> tuple[dict[uuid.UUID, float | None], str | None]:
    """Compute one axis's value per work (``None`` = unavailable for that work), plus an optional
    axis-wide note (e.g. why a similarity axis is unavailable)."""
    if key == "year":
        return ({w.id: float(w.year) if w.year is not None else None for w in works}, None)
    if key == "citation_count":
        return (
            {
                w.id: float(w.citation_count) if w.citation_count is not None else None
                for w in works
            },
            None,
        )
    if key == "local_degree":
        return ({w.id: float(ctx.degree.get(str(w.id), 0)) for w in works}, None)
    if key == "citation_velocity":
        values: dict[uuid.UUID, float | None] = {}
        for work in works:
            if work.citation_count is None or work.year is None:
                values[work.id] = None
            else:
                values[work.id] = round(
                    work.citation_count / max(1, ctx.current_year - work.year), 4
                )
        return (values, None)
    if key == "similarity_to_focus":
        return _similarity_axis(db, works, ctx)
    if key == "topic_similarity_to_focus":
        return _topic_similarity_axis(works, ctx)
    raise ValueError(f"Unknown axis: {key}")


def _size_value(work: Work, size_by: str, degree: dict[str, int]) -> float | None:
    if size_by == "none":
        return None
    if size_by == "citation_count":
        return float(work.citation_count) if work.citation_count is not None else None
    return float(degree.get(str(work.id), 0))  # local_degree default


def _color_group(work: Work, color_by: str) -> str | None:
    if color_by == "none":
        return None
    if color_by == "work_type":
        return work.work_type or "unknown"
    return work.reading_status or "unread"  # status default


@register_viz("temporal_map")
def temporal_map(db: Session, actor: User, scope: VizScope, params: dict) -> VizPayload:
    """The Litmaps-style temporal citation map: one point per in-library paper.

    Both axes are independently selected from :data:`AXIS_LABELS` via ``params['x_axis']`` /
    ``params['y_axis']``. Encodings: ``size`` (local degree or citation count), ``color_group``
    (reading status or work type), ``shape`` (reserved — all temporal-map nodes are in-library
    works). An optional citation-edge overlay (``params['include_edges']``) reuses the citation
    graph's resolved local edges among the scope papers. Local degree and the edges both come from
    ``build_citation_graph`` — resolution is never re-implemented here.
    """
    x_axis = params.get("x_axis") or DEFAULT_X_AXIS
    y_axis = params.get("y_axis") or DEFAULT_Y_AXIS
    for axis in (x_axis, y_axis):
        if axis not in AXIS_LABELS:
            raise ValueError(f"Unknown axis: {axis}")
    size_by = params.get("size_by") or DEFAULT_SIZE_BY
    color_by = params.get("color_by") or DEFAULT_COLOR_BY
    include_edges = bool(params.get("include_edges"))
    cap = int(params.get("max_nodes") or MAX_NODES)

    visible = access.visible_work_ids(db, actor)
    scope_works = _scope_works(
        db,
        scope_type=scope.type,
        scope_id=scope.id,
        work_ids=scope.work_ids,
        visible_ids=visible,
    )
    works = _ordered_works(list(scope_works.values()))
    total = len(works)
    notes: list[str] = []
    if total > cap:
        works = works[:cap]
        notes.append(
            f"Showing {cap} of {total} papers (node cap {cap}); refine the scope to see the rest."
        )

    axes = {
        "x": {"key": x_axis, "label": AXIS_LABELS[x_axis]},
        "y": {"key": y_axis, "label": AXIS_LABELS[y_axis]},
    }
    axis_options = [{"key": k, "label": v} for k, v in AXIS_LABELS.items()]

    if not works:
        notes.append("No papers in this scope.")
        return VizPayload(
            view_type="temporal_map",
            nodes=[],
            axes=axes,
            edges=[] if include_edges else None,
            notes=notes,
            axis_options=axis_options,
        )

    work_ids = [w.id for w in works]
    # Reuse the citation graph (over exactly the capped node set) for resolved local edges and the
    # per-work local citation degree (distinct in-library citing papers = number of incoming edges).
    graph = build_citation_graph(
        db,
        scope_type="selected_papers",
        work_ids=work_ids,
        node_mode="local_only",
        visible_ids=visible,
    )
    degree: dict[str, int] = defaultdict(int)
    for edge in graph.edges:
        degree[edge.target] += 1

    focus_work_id = params.get("focus_work_id")
    focus_work: Work | None = None
    if focus_work_id is not None:
        candidate = db.get(Work, focus_work_id)
        if candidate is not None and (visible is None or candidate.id in visible):
            focus_work = candidate
    ctx = _AxisContext(
        current_year=int(params.get("current_year") or datetime.now(UTC).year),
        degree=degree,
        visible=visible,
        focus_work_id=focus_work_id,
        focus_work=focus_work,
        embedding_model=params.get("embedding_model"),
    )

    x_values, x_note = _axis_values(db, x_axis, works, ctx)
    y_values, y_note = _axis_values(db, y_axis, works, ctx)
    for note in (x_note, y_note):
        if note:
            notes.append(note)

    nodes: list[VizNode] = []
    for work in works:
        node_id = str(work.id)
        local_degree = degree.get(node_id, 0)
        nodes.append(
            VizNode(
                id=node_id,
                x=x_values.get(work.id),
                y=y_values.get(work.id),
                size=_size_value(work, size_by, degree),
                color_group=_color_group(work, color_by),
                shape="in_library",
                label=work.canonical_title or f"Untitled work ({node_id[:8]})",
                meta={
                    "title": work.canonical_title,
                    "year": work.year,
                    "citation_count": work.citation_count,
                    "local_degree": local_degree,
                    "reading_status": work.reading_status,
                    "work_type": work.work_type,
                    "doi": work.doi,
                },
            )
        )

    legend: dict | None = None
    if color_by != "none":
        groups = sorted({n.color_group for n in nodes if n.color_group is not None})
        legend = {"color_by": color_by, "groups": groups}

    edges: list[VizEdge] | None = None
    if include_edges:
        edges = [
            VizEdge(source=e.source, target=e.target, weight=float(e.weight)) for e in graph.edges
        ]

    return VizPayload(
        view_type="temporal_map",
        nodes=nodes,
        axes=axes,
        edges=edges,
        legend=legend,
        notes=notes,
        axis_options=axis_options,
    )


# Embedding-cluster fixed axes: the two PCA components. Unlike temporal_map these are not swappable,
# so the view exposes no ``axis_options``.
EMBEDDING_AXES: dict[str, dict[str, str]] = {
    "x": {"key": "component_1", "label": "Component 1"},
    "y": {"key": "component_2", "label": "Component 2"},
}


def _pca_2d(matrix: np.ndarray) -> np.ndarray:
    """Project ``matrix`` (n×d) onto its top-2 principal components (mean-centered, via SVD).

    Deterministic: each component's sign is fixed by making its largest-magnitude loading positive
    (the sklearn ``svd_flip`` convention), so the same input always yields the same coordinates
    across runs and platforms. Pads to two columns when the data spans fewer than two components.
    """
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    if vt.shape[0] == 0:
        return np.zeros((matrix.shape[0], 2))
    max_abs = np.argmax(np.abs(vt), axis=1)
    signs = np.sign(vt[np.arange(vt.shape[0]), max_abs])
    signs[signs == 0] = 1.0
    vt = vt * signs[:, np.newaxis]
    coords = centered @ vt[:2].T
    if coords.shape[1] < 2:
        coords = np.hstack([coords, np.zeros((coords.shape[0], 2 - coords.shape[1]))])
    return coords


def _sample_works(works: list[Work], cap: int) -> list[Work]:
    """Deterministically sample ``cap`` works evenly across the (title-ordered) input."""
    n = len(works)
    if n <= cap:
        return list(works)
    return [works[(i * n) // cap] for i in range(cap)]


def _scope_dense_matrix(
    db: Session, works: list[Work], embedding_model: str | None
) -> tuple[np.ndarray | None, list[Work], str | None, str | None]:
    """Return ``(matrix, kept_works, model_label, note)`` — one dense row per placed paper.

    Prefers stored dense vectors via :func:`_paper_dense_vectors` (the topic / related-works
    embedding path): a real model's vectors are reused, never re-embedded on the read path, and
    un-indexed papers are skipped (D19) and surfaced in ``note``. When only the hash-BOW baseline is
    active it falls back to embedding each paper's text with the resolved baseline provider — those
    vectors are dense and usable for PCA — so the map still renders, with an honest note. Returns
    ``(None, [], label, note)`` when there is nothing to place.
    """
    vectors, kept, label, skipped = _paper_dense_vectors(db, works, embedding_model)
    if vectors is not None:
        matrix = np.array([[vec[i] for i in range(len(vec))] for vec in vectors], dtype=float)
        note = (
            f"{skipped} papers not indexed for this model — reindex to include them."
            if skipped
            else None
        )
        return (matrix if kept else None), kept, label, note

    from app.services.embeddings import resolve_embedding_provider

    provider = resolve_embedding_provider(db=db).provider
    rows: list[list[float]] = []
    kept_works: list[Work] = []
    for work in works:
        text = _doc_text(work)
        if not text.strip():
            continue
        rows.append([float(x) for x in provider.embed(text)])
        kept_works.append(work)
    omitted = len(works) - len(kept_works)
    if not kept_works:
        return None, [], provider.model_name, "No papers with text to place in this scope."
    note = (
        "Cluster layout uses the built-in baseline embedder; enable a real embedding model and "
        "reindex for sharper clusters."
    )
    if omitted:
        note = f"{note} {omitted} papers have no text and were omitted."
    return np.array(rows, dtype=float), kept_works, provider.model_name, note


@dataclass
class _EmbeddingLayout:
    coords: np.ndarray | None
    kept: list[Work]
    assignments: list[int]
    model_label: str | None
    note: str | None

    @property
    def empty(self) -> bool:
        return self.coords is None


def _embedding_layout(
    db: Session, works: list[Work], embedding_model: str | None
) -> _EmbeddingLayout:
    """Source dense vectors, then PCA-project + cluster them, reusing the cache when possible."""
    matrix, kept, model_label, note = _scope_dense_matrix(db, works, embedding_model)
    if matrix is None:
        return _EmbeddingLayout(None, [], [], model_label, note)

    scope_sig = tuple(sorted(str(w.id) for w in kept))
    vector_hash = hashlib.md5(  # noqa: S324 - cache fingerprint, not a security control.
        np.ascontiguousarray(matrix, dtype=float).tobytes()
    ).hexdigest()
    cache_key = (scope_sig, model_label)
    cached = _LAYOUT_CACHE.get(cache_key)
    if cached is not None and cached[0] == vector_hash:
        return _EmbeddingLayout(cached[1], kept, cached[2], model_label, note)

    coords = _pca_2d(matrix)
    k = max(1, min(DEFAULT_MAX_TOPICS, len(kept)))
    dense_dicts = [{i: float(v) for i, v in enumerate(row)} for row in matrix]
    assignments = _kmeans(dense_dicts, k)
    _LAYOUT_CACHE[cache_key] = (vector_hash, coords, assignments)
    return _EmbeddingLayout(coords, kept, assignments, model_label, note)


def _cluster_labels(kept: list[Work], assignments: list[int]) -> dict[int, str]:
    """Human-readable label per cluster id from its top TF-IDF terms (reuses the topic labeller)."""
    tfidf_vectors = _tfidf([_tokenize(_doc_text(w)) for w in kept])
    labels: dict[int, str] = {}
    for cid in sorted(set(assignments)):
        members = [i for i, c in enumerate(assignments) if c == cid]
        centroid = _centroid([tfidf_vectors[i] for i in members])
        keywords = _cluster_keywords(centroid)[:2]
        labels[cid] = f"{cid + 1}. {', '.join(keywords)}" if keywords else f"Cluster {cid + 1}"
    return labels


@register_viz("embedding_cluster")
def embedding_cluster(db: Session, actor: User, scope: VizScope, params: dict) -> VizPayload:
    """Embedding-cluster map (§2b): place the SEE-filtered scope papers in 2D by embedding proximity.

    The layout is server-side **PCA-2D** (numpy, no new dependency, deterministic) over each paper's
    stored dense vector — reused from the topic / related-works embedding path, never re-embedded on
    the read path for a real model (un-indexed papers are skipped and reported, per D19). Points are
    colored by an embedding k-means ``cluster`` labelled with its top TF-IDF terms (reusing the topic
    modeller's clustering + labeller). ``size`` reuses the shared metric helper (local degree by
    default). The computed layout is cached per ``(scope, model, vector fingerprint)`` so repeat
    views don't recompute. The two axes are the fixed PCA components — there is no swappable axis set,
    so ``axis_options`` is omitted.
    """
    cap = int(params.get("max_nodes") or MAX_NODES)
    size_by = params.get("size_by") or DEFAULT_SIZE_BY
    embedding_model = params.get("embedding_model")

    visible = access.visible_work_ids(db, actor)
    scope_works = _scope_works(
        db,
        scope_type=scope.type,
        scope_id=scope.id,
        work_ids=scope.work_ids,
        visible_ids=visible,
    )
    works = _ordered_works(list(scope_works.values()))
    total = len(works)
    notes: list[str] = []

    if total > cap:
        works = _sample_works(works, cap)
        notes.append(
            f"Sampled {cap} of {total} papers (node cap {cap}); refine the scope to see all."
        )

    if not works:
        notes.append("No papers in this scope.")
        return VizPayload(view_type="embedding_cluster", nodes=[], axes=EMBEDDING_AXES, notes=notes)

    layout = _embedding_layout(db, works, embedding_model)
    if layout.note:
        notes.append(layout.note)
    if layout.empty:
        return VizPayload(view_type="embedding_cluster", nodes=[], axes=EMBEDDING_AXES, notes=notes)

    kept = layout.kept
    coords = layout.coords
    assignments = layout.assignments
    labels = _cluster_labels(kept, assignments)

    graph = build_citation_graph(
        db,
        scope_type="selected_papers",
        work_ids=[w.id for w in kept],
        node_mode="local_only",
        visible_ids=visible,
    )
    degree: dict[str, int] = defaultdict(int)
    for edge in graph.edges:
        degree[edge.target] += 1

    nodes: list[VizNode] = []
    for i, work in enumerate(kept):
        node_id = str(work.id)
        cluster_label = labels[assignments[i]]
        nodes.append(
            VizNode(
                id=node_id,
                x=round(float(coords[i][0]), 6),
                y=round(float(coords[i][1]), 6),
                size=_size_value(work, size_by, degree),
                color_group=cluster_label,
                shape="in_library",
                label=work.canonical_title or f"Untitled work ({node_id[:8]})",
                meta={
                    "title": work.canonical_title,
                    "year": work.year,
                    "cluster": cluster_label,
                    "local_degree": degree.get(node_id, 0),
                    "citation_count": work.citation_count,
                    "doi": work.doi,
                },
            )
        )

    legend = {
        "color_by": "cluster",
        "groups": sorted({n.color_group for n in nodes if n.color_group is not None}),
    }
    return VizPayload(
        view_type="embedding_cluster",
        nodes=nodes,
        axes=EMBEDDING_AXES,
        legend=legend,
        notes=notes,
        axis_options=None,
    )


__all__ = [
    "MAX_NODES",
    "AXIS_LABELS",
    "EMBEDDING_AXES",
    "VizScope",
    "VizNode",
    "VizEdge",
    "VizPayload",
    "register_viz",
    "available_view_types",
    "get_viz",
    "temporal_map",
    "embedding_cluster",
]
