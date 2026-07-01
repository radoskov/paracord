"""Build a scoped citation graph from extracted references (SPEC §8.9, §12.5).

Nodes are works; an edge ``A -> B`` means work A's bibliography cites work B. Each reference is
resolved to a local work by its identifier (a pre-set ``resolved_work_id``, else an exact DOI or
arXiv-base match); unresolved references point at an *external* node carrying the reference's own
metadata. ``node_mode="local_only"`` keeps only edges between works inside the scope, while
``include_external`` also surfaces cited works that are not in the library yet.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.citation import Reference
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work
from app.services.duplicate_detection import split_arxiv_id
from app.utils.normalization import normalize_doi

# Flat tuple of scope kinds. ``search_result`` and ``selected_papers`` resolve from an explicit
# ``work_ids`` list (mirroring export's selection/search); ``import_batch`` resolves from
# ``Work.import_batch_id == scope_id``. ``saved_filter`` (Phase B7) also resolves from an explicit
# ``work_ids`` list — the endpoint loads the filter (owned-by-actor 404) and passes the ids from
# ``resolve_saved_filter_work_ids`` (already visibility-clamped) in, exactly like the explicit sets.
ScopeType = Literal[
    "library", "shelf", "rack", "search_result", "selected_papers", "import_batch", "saved_filter"
]
NodeMode = Literal["local_only", "include_external"]


@dataclass
class GraphNode:
    id: str
    label: str
    type: str  # "local" | "external"
    work_id: uuid.UUID | None
    year: int | None
    doi: str | None


@dataclass
class GraphEdge:
    source: str
    target: str
    weight: int
    resolution: str  # "local_match" | "external"


@dataclass
class CitationGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def build_citation_graph(
    db: Session,
    *,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None = None,
    work_ids: list[uuid.UUID] | None = None,
    node_mode: NodeMode = "local_only",
    collapse_versions: bool = False,
    visible_ids: set[uuid.UUID] | None = None,
) -> CitationGraph:
    """Build the citation graph for a scope.

    ``visible_ids`` (Phase H access control) restricts which local works may appear as nodes or
    resolution targets: only works in this set are graphed. ``None`` means unrestricted
    (admin/owner). The scope set and the local resolution index are both filtered, so a hidden
    work can never surface as a node, an edge target, or a resolved-title leak.

    ``work_ids`` supplies the explicit set for the ``search_result``/``selected_papers`` scopes
    (mirroring export's selection/search); ``scope_id`` supplies the batch id for ``import_batch``.
    When ``collapse_versions`` is set, works sharing a ``version_group_id`` are merged into a single
    representative node in a post-build pass (edges re-aggregated, version-to-version self-loops
    dropped).
    """
    scope_works = _scope_works(
        db,
        scope_type=scope_type,
        scope_id=scope_id,
        work_ids=work_ids,
        visible_ids=visible_ids,
    )
    if not scope_works:
        return CitationGraph(summary=_summary([], [], scope_count=0, unresolved=0))

    local_index = _local_work_index(db, visible_ids=visible_ids)

    nodes: dict[str, GraphNode] = {str(work.id): _local_node(work) for work in scope_works.values()}
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    edge_resolution: dict[tuple[str, str], str] = {}
    unresolved = 0

    references = db.scalars(
        select(Reference).where(Reference.citing_work_id.in_(scope_works.keys()))
    ).all()

    for reference in references:
        resolved = _resolve_reference(reference, local_index)
        if resolved is None:
            if reference.resolution_status == "unresolved":
                pass  # status already correct
            unresolved += 1
            continue
        target_node, resolution = resolved
        # Access control: never surface a hidden local work as an edge target (a persisted
        # ``resolved_work_id`` could otherwise point at a work outside the caller's visibility).
        if (
            visible_ids is not None
            and target_node.work_id is not None
            and target_node.work_id not in visible_ids
        ):
            unresolved += 1
            continue
        # Persist the resolution result so subsequent queries don't need to re-resolve.
        if reference.resolution_status == "unresolved":
            reference.resolution_status = resolution
        # In local_only mode keep only edges that stay inside the scope.
        if node_mode == "local_only" and (
            resolution == "external" or target_node.work_id not in scope_works
        ):
            continue
        if target_node.id == str(reference.citing_work_id):
            continue  # drop self-citations
        nodes.setdefault(target_node.id, target_node)
        key = (str(reference.citing_work_id), target_node.id)
        edge_weights[key] += 1
        edge_resolution[key] = resolution

    edges = [
        GraphEdge(
            source=source,
            target=target,
            weight=weight,
            resolution=edge_resolution[(source, target)],
        )
        for (source, target), weight in edge_weights.items()
    ]
    node_list = list(nodes.values())
    graph = CitationGraph(
        nodes=node_list,
        edges=edges,
        summary=_summary(node_list, edges, scope_count=len(scope_works), unresolved=unresolved),
    )
    if collapse_versions:
        graph = _collapse_versions(db, graph, visible_ids=visible_ids)
    return graph


def _collapse_versions(
    db: Session, graph: CitationGraph, *, visible_ids: set[uuid.UUID] | None = None
) -> CitationGraph:
    """Merge works that share a ``version_group_id`` into a single representative node.

    Pure post-pass over an already-built graph: builds a ``node id -> representative id`` map, drops
    non-representative local nodes, remaps every edge's endpoints to their representative, re-sums
    weights per ``(source, target)`` (deduping parallel edges), and drops any resulting self-loop
    (the same self-citation rule applied post-collapse — a version citing another version of itself
    is not a real citation). External nodes always map to themselves (``work_id is None``). Only
    visible works participate, so the pass can never surface a hidden work.
    """
    # Representative per group: the work whose ``version_group_id`` equals its own id; if none such
    # (e.g. a representative outside the graph/visibility), fall back to the deterministic min member
    # id so collapse is stable and never picks a hidden work.
    grouped = db.execute(
        select(Work.id, Work.version_group_id).where(Work.version_group_id.is_not(None))
    ).all()
    members_by_group: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    self_reps: dict[uuid.UUID, uuid.UUID] = {}
    for work_id, group_id in grouped:
        if visible_ids is not None and work_id not in visible_ids:
            continue
        members_by_group[group_id].append(work_id)
        if work_id == group_id:
            self_reps[group_id] = work_id
    rep_of_work: dict[str, str] = {}
    for group_id, members in members_by_group.items():
        rep = self_reps.get(group_id, min(members))
        for member in members:
            rep_of_work[str(member)] = str(rep)

    def group_of(node_id: str) -> str:
        # External nodes and ungrouped locals map to themselves.
        return rep_of_work.get(node_id, node_id)

    # Count only groups that actually had more than one member present in the graph (i.e. a real
    # collapse happened for that group).
    members_in_graph: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        rep = group_of(node.id)
        if rep in rep_of_work.values() or node.id in rep_of_work:
            members_in_graph[rep] += 1
    collapsed_groups = {rep for rep, count in members_in_graph.items() if count > 1}

    # Keep representative + external + ungrouped nodes; drop non-representative group members.
    kept_nodes = [node for node in graph.nodes if group_of(node.id) == node.id]

    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    edge_resolution: dict[tuple[str, str], str] = {}
    for edge in graph.edges:
        source = group_of(edge.source)
        target = group_of(edge.target)
        if source == target:
            continue  # drop version-to-version (and other resulting) self-loops
        key = (source, target)
        edge_weights[key] += edge.weight
        # Prefer a local match if any contributing edge resolved locally.
        if key not in edge_resolution or edge.resolution == "local_match":
            edge_resolution[key] = edge.resolution
    edges = [
        GraphEdge(source=s, target=t, weight=w, resolution=edge_resolution[(s, t)])
        for (s, t), w in edge_weights.items()
    ]

    summary = _summary(
        kept_nodes,
        edges,
        scope_count=graph.summary.get("scope_work_count", len(kept_nodes)),
        unresolved=graph.summary.get("unresolved_reference_count", 0),
    )
    summary["collapsed_version_groups"] = len(collapsed_groups)
    return CitationGraph(nodes=kept_nodes, edges=edges, summary=summary)


def _scope_works(
    db: Session,
    *,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None,
    work_ids: list[uuid.UUID] | None = None,
    visible_ids: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, Work]:
    # Flat if/elif so each scope is self-contained and Phase B7 can append ``saved_filter`` as one
    # more branch. Every branch feeds the single ``visible_ids`` clamp at the tail — the one place
    # visibility is enforced, so no branch can leak a hidden work as a node/target.
    if scope_type == "library":
        works = db.scalars(select(Work)).all()
    elif scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope id is required for a shelf graph")
        works = db.scalars(
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .where(ShelfWork.shelf_id == scope_id)
        ).all()
    elif scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope id is required for a rack graph")
        works = db.scalars(
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .where(RackShelf.rack_id == scope_id)
            .distinct()
        ).all()
    elif scope_type in ("search_result", "selected_papers", "saved_filter"):
        # An explicit set of works: a search result set, the library multi-selection, or a saved
        # filter's resolved ids (Phase B7 — the endpoint resolves + clamps the filter and passes the
        # ids in, mirroring export's selection/search). An empty set is a valid (empty) scope.
        if not work_ids:
            return {}
        works = db.scalars(select(Work).where(Work.id.in_(work_ids))).all()
    elif scope_type == "import_batch":
        if scope_id is None:
            raise ValueError("scope id is required for an import-batch graph")
        works = db.scalars(select(Work).where(Work.import_batch_id == scope_id)).all()
    else:
        raise ValueError(f"Unsupported graph scope: {scope_type}")
    if visible_ids is not None:
        works = [work for work in works if work.id in visible_ids]
    return {work.id: work for work in works}


def _local_work_index(db: Session, *, visible_ids: set[uuid.UUID] | None = None) -> dict[str, Work]:
    """Map ``doi:<doi>`` / ``arxiv:<base>`` identifier keys to local works.

    When ``visible_ids`` is provided, hidden works are excluded so a reference can never resolve to
    (and leak the title/year/DOI of) a work the caller may not see.
    """
    index: dict[str, Work] = {}
    for work in db.scalars(select(Work)).all():
        if visible_ids is not None and work.id not in visible_ids:
            continue
        for key in _identifier_keys(doi=work.doi, arxiv_id=work.arxiv_id):
            index.setdefault(key, work)
    return index


def _identifier_keys(*, doi: str | None, arxiv_id: str | None) -> list[str]:
    keys: list[str] = []
    if doi:
        keys.append(f"doi:{normalize_doi(doi)}")
    base = split_arxiv_id(arxiv_id)["base"] if arxiv_id else None
    if base:
        keys.append(f"arxiv:{base}")
    return keys


def _resolve_reference(
    reference: Reference, local_index: dict[str, Work]
) -> tuple[GraphNode, str] | None:
    if reference.resolved_work_id is not None:
        # Trust a previously persisted resolution.
        return (
            GraphNode(
                id=str(reference.resolved_work_id),
                label=reference.title or "Cited work",
                type="local",
                work_id=reference.resolved_work_id,
                year=reference.year,
                doi=reference.doi,
            ),
            "local_match",
        )
    for key in _identifier_keys(doi=reference.doi, arxiv_id=reference.arxiv_id):
        work = local_index.get(key)
        if work is not None:
            return _local_node(work), "local_match"
    if reference.title or reference.doi or reference.arxiv_id:
        return _external_node(reference), "external"
    return None


def _local_node(work: Work) -> GraphNode:
    return GraphNode(
        id=str(work.id),
        label=work.canonical_title or f"Untitled work ({str(work.id)[:8]})",
        type="local",
        work_id=work.id,
        year=work.year,
        doi=work.doi,
    )


def _external_node(reference: Reference) -> GraphNode:
    # Stable id so repeated references to the same external work collapse to one node.
    key = next(iter(_identifier_keys(doi=reference.doi, arxiv_id=reference.arxiv_id)), None)
    node_id = f"ext:{key}" if key else f"ext:ref:{reference.id}"
    return GraphNode(
        id=node_id,
        label=reference.title or reference.doi or reference.arxiv_id or "External work",
        type="external",
        work_id=None,
        year=reference.year,
        doi=reference.doi,
    )


def _summary(
    nodes: list[GraphNode], edges: list[GraphEdge], *, scope_count: int, unresolved: int
) -> dict:
    return {
        "scope_work_count": scope_count,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "local_node_count": sum(1 for node in nodes if node.type == "local"),
        "external_node_count": sum(1 for node in nodes if node.type == "external"),
        "unresolved_reference_count": unresolved,
    }
