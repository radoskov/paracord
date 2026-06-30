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

ScopeType = Literal["library", "shelf", "rack"]
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
    node_mode: NodeMode = "local_only",
    visible_ids: set[uuid.UUID] | None = None,
) -> CitationGraph:
    """Build the citation graph for a scope.

    ``visible_ids`` (Phase H access control) restricts which local works may appear as nodes or
    resolution targets: only works in this set are graphed. ``None`` means unrestricted
    (admin/owner). The scope set and the local resolution index are both filtered, so a hidden
    work can never surface as a node, an edge target, or a resolved-title leak.
    """
    scope_works = _scope_works(
        db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids
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
    return CitationGraph(
        nodes=node_list,
        edges=edges,
        summary=_summary(node_list, edges, scope_count=len(scope_works), unresolved=unresolved),
    )


def _scope_works(
    db: Session,
    *,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None,
    visible_ids: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, Work]:
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
