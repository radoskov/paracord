"""Visualization data providers (D38, Track C P2).

An extensible **provider registry** turning a scope into a normalized :class:`VizPayload` that any
frontend renderer can consume. A view type is one registered provider; adding a view later
(P3-P5: embedding-cluster, co-citation, topic-river, heatmap) is a single :func:`register_viz`
call, not a plumbing change.

``VizPayload`` is the contract shared by every provider::

    {view_type, nodes:[{id, x, y, size, color_group, shape, label, meta}],
     edges:[{source, target, weight}]?, axes:{x:{key,label}, y:{key,label}}?,
     legend?, notes, axis_options?, series?, matrix?}

``series`` and ``matrix`` (P5a) are the typed carriers for non-scatter chart views (topic river /
similarity heatmap) — see :class:`VizPayload`. Scatter/network views leave them ``None``.

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
from itertools import combinations

import numpy as np
from sqlalchemy import select
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
# ``(scope signature, model, layout)`` — the scope signature is the sorted set of placed work ids, so
# a changed vector set (a paper (re)indexed for the model) yields a new key, and PCA vs UMAP (P5b)
# cache independently. The stored ``vector_hash`` guards against a same-key vector change (values
# updated in place): a hash mismatch recomputes and overwrites, so the cache self-invalidates instead
# of serving a stale layout. An in-process dict is enough for a mostly single-user / few-LAN-user
# deployment. Cached tuple: ``(vector_hash, coords, assignments, effective_layout, layout_note)``.
_LAYOUT_CACHE: dict[
    tuple[tuple[str, ...], str | None, str],
    tuple[str, np.ndarray, list[int], str, str | None],
] = {}

# Embedding-cluster layout algorithms (§2b). ``pca`` (numpy, instant, no dep) is the default; ``umap``
# is opt-in and needs ``umap-learn`` from the AI extra image (see backend/Dockerfile ml-extraction).
# When ``umap`` is requested but the package is absent, the layout falls back to PCA with a note.
LAYOUT_ALGORITHMS = ("pca", "umap")
DEFAULT_LAYOUT = "pca"

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
    # B2: structured "some papers aren't indexed" hint — {reindexable: int, needs_text:[{work_id,
    # title}]} — so the UI can split "reindex to include" from "attach a PDF & extract" and list the
    # specific papers that need a file. None when everything in scope is indexed.
    reindex_hint: dict | None = None
    # Available axis options for both dropdowns; ``None`` for non-axis views. Server-driven so P3+
    # can add an axis without a frontend change.
    axis_options: list[dict[str, str]] | None = None
    # P5a typed additions for non-scatter chart views (backward-compatible; every existing view
    # leaves them ``None``). ``series`` carries stacked time-series data for the topic river as
    # ``{"years": list[int], "topics": [{"label": str, "values": list[float]}]}`` (one value per
    # year, aligned to ``years``). ``matrix`` carries a labelled square matrix for the similarity
    # heatmap as ``{"labels": list[str], "ids": list[str], "values": list[list[float]]}`` (row/column
    # order matches ``labels``/``ids``). Future chart views (P5b+) reuse these two fields rather than
    # smuggling data through ``notes``/``legend``.
    series: dict | None = None
    matrix: dict | None = None


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
        return (
            {w.id: None for w in works},
            "Similarity-to-focus axis unavailable: pick a focus paper first.",
        )
    focus = ctx.focus_work
    if focus is None:
        return (
            {w.id: None for w in works},
            "Similarity-to-focus axis unavailable: the focus paper wasn't found or isn't visible "
            "to you.",
        )
    target_works = list(works)
    if all(w.id != focus.id for w in works):
        target_works = [focus, *works]
    vectors, kept, _model, _unindexed = _paper_dense_vectors(db, target_works, ctx.embedding_model)
    if vectors is None:
        return (
            {w.id: None for w in works},
            "Similarity-to-focus axis unavailable: no real embedding model is active — select a "
            "MiniLM/nomic model (AI & Models) and reindex.",
        )
    vec_by_id: dict[uuid.UUID, dict[int, float]] = {kw.id: vectors[i] for i, kw in enumerate(kept)}
    focus_vec = vec_by_id.get(focus.id)
    if focus_vec is None:
        return (
            {w.id: None for w in works},
            "Similarity-to-focus axis unavailable: the focus paper isn't indexed for this "
            "embedding model. Reindex embeddings (AI & Models), then rebuild.",
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
            "Topic-similarity axis unavailable: pick a focus paper first.",
        )
    focus = ctx.focus_work
    focus_topics = {str(t).casefold() for t in (focus.topics or [])} if focus else set()
    if not focus_topics:
        return (
            {w.id: None for w in works},
            "Topic-similarity axis unavailable: the focus paper has no topic terms yet. Run Topic "
            "modeling (AI & Models) over a scope that includes it, then rebuild.",
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


def _reindex_hint(db: Session, skipped_works: list[Work]) -> dict | None:
    """Split papers skipped for lacking a model embedding into two actionable buckets (B2): those a
    **reindex will include** (they have extracted text → chunks) and those that first need a **PDF +
    extraction** (no chunks — reindex can't produce a vector). Returns
    ``{"reindexable": int, "needs_text": [{"work_id", "title"}]}`` or None. This is what lets the viz
    stop telling users to "reindex" a paper whose real problem is a missing PDF."""
    if not skipped_works:
        return None
    from app.models.chunk import WorkChunk  # local import avoids an import cycle

    ids = [w.id for w in skipped_works]
    with_chunks = {
        wid
        for (wid,) in db.execute(
            select(WorkChunk.work_id).where(WorkChunk.work_id.in_(ids)).distinct()
        ).all()
    }
    needs_text = [
        {"work_id": str(w.id), "title": w.canonical_title or f"Untitled ({str(w.id)[:8]})"}
        for w in skipped_works
        if w.id not in with_chunks
    ]
    return {"reindexable": len(skipped_works) - len(needs_text), "needs_text": needs_text}


def _scope_dense_matrix(
    db: Session, works: list[Work], embedding_model: str | None
) -> tuple[np.ndarray | None, list[Work], str | None, str | None, dict | None]:
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
        hint = None
        if skipped:
            kept_ids = {w.id for w in kept}
            hint = _reindex_hint(db, [w for w in works if w.id not in kept_ids])
        return (matrix if kept else None), kept, label, None, hint

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
        return None, [], provider.model_name, "No papers with text to place in this scope.", None
    note = (
        "Cluster layout uses the built-in baseline embedder; enable a real embedding model and "
        "reindex for sharper clusters."
    )
    if omitted:
        note = f"{note} {omitted} papers have no text and were omitted."
    return np.array(rows, dtype=float), kept_works, provider.model_name, note, None


@dataclass
class _EmbeddingLayout:
    coords: np.ndarray | None
    kept: list[Work]
    assignments: list[int]
    model_label: str | None
    note: str | None
    # The layout actually used (``pca``/``umap``): a requested ``umap`` degrades to ``pca`` when
    # ``umap-learn`` is absent, so this can differ from the requested layout.
    layout: str = DEFAULT_LAYOUT
    # B2 structured reindex/needs-a-PDF hint (see VizPayload.reindex_hint).
    reindex_hint: dict | None = None

    @property
    def empty(self) -> bool:
        return self.coords is None


def _embedding_layout(
    db: Session, works: list[Work], embedding_model: str | None, layout: str = DEFAULT_LAYOUT
) -> _EmbeddingLayout:
    """Source dense vectors, then project (PCA/UMAP) + cluster them, reusing the cache when possible.

    ``layout`` selects the 2D projection: ``pca`` (default) or the opt-in ``umap``. A requested
    ``umap`` falls back to PCA — with a note — when ``umap-learn`` is not installed (it lives in the
    AI extra image, not the base deps), so the map always renders.
    """
    matrix, kept, model_label, note, hint = _scope_dense_matrix(db, works, embedding_model)
    if matrix is None:
        return _EmbeddingLayout(None, [], [], model_label, note, reindex_hint=hint)

    scope_sig = tuple(sorted(str(w.id) for w in kept))
    vector_hash = hashlib.md5(  # noqa: S324 - cache fingerprint, not a security control.
        np.ascontiguousarray(matrix, dtype=float).tobytes()
    ).hexdigest()
    cache_key = (scope_sig, model_label, layout)
    cached = _LAYOUT_CACHE.get(cache_key)
    if cached is not None and cached[0] == vector_hash:
        merged = _join_notes(note, cached[4])
        return _EmbeddingLayout(
            cached[1], kept, cached[2], model_label, merged, cached[3], reindex_hint=hint
        )

    coords, effective_layout, layout_note = _project_2d(matrix, layout)
    k = max(1, min(DEFAULT_MAX_TOPICS, len(kept)))
    dense_dicts = [{i: float(v) for i, v in enumerate(row)} for row in matrix]
    assignments = _kmeans(dense_dicts, k)
    _LAYOUT_CACHE[cache_key] = (vector_hash, coords, assignments, effective_layout, layout_note)
    return _EmbeddingLayout(
        coords,
        kept,
        assignments,
        model_label,
        _join_notes(note, layout_note),
        effective_layout,
        reindex_hint=hint,
    )


def _join_notes(*notes: str | None) -> str | None:
    parts = [n for n in notes if n]
    return " ".join(parts) if parts else None


def _project_2d(matrix: np.ndarray, layout: str) -> tuple[np.ndarray, str, str | None]:
    """Project ``matrix`` to 2D by the requested layout; return ``(coords, effective_layout, note)``.

    ``umap`` needs the opt-in ``umap-learn`` package (AI extra image). When it is absent — or the
    scope is too small for UMAP to be meaningful — the projection degrades to PCA with a note so the
    caller can surface why.
    """
    if layout == "umap":
        if not _umap_available():
            return (
                _pca_2d(matrix),
                "pca",
                "UMAP layout needs the opt-in AI extra image (umap-learn not installed); showing "
                "PCA instead.",
            )
        coords = _umap_2d(matrix)
        if coords is None:
            return _pca_2d(matrix), "pca", "Too few papers for a UMAP layout; showing PCA instead."
        return coords, "umap", None
    return _pca_2d(matrix), "pca", None


def _umap_available() -> bool:
    """Whether ``umap-learn`` is importable (an opt-in AI-extra dependency, guarded)."""
    import importlib.util

    return importlib.util.find_spec("umap") is not None


def _umap_2d(matrix: np.ndarray) -> np.ndarray | None:
    """UMAP-project ``matrix`` (n×d) to 2D; ``None`` when the scope is too small to be meaningful.

    The import is guarded (opt-in dep) and ``random_state`` is pinned so the layout is reproducible
    (UMAP's JIT/numba cold-start on the first call is expected). Caller caches the result.
    """
    import umap  # noqa: PLC0415 - opt-in AI-extra dependency, imported lazily behind a guard.

    n = matrix.shape[0]
    if n < 3:
        return None
    reducer = umap.UMAP(n_components=2, n_neighbors=min(15, n - 1), min_dist=0.1, random_state=42)
    coords = reducer.fit_transform(matrix)
    return np.asarray(coords, dtype=float)


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
    layout = params.get("layout") or DEFAULT_LAYOUT
    if layout not in LAYOUT_ALGORITHMS:
        raise ValueError(f"Unknown layout: {layout}")

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

    embedding = _embedding_layout(db, works, embedding_model, layout)
    if embedding.note:
        notes.append(embedding.note)
    if embedding.empty:
        return VizPayload(
            view_type="embedding_cluster",
            nodes=[],
            axes=EMBEDDING_AXES,
            notes=notes,
            reindex_hint=embedding.reindex_hint,
        )

    kept = embedding.kept
    coords = embedding.coords
    assignments = embedding.assignments
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
        # The layout actually used (``pca``/``umap``) so the frontend toggle reflects a UMAP→PCA
        # fallback rather than claiming UMAP ran.
        "layout": embedding.layout,
    }
    return VizPayload(
        view_type="embedding_cluster",
        nodes=nodes,
        axes=EMBEDDING_AXES,
        legend=legend,
        notes=notes,
        axis_options=None,
        reindex_hint=embedding.reindex_hint,
    )


# Co-citation edge contexts (§2d). ``coupling`` = bibliographic coupling (two works linked when they
# cite the same works; weight = shared references). ``co_citation`` = classic co-citation (two works
# linked when a third work cites both; weight = shared citers). Default is coupling.
CO_CITATION_CONTEXTS = ("coupling", "co_citation")
DEFAULT_EDGE_CONTEXT = "coupling"


def _coupling_edge_weights(edges: list[VizEdge], scope_ids: set[str]) -> dict[tuple[str, str], int]:
    """Bibliographic-coupling weights: shared cited works per scope-work pair.

    ``edges`` are the resolved citation edges (``source`` cites ``target``) of the scope's own works
    in ``include_external`` mode, so a shared target may be an external (not-in-library) work — that
    is exactly the coupling signal. The weight of ``(a, b)`` is ``|cited(a) ∩ cited(b)|``.
    """
    cited: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.source in scope_ids:
            cited[edge.source].add(edge.target)
    weights: dict[tuple[str, str], int] = {}
    ordered = sorted(scope_ids)
    for a, b in combinations(ordered, 2):
        shared = cited[a] & cited[b]
        if shared:
            weights[(a, b)] = len(shared)
    return weights


def _co_citation_edge_weights(
    edges: list[VizEdge], scope_ids: set[str]
) -> dict[tuple[str, str], int]:
    """Co-citation weights: shared citers per scope-work pair.

    ``edges`` are the resolved local citation edges of the whole visible library (any in-library work
    citing any in-library work). For each citer, its scope-work targets are pooled and every pair of
    them gains one shared citer. The weight of ``(a, b)`` is the number of works that cite both. Only
    in-library citers are knowable (an external citing paper has no stored references), which bounds
    co-citation to library-internal co-citations — noted on the payload.
    """
    citer_targets: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.target in scope_ids:
            citer_targets[edge.source].add(edge.target)
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for targets in citer_targets.values():
        for a, b in combinations(sorted(targets), 2):
            weights[(a, b)] += 1
    return dict(weights)


@register_viz("co_citation")
def co_citation(db: Session, actor: User, scope: VizScope, params: dict) -> VizPayload:
    """Co-citation / bibliographic-coupling network among the SEE-filtered scope works (§2d).

    ``params['edge_context']`` selects the edge semantics: ``coupling`` (default) links works that
    cite the same works (weight = shared references), ``co_citation`` links works cited together
    (weight = shared citers). Both are computed from the resolved citation edges — resolution is
    reused from ``build_citation_graph``, never re-implemented. Nodes are the scope works; ``size`` is
    the co-citation/coupling degree (distinct linked neighbours) and ``color_group`` reuses the shared
    status/work-type helper. This is a node-link view with no fixed coordinates, so ``x``/``y`` are
    ``None`` and the frontend lays it out with an ECharts ``graph`` (force) series — consistent with
    the P2 choice to render every view through ECharts rather than a second (Cytoscape) path.
    """
    edge_context = params.get("edge_context") or DEFAULT_EDGE_CONTEXT
    if edge_context not in CO_CITATION_CONTEXTS:
        raise ValueError(f"Unknown edge context: {edge_context}")
    color_by = params.get("color_by") or DEFAULT_COLOR_BY
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
    if not works:
        notes.append("No papers in this scope.")
        return VizPayload(view_type="co_citation", nodes=[], edges=[], notes=notes)

    work_ids = [w.id for w in works]
    scope_id_strs = {str(wid) for wid in work_ids}
    if edge_context == "coupling":
        graph = build_citation_graph(
            db,
            scope_type="selected_papers",
            work_ids=work_ids,
            node_mode="include_external",
            visible_ids=visible,
        )
        graph_edges = [
            VizEdge(source=e.source, target=e.target, weight=float(e.weight)) for e in graph.edges
        ]
        weights = _coupling_edge_weights(graph_edges, scope_id_strs)
    else:
        # Co-citation needs citers, which may sit outside the scope: resolve the whole visible
        # library once, then keep only edges landing on a scope work.
        graph = build_citation_graph(
            db,
            scope_type="library",
            node_mode="local_only",
            visible_ids=visible,
        )
        graph_edges = [
            VizEdge(source=e.source, target=e.target, weight=float(e.weight)) for e in graph.edges
        ]
        weights = _co_citation_edge_weights(graph_edges, scope_id_strs)
        notes.append(
            "Co-citation counts only in-library citers (external citing papers are unknown)."
        )

    edges = [VizEdge(source=a, target=b, weight=float(w)) for (a, b), w in weights.items()]
    degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree[edge.source] += 1
        degree[edge.target] += 1

    nodes: list[VizNode] = []
    for work in works:
        node_id = str(work.id)
        nodes.append(
            VizNode(
                id=node_id,
                x=None,
                y=None,
                size=float(degree.get(node_id, 0)),
                color_group=_color_group(work, color_by),
                shape="in_library",
                label=work.canonical_title or f"Untitled work ({node_id[:8]})",
                meta={
                    "title": work.canonical_title,
                    "year": work.year,
                    "degree": degree.get(node_id, 0),
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

    return VizPayload(
        view_type="co_citation",
        nodes=nodes,
        edges=edges,
        legend=legend,
        notes=notes,
        axis_options=None,
    )


@register_viz("topic_river")
def topic_river(db: Session, actor: User, scope: VizScope, params: dict) -> VizPayload:
    """Topic-prevalence stream over publication years (§2d).

    For the SEE-filtered scope, each placed paper is assigned an embedding k-means topic (the same
    clustering + TF-IDF labelling P3's embedding-cluster map uses), then per publication year the
    share of papers in each topic is computed. The result is carried in :attr:`VizPayload.series` as
    ``{"years": [...], "topics": [{"label", "values"}]}`` with one share per year (each year's shares
    sum to 1). The frontend renders it as a stacked-area / streamgraph. Papers with no dense vector
    are skipped (reported) and papers with no publication year are excluded from the stream.
    """
    cap = int(params.get("max_nodes") or MAX_NODES)
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
        return VizPayload(view_type="topic_river", nodes=[], series=None, notes=notes)

    layout = _embedding_layout(db, works, embedding_model)
    if layout.note:
        notes.append(layout.note)
    if layout.empty:
        return VizPayload(view_type="topic_river", nodes=[], series=None, notes=notes)

    labels = _cluster_labels(layout.kept, layout.assignments)
    counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    no_year = 0
    for work, cid in zip(layout.kept, layout.assignments, strict=True):
        if work.year is None:
            no_year += 1
            continue
        counts[work.year][cid] += 1
    if not counts:
        notes.append("No papers with a publication year to chart.")
        return VizPayload(view_type="topic_river", nodes=[], series=None, notes=notes)
    if no_year:
        notes.append(
            f"{no_year} papers have no publication year and were excluded from the stream."
        )

    years = sorted(counts)
    topic_ids = sorted(labels)
    topics_series: list[dict] = []
    for cid in topic_ids:
        values: list[float] = []
        for year in years:
            year_total = sum(counts[year].values())
            share = counts[year].get(cid, 0) / year_total if year_total else 0.0
            values.append(round(share, 4))
        topics_series.append({"label": labels[cid], "values": values})

    legend = {"color_by": "cluster", "groups": [labels[cid] for cid in topic_ids]}
    return VizPayload(
        view_type="topic_river",
        nodes=[],
        series={"years": years, "topics": topics_series},
        legend=legend,
        notes=notes,
        axis_options=None,
    )


# Similarity-heatmap selection cap (§2d asks for ~≤50): a dense pairwise matrix, so the row/column
# count is deliberately small. A larger scope is trimmed to the most recent papers, reported in a note.
HEATMAP_CAP = 50


def _cosine_matrix(matrix: np.ndarray) -> np.ndarray:
    """Row-wise cosine-similarity matrix (symmetric, 1.0 diagonal for non-zero rows)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    normed = matrix / safe
    sim = normed @ normed.T
    return np.clip(sim, -1.0, 1.0)


@register_viz("similarity_heatmap")
def similarity_heatmap(db: Session, actor: User, scope: VizScope, params: dict) -> VizPayload:
    """Pairwise cosine-similarity heatmap for a small SEE-filtered selection (§2d).

    Reuses P3's dense-vector source (``_paper_dense_vectors`` via ``_scope_dense_matrix``, with the
    hash-BOW baseline fallback) and computes the symmetric cosine matrix (1.0 diagonal). The scope is
    capped at :data:`HEATMAP_CAP` (~50) since the payload is dense; a larger scope is trimmed to the
    most recent papers (publication year desc, stable within a year) and the trim is reported. The
    matrix is carried in :attr:`VizPayload.matrix` as ``{"labels", "ids", "values"}`` for the ECharts
    heatmap.
    """
    cap = min(int(params.get("max_nodes") or HEATMAP_CAP), HEATMAP_CAP)
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
        # Most-recent-first, keeping the title order within a year (stable sort on a descending key);
        # papers with no year sort last.
        works = sorted(works, key=lambda w: -(w.year if w.year is not None else -(10**9)))[:cap]
        notes.append(
            f"Showing the {cap} most recent of {total} papers (heatmap cap {cap}); refine the scope."
        )
    if not works:
        notes.append("No papers in this scope.")
        return VizPayload(view_type="similarity_heatmap", nodes=[], matrix=None, notes=notes)

    matrix, kept, _model_label, note, hint = _scope_dense_matrix(db, works, embedding_model)
    if note:
        notes.append(note)
    if matrix is None or not kept:
        return VizPayload(
            view_type="similarity_heatmap", nodes=[], matrix=None, notes=notes, reindex_hint=hint
        )

    sim = _cosine_matrix(matrix)
    labels = [w.canonical_title or f"Untitled work ({str(w.id)[:8]})" for w in kept]
    ids = [str(w.id) for w in kept]
    values = [[round(float(sim[i][j]), 4) for j in range(len(kept))] for i in range(len(kept))]
    return VizPayload(
        view_type="similarity_heatmap",
        nodes=[],
        matrix={"labels": labels, "ids": ids, "values": values},
        notes=notes,
        axis_options=None,
        reindex_hint=hint,
    )


__all__ = [
    "MAX_NODES",
    "HEATMAP_CAP",
    "CO_CITATION_CONTEXTS",
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
    "co_citation",
    "topic_river",
    "similarity_heatmap",
]
