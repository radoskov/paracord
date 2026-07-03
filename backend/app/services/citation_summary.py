"""Scoped citation analytics (SPEC §8.11, D38 Track C P4) — the README-headline summaries.

The numeric face of the same computed layer that feeds the graphs (:mod:`app.services.citation_graph`
/ :mod:`app.services.visualization`): given a SEE-filtered scope (library / rack / shelf / search /
selection / import-batch / saved-filter) it computes six analytics blocks:

* **most-cited local works** — in-library works ranked by *local in-degree* (how many scope works
  cite them), from the citation graph's resolved local edges (never re-resolved here);
* **most-cited external works** — scope works ranked by their external ``Work.citation_count`` (P1);
* **frequently-cited-but-missing works** — unresolved references aggregated by normalized
  identifier/title, ranked by citation frequency (import candidates — "you cite X a lot but don't
  have it"), each carrying a representative reference id for the ``POST /works/from-reference`` path;
* **bridge papers** — works with the highest *betweenness centrality* on the undirected local
  citation graph (exact Brandes; the local graph is capped at :data:`MAX_NODES`);
* **isolated papers** — scope works with zero local citation links (in + out degree 0);
* **chronological distribution** — scope work counts by publication year.

Everything is read-only. Results are cached in-process keyed by a **scope signature** (the member
work ids + a data-version = max ``updated_at`` over the scope + the scope's reference count + the
result limit); when the scope's works or references change the signature changes and the entry is
recomputed. An in-process dict is enough at this scale (mostly single-user / a few LAN users); a
persisted cache would live behind :func:`citation_summary` keyed by the same signature.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.citation import Reference
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.citation_graph import (
    ScopeType,
    _local_work_index,
    _resolve_reference,
    _scope_works,
    build_citation_graph,
)
from app.utils.normalization import normalize_doi, normalize_title

# Local-graph node cap (mirrors the citation/topic graph and the viz providers). Exact Brandes
# betweenness is O(V·E); at this cap it is comfortably fast in pure Python. Over the cap the scope
# is deterministically truncated (title order) and the truncation is reported in ``notes``.
MAX_NODES = 500

# Default number of rows returned per ranked block.
DEFAULT_LIMIT = 15

# Bumped when the shape/meaning of the computed summary changes so cached entries self-invalidate.
_SCHEMA_VERSION = "v1"

# Bridge-centrality method label surfaced to the client (exact, not an approximation, at this cap).
BRIDGE_METHOD = "brandes_betweenness_undirected"

# In-process summary cache, keyed by the scope signature (see :func:`_scope_signature`). Fine for a
# mostly single-user / few-LAN-user deployment; a persisted cache would slot in here keyed the same.
_SUMMARY_CACHE: dict[str, CitationSummary] = {}


@dataclass
class RankedWork:
    """An in-library work in a ranked block, with the block's score (degree / count / centrality)."""

    work_id: uuid.UUID
    title: str
    year: int | None
    doi: str | None
    score: float


@dataclass
class MissingWork:
    """A frequently-cited work not resolvable to any in-library work (an import candidate)."""

    key: str
    title: str
    doi: str | None
    year: int | None
    cited_by_count: int
    mention_count: int
    reference_id: uuid.UUID | None


@dataclass
class YearCount:
    """Scope work count for one publication year (``year`` ``None`` = unknown year)."""

    year: int | None
    work_count: int


@dataclass
class CitationSummary:
    scope_work_count: int
    most_cited_local: list[RankedWork] = field(default_factory=list)
    most_cited_external: list[RankedWork] = field(default_factory=list)
    frequently_cited_missing: list[MissingWork] = field(default_factory=list)
    bridge_papers: list[RankedWork] = field(default_factory=list)
    isolated_papers: list[RankedWork] = field(default_factory=list)
    chronological: list[YearCount] = field(default_factory=list)
    bridge_method: str = BRIDGE_METHOD
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class SummaryScope:
    """A citation-summary scope, mirroring the citation-graph / viz scope family."""

    type: ScopeType
    id: uuid.UUID | None = None
    work_ids: list[uuid.UUID] | None = None


def _scope_signature(works: list[Work], reference_count: int, limit: int) -> str:
    """A content fingerprint of the scope; when it changes the cached summary is recomputed.

    Inputs (per the P4 design): the sorted member work ids, a data-version = the max ``updated_at``
    over the scope (any edited work bumps it) + the scope's reference count (any (un)resolved
    reference bumps it), and the result ``limit`` (different truncations are distinct entries).
    """
    ids = "|".join(sorted(str(w.id) for w in works))
    latest = max((w.updated_at for w in works if w.updated_at is not None), default=None)
    raw = f"{_SCHEMA_VERSION}::{ids}::{latest.isoformat() if latest else '0'}::{reference_count}::{limit}"
    return hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324 - cache fingerprint, not security.


def _missing_key(reference: Reference) -> str | None:
    """Aggregation key for an unresolved reference: normalized DOI, else arXiv base, else title."""
    if reference.doi:
        return f"doi:{normalize_doi(reference.doi)}"
    if reference.arxiv_id:
        return f"arxiv:{reference.arxiv_id.strip().lower()}"
    if reference.title:
        title = normalize_title(reference.title)
        if title:
            return f"title:{title}"
    return None


def _betweenness(adjacency: dict[str, set[str]]) -> dict[str, float]:
    """Exact Brandes betweenness centrality over an undirected, unweighted graph.

    Standard Brandes accumulation with a BFS from each source; the final scores are halved because
    every shortest path is counted from both endpoints in an undirected graph. Nodes with no edges
    score 0. O(V·E), fine at :data:`MAX_NODES`.
    """
    nodes = list(adjacency)
    centrality: dict[str, float] = dict.fromkeys(nodes, 0.0)
    for source in nodes:
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {v: [] for v in nodes}
        sigma: dict[str, float] = dict.fromkeys(nodes, 0.0)
        sigma[source] = 1.0
        distance: dict[str, int] = dict.fromkeys(nodes, -1)
        distance[source] = 0
        queue: deque[str] = deque([source])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adjacency[v]:
                if distance[w] < 0:
                    distance[w] = distance[v] + 1
                    queue.append(w)
                if distance[w] == distance[v] + 1:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)
        delta: dict[str, float] = dict.fromkeys(nodes, 0.0)
        while stack:
            w = stack.pop()
            for v in predecessors[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != source:
                centrality[w] += delta[w]
    return {v: score / 2.0 for v, score in centrality.items()}


def citation_summary(
    db: Session,
    actor: User,
    scope: SummaryScope,
    *,
    limit: int = DEFAULT_LIMIT,
) -> CitationSummary:
    """Compute (or serve from cache) the scoped citation summary for ``actor``.

    The scope is SEE-clamped to the actor's visible works (``access.visible_work_ids``) and resolved
    with the citation graph's ``_scope_works`` — no visibility rule is re-implemented here, so a
    reader's summary never counts or names a hidden paper. Read-only: nothing is mutated.
    """
    limit = max(1, limit)
    visible = access.visible_work_ids(db, actor)
    scope_works = _scope_works(
        db,
        scope_type=scope.type,
        scope_id=scope.id,
        work_ids=scope.work_ids,
        visible_ids=visible,
    )
    works = sorted(scope_works.values(), key=lambda w: (w.canonical_title or "").casefold())
    notes: list[str] = []
    total = len(works)
    if total > MAX_NODES:
        works = works[:MAX_NODES]
        notes.append(
            f"Analyzed {MAX_NODES} of {total} papers (node cap {MAX_NODES}); refine the scope for "
            "the rest."
        )
    member_ids = [w.id for w in works]

    if not works:
        summary = CitationSummary(scope_work_count=0, notes=["No papers in this scope."])
        summary.version = _scope_signature([], 0, limit)
        return summary

    references = db.scalars(select(Reference).where(Reference.citing_work_id.in_(member_ids))).all()
    signature = _scope_signature(works, len(references), limit)
    cached = _SUMMARY_CACHE.get(signature)
    if cached is not None:
        return cached

    summary = _compute(db, works, member_ids, references, visible, limit, notes)
    summary.version = signature
    _SUMMARY_CACHE[signature] = summary
    return summary


def _compute(
    db: Session,
    works: list[Work],
    member_ids: list[uuid.UUID],
    references: list[Reference],
    visible: set[uuid.UUID] | None,
    limit: int,
    notes: list[str],
) -> CitationSummary:
    """Do the heavy analytics (called only on a cache miss)."""
    scope_id_set = set(member_ids)

    # Local edges + degrees / bridges / isolated: reuse the citation graph's resolved-edge
    # computation over exactly this (capped) member set — resolution is never re-implemented here.
    graph = build_citation_graph(
        db,
        scope_type="selected_papers",
        work_ids=member_ids,
        node_mode="include_external",
        visible_ids=visible,
    )
    node_by_id = {node.id: node for node in graph.nodes}
    in_degree: Counter[str] = Counter()
    linked: set[str] = set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        target_node = node_by_id.get(edge.target)
        if target_node is None or target_node.work_id is None:
            continue  # external target — not a local citation link
        in_degree[edge.target] += 1  # one distinct citing work per resolved edge
        linked.add(edge.source)
        linked.add(edge.target)
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)

    most_cited_local = _rank_local_targets(db, in_degree, node_by_id, limit)
    bridge_papers = _bridge_papers(db, adjacency, node_by_id, limit)
    isolated_papers = _isolated_papers(works, linked, limit)
    most_cited_external = _most_cited_external(works, limit)
    frequently_cited_missing = _missing_works(references, scope_id_set, visible, db, limit)
    chronological = _chronological(works)

    return CitationSummary(
        scope_work_count=len(works),
        most_cited_local=most_cited_local,
        most_cited_external=most_cited_external,
        frequently_cited_missing=frequently_cited_missing,
        bridge_papers=bridge_papers,
        isolated_papers=isolated_papers,
        chronological=chronological,
        notes=notes,
    )


def _load_works(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, Work]:
    if not ids:
        return {}
    return {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(ids))).all()}


def _rank_local_targets(db, in_degree, node_by_id, limit) -> list[RankedWork]:
    """In-library works ranked by local in-degree (distinct scope works citing them)."""
    target_ids = {
        node_by_id[nid].work_id for nid in in_degree if node_by_id[nid].work_id is not None
    }
    works_by_id = _load_works(db, target_ids)
    ranked: list[RankedWork] = []
    for node_id, degree in in_degree.items():
        node = node_by_id[node_id]
        if node.work_id is None:
            continue
        work = works_by_id.get(node.work_id)
        ranked.append(
            RankedWork(
                work_id=node.work_id,
                title=(work.canonical_title if work else None) or node.label,
                year=(work.year if work else node.year),
                doi=(work.doi if work else node.doi),
                score=float(degree),
            )
        )
    ranked.sort(key=lambda r: (-r.score, r.title.casefold()))
    return ranked[:limit]


def _bridge_papers(db, adjacency, node_by_id, limit) -> list[RankedWork]:
    """Works with the highest betweenness centrality on the undirected local citation graph."""
    if not adjacency:
        return []
    scores = _betweenness(adjacency)
    ranked_ids = [nid for nid, score in scores.items() if score > 0.0]
    work_ids = {
        node_by_id[nid].work_id for nid in ranked_ids if node_by_id[nid].work_id is not None
    }
    works_by_id = _load_works(db, work_ids)
    bridges: list[RankedWork] = []
    for node_id in ranked_ids:
        node = node_by_id[node_id]
        if node.work_id is None:
            continue
        work = works_by_id.get(node.work_id)
        bridges.append(
            RankedWork(
                work_id=node.work_id,
                title=(work.canonical_title if work else None) or node.label,
                year=(work.year if work else node.year),
                doi=(work.doi if work else node.doi),
                score=round(scores[node_id], 4),
            )
        )
    bridges.sort(key=lambda r: (-r.score, r.title.casefold()))
    return bridges[:limit]


def _isolated_papers(works: list[Work], linked: set[str], limit: int) -> list[RankedWork]:
    """Scope works with zero local citation links (they neither cite nor are cited locally)."""
    isolated = [
        RankedWork(
            work_id=work.id,
            title=work.canonical_title or f"Untitled work ({str(work.id)[:8]})",
            year=work.year,
            doi=work.doi,
            score=0.0,
        )
        for work in works
        if str(work.id) not in linked
    ]
    isolated.sort(key=lambda r: r.title.casefold())
    return isolated[:limit]


def _most_cited_external(works: list[Work], limit: int) -> list[RankedWork]:
    """Scope works ranked by their external citation count (P1 ``Work.citation_count``)."""
    ranked = [
        RankedWork(
            work_id=work.id,
            title=work.canonical_title or f"Untitled work ({str(work.id)[:8]})",
            year=work.year,
            doi=work.doi,
            score=float(work.citation_count),
        )
        for work in works
        if work.citation_count is not None
    ]
    ranked.sort(key=lambda r: (-r.score, r.title.casefold()))
    return ranked[:limit]


def _missing_works(references, scope_id_set, visible, db, limit) -> list[MissingWork]:
    """Aggregate unresolved references by normalized identifier/title, ranked by citation frequency.

    Resolution reuses the citation graph's helpers (``_local_work_index`` / ``_resolve_reference``);
    a reference is "missing" only when it resolves to an *external* node — a reference that resolves
    to a local work (even one hidden from the actor) is not surfaced. ``cited_by_count`` counts the
    distinct scope works citing the missing work; ``mention_count`` counts every citing reference.
    """
    scope_works = {wid: db.get(Work, wid) for wid in scope_id_set}
    scope_works = {wid: w for wid, w in scope_works.items() if w is not None}
    local_index = _local_work_index(
        db, scope_works=scope_works, references=references, visible_ids=visible
    )

    @dataclass
    class _Agg:
        title: str
        doi: str | None
        year: int | None
        citing: set[uuid.UUID]
        mentions: int
        reference_id: uuid.UUID
        has_doi: bool

    aggregates: dict[str, _Agg] = {}
    for reference in references:
        resolved = _resolve_reference(reference, local_index)
        if resolved is None:
            continue
        _node, resolution = resolved
        if resolution != "external":
            continue
        key = _missing_key(reference)
        if key is None:
            continue
        agg = aggregates.get(key)
        title = reference.title or reference.doi or reference.arxiv_id or "Cited work"
        has_doi = reference.doi is not None
        if agg is None:
            aggregates[key] = _Agg(
                title=title,
                doi=normalize_doi(reference.doi) if reference.doi else None,
                year=reference.year,
                citing={reference.citing_work_id},
                mentions=1,
                reference_id=reference.id,
                has_doi=has_doi,
            )
        else:
            agg.citing.add(reference.citing_work_id)
            agg.mentions += 1
            # Prefer a representative reference that carries a DOI and a real title.
            if (has_doi and not agg.has_doi) or (
                reference.title and agg.title in (agg.doi, "Cited work")
            ):
                agg.reference_id = reference.id
                agg.title = title
                agg.has_doi = agg.has_doi or has_doi
                if reference.doi and agg.doi is None:
                    agg.doi = normalize_doi(reference.doi)
            if agg.year is None and reference.year is not None:
                agg.year = reference.year

    missing = [
        MissingWork(
            key=key,
            title=agg.title,
            doi=agg.doi,
            year=agg.year,
            cited_by_count=len(agg.citing),
            mention_count=agg.mentions,
            reference_id=agg.reference_id,
        )
        for key, agg in aggregates.items()
    ]
    missing.sort(key=lambda m: (-m.cited_by_count, -m.mention_count, m.title.casefold()))
    return missing[:limit]


def _chronological(works: list[Work]) -> list[YearCount]:
    """Scope work counts by publication year (unknown-year works bucketed under ``year=None``)."""
    counts: Counter[int | None] = Counter(work.year for work in works)
    known = sorted(y for y in counts if y is not None)
    result = [YearCount(year=y, work_count=counts[y]) for y in known]
    if None in counts:
        result.append(YearCount(year=None, work_count=counts[None]))
    return result


__all__ = [
    "MAX_NODES",
    "DEFAULT_LIMIT",
    "BRIDGE_METHOD",
    "RankedWork",
    "MissingWork",
    "YearCount",
    "CitationSummary",
    "SummaryScope",
    "citation_summary",
]
