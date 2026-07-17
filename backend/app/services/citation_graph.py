"""Build a scoped citation graph from extracted references (SPEC §8.9, §12.5).

Nodes are works; an edge ``A -> B`` means work A's bibliography cites work B. Each reference is
resolved to a local work by its identifier (a pre-set ``resolved_work_id``, else an exact DOI or
arXiv-base match); unresolved references point at an *external* node carrying the reference's own
metadata. ``node_mode="local_only"`` keeps only edges between works inside the scope, while
``include_external`` also surfaces cited works that are not in the library yet.
"""

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.citation import Reference, ReferenceCitation
from app.models.duplicate import DuplicateCandidate
from app.models.file import FileWorkLink
from app.models.work import Work
from app.services.duplicate_detection import split_arxiv_id
from app.services.graph_color import MEMBERSHIP_COLOR_KINDS, membership_groups
from app.services.reference_links import citing_work_ids_subquery
from app.services.scope_resolution import resolve_scope_works
from app.utils.normalization import arxiv_base_from_doi, normalize_doi

# 1-hop neighborhood cap (mirrors the graph node cap): the focus work plus its expanded neighbors are
# capped so a hub paper can't produce an unbounded neighborhood.
MAX_NEIGHBORHOOD_NODES = 500

# Flat tuple of scope kinds. ``search_result`` and ``selected_papers`` resolve from an explicit
# ``work_ids`` list (mirroring export's selection/search); ``import_batch`` resolves from
# ``Work.import_batch_id == scope_id``. ``saved_filter`` (Phase B7) also resolves from an explicit
# ``work_ids`` list — the endpoint loads the filter (owned-by-actor 404) and passes the ids from
# ``resolve_saved_filter_work_ids`` (already visibility-clamped) in, exactly like the explicit sets.
ScopeType = Literal[
    "library", "shelf", "rack", "search_result", "selected_papers", "import_batch", "saved_filter"
]
NodeMode = Literal["local_only", "include_external"]
# Node-size metric (§8.9). All three are computed server-side and shipped on every node (see
# ``_attach_node_metrics``), so the frontend switches ``size_by`` by re-styling live without a
# refetch or relayout. ``degree`` is the weighted degree (sum of incident mention counts).
SizeBy = Literal["degree", "pagerank", "betweenness"]
# Node-color grouping (§8.9). ``none`` leaves nodes uncolored; the others attach one categorical
# ``color_group`` per local node from existing library data (SEE-clamped). External nodes are never
# colored.
ColorBy = Literal["none", "shelf", "rack", "tag", "topic", "status", "year"]


@dataclass
class GraphNode:
    """One work (local or external) rendered as a graph node."""

    id: str
    label: str
    type: str  # "local" | "external"
    work_id: uuid.UUID | None
    year: int | None
    doi: str | None
    # §8.9 depth encodings, populated by ``_attach_node_metrics`` when ``compute_metrics`` is set.
    # ``degree`` is the weighted (mention-count) degree; centrality is over the final node/edge set.
    degree: int = 0
    pagerank: float = 0.0
    betweenness: float = 0.0
    # External/global citation count from stored metadata (local nodes only; None for external
    # nodes and works with no count fetched). A selectable node-size channel (UX batch 4b).
    citation_count: int | None = None
    # Categorical color group per the request's ``color_by`` (``None`` when uncolored/external).
    color_group: str | None = None
    # ALL membership groups for shelf/rack/tag color-by (a paper can be on several shelves/racks
    # and carry several tags) — the UI renders >1 as a multi-segment "color wheel" node.
    # ``color_group`` stays the first entry for anything reading the single value.
    color_groups: list[str] | None = None
    # Review-warning marker: the work has a file-link warning state or an open duplicate candidate.
    warning: bool = False


@dataclass
class GraphEdge:
    """A directed citation edge between two node ids, aggregated by shared reference/citer."""

    source: str
    target: str
    weight: int
    resolution: str  # "local_match" | "external"
    # 2026-07-16: direction relative to the scope — "reference" (scope work → cited work) or
    # "citing" (a paper → scope work). Lets the UI colour the two kinds of edge differently.
    relation: str = "reference"


@dataclass
class CitationGraph:
    """Result of a graph build: the node/edge set plus a summary dict of counts/flags."""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


DEFAULT_MAX_EXTERNAL = 50


def build_citation_graph(
    db: Session,
    *,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None = None,
    work_ids: list[uuid.UUID] | None = None,
    node_mode: NodeMode = "local_only",
    collapse_versions: bool = False,
    compute_metrics: bool = False,
    color_by: ColorBy = "none",
    visible_ids: set[uuid.UUID] | None = None,
    max_external: int = DEFAULT_MAX_EXTERNAL,
    max_external_citing: int | None = None,
    include_citing: bool = False,
    max_nodes: int | None = None,
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

    ``compute_metrics`` (§8.9 depth) attaches per-node centrality (weighted ``degree``, ``pagerank``,
    exact Brandes ``betweenness``) over the final node/edge set plus a ``warning`` marker, and — when
    ``color_by`` is not ``none`` — a categorical ``color_group``. It is off by default so the
    per-request viz callers that only need edges/degree don't pay the centrality cost.
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

    # Which scope works cite each (shared) canonical reference — a reference may be cited by several.
    citing_by_ref: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for ref_id, citing_wid in db.execute(
        select(ReferenceCitation.reference_id, ReferenceCitation.citing_work_id).where(
            ReferenceCitation.citing_work_id.in_(scope_works.keys())
        )
    ).all():
        citing_by_ref[ref_id].append(citing_wid)
    references = db.scalars(select(Reference).where(Reference.id.in_(citing_by_ref.keys()))).all()

    local_index = _local_work_index(
        db,
        scope_works=scope_works,
        references=references if node_mode == "include_external" else None,
        visible_ids=visible_ids,
    )

    nodes: dict[str, GraphNode] = {str(work.id): _local_node(work) for work in scope_works.values()}
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    edge_resolution: dict[tuple[str, str], str] = {}
    edge_relation: dict[tuple[str, str], str] = {}  # 2026-07-16: "reference" | "citing"
    unresolved = 0

    for reference in references:
        citing_wids = citing_by_ref.get(reference.id, [])
        resolved = _resolve_reference(reference, local_index)
        if resolved is None:
            unresolved += len(citing_wids)  # one unresolved edge per citing work in scope
            continue
        target_node, resolution = resolved
        # Access control: never surface a hidden local work as an edge target (a persisted
        # ``resolved_work_id`` could otherwise point at a work outside the caller's visibility).
        if (
            visible_ids is not None
            and target_node.work_id is not None
            and target_node.work_id not in visible_ids
        ):
            unresolved += len(citing_wids)
            continue
        # Read-only: reference→work resolution is owned and persisted by the matcher
        # (app.services.reference_matching) on the write path — extraction, new-work reverse-rescan,
        # merge/delete re-resolve, and the manual/startup full rescan. The graph trusts the stored
        # ``resolved_work_id`` (see ``_resolve_reference``) and never mutates it here, so building a
        # graph is a pure read (safe under concurrency and on a read-only connection).
        # In local_only mode keep only edges that stay inside the scope.
        if node_mode == "local_only" and (
            resolution == "external" or target_node.work_id not in scope_works
        ):
            continue
        nodes.setdefault(target_node.id, target_node)
        # One edge per citing work in scope (a shared reference fans out to each of them).
        for citing_wid in citing_wids:
            if target_node.id == str(citing_wid):
                continue  # drop self-citations
            key = (str(citing_wid), target_node.id)
            edge_weights[key] += 1
            edge_resolution[key] = resolution
            edge_relation[key] = "reference"

    # Citing papers (2026-07-16, Q9): the papers that CITE the scope works, from the already-fetched
    # incoming-citation data (ExternalCitationLink → ExternalPaper). Edge points citer → scope work
    # (relation "citing"). An in-library citer (resolved + visible) links as a local node; otherwise
    # it's an external node. Skipped in local_only mode for external citers. `citing_available` lets
    # the UI distinguish "no citing papers fetched" from "none exist".
    citing_available = False
    if include_citing:
        from app.models.external_citation import ExternalCitationLink, ExternalPaper

        cited_ids = {str(wid) for wid in scope_works}
        link_rows = db.execute(
            select(ExternalPaper, ExternalCitationLink.work_id)
            .join(ExternalCitationLink, ExternalCitationLink.external_paper_id == ExternalPaper.id)
            .where(ExternalCitationLink.work_id.in_(scope_works.keys()))
        ).all()
        citing_available = bool(link_rows)
        for ext, cited_wid in link_rows:
            cited_id = str(cited_wid)
            if cited_id not in cited_ids:
                continue
            rwid = ext.resolved_work_id
            if rwid is not None and (visible_ids is None or rwid in visible_ids):
                citer_id = str(rwid)
                if citer_id not in nodes:
                    citer_work = db.get(Work, rwid)
                    if citer_work is None:
                        continue
                    nodes[citer_id] = _local_node(citer_work)
                resolution = "local_match"
            else:
                if node_mode == "local_only":
                    continue
                citer_id = f"citing:{ext.id}"
                if citer_id not in nodes:
                    nodes[citer_id] = GraphNode(
                        id=citer_id,
                        label=ext.title or ext.doi or "Citing paper",
                        type="external",
                        work_id=None,
                        year=ext.year,
                        doi=ext.doi,
                    )
                resolution = "external"
            if citer_id == cited_id:
                continue
            key = (citer_id, cited_id)
            # A reference edge for the same directed pair already says "A cites B" — don't double it.
            if edge_relation.get(key) == "reference":
                continue
            edge_weights[key] += 1
            edge_resolution[key] = resolution
            edge_relation[key] = "citing"

    # Cap the external fan-out (item 1, 2026-07-13; even distribution 2026-07-16): a large scope can
    # drag in thousands of cited-but-not-in-library nodes that drown the layout. Rather than a global
    # top-N (where one paper with 800 refs eats the whole budget), distribute the ``max_external``
    # budget ACROSS the scope papers — see ``_distribute_external_keep``.
    # References (external edge TARGETS) and citing papers (external edge SOURCES) get SEPARATE
    # budgets (2026-07-16, owner request) so a paper's many references never starve the citing half
    # (or vice versa); each is distributed across the scope papers independently.
    cap_citing = max_external if max_external_citing is None else max_external_citing
    external_hidden = 0
    citing_hidden = 0
    if node_mode == "include_external":
        external_set = {nid for nid, node in nodes.items() if node.type == "external"}
        ref_by_source: dict[str, list[tuple[str, int]]] = defaultdict(list)  # scope → ext refs
        cit_by_cited: dict[str, list[tuple[str, int]]] = defaultdict(list)  # scope → ext citers
        for (source, target), weight in edge_weights.items():
            if edge_relation.get((source, target)) == "citing":
                if source in external_set:
                    cit_by_cited[target].append((source, weight))
            elif target in external_set:
                ref_by_source[source].append((target, weight))
        ref_ext = {t for refs in ref_by_source.values() for t, _ in refs}
        cit_ext = {s for cits in cit_by_cited.values() for s, _ in cits}
        keep_ref = (
            _distribute_external_keep(ref_by_source, max(0, max_external))
            if len(ref_ext) > max(0, max_external)
            else ref_ext
        )
        keep_cit = (
            _distribute_external_keep(cit_by_cited, max(0, cap_citing))
            if len(cit_ext) > max(0, cap_citing)
            else cit_ext
        )
        keep = keep_ref | keep_cit
        dropped = external_set - keep
        external_hidden = len(ref_ext - keep_ref)
        citing_hidden = len(cit_ext - keep_cit)
        for nid in dropped:
            nodes.pop(nid, None)
        edge_weights = {
            key: weight
            for key, weight in edge_weights.items()
            if key[0] not in dropped and key[1] not in dropped
        }

    edges = [
        GraphEdge(
            source=source,
            target=target,
            weight=weight,
            resolution=edge_resolution[(source, target)],
            relation=edge_relation.get((source, target), "reference"),
        )
        for (source, target), weight in edge_weights.items()
    ]
    node_list = list(nodes.values())
    # Backfill citation_count for any local node built via the reference fast-path (out-of-scope
    # resolved target, no Work loaded) so the citation-count size channel covers every local node.
    missing_cc = [
        n.work_id for n in node_list if n.type == "local" and n.work_id and n.citation_count is None
    ]
    if missing_cc:
        counts = dict(
            db.execute(select(Work.id, Work.citation_count).where(Work.id.in_(missing_cc))).all()
        )
        for n in node_list:
            if n.type == "local" and n.work_id in counts:
                n.citation_count = counts[n.work_id]
    # L-a (owner decision): cap the TOTAL node count, keeping the best-connected nodes, BEFORE the
    # O(V·E) centrality pass — previously a library scope ran exact betweenness over an unbounded
    # graph on the request path. The cap is admin-configurable per surface; hidden counts ship in
    # the summary so the UI can say so.
    nodes_hidden = 0
    if max_nodes is not None and len(node_list) > max(1, max_nodes):
        weight_by_node: dict[str, int] = defaultdict(int)
        for (source, target), weight in edge_weights.items():
            weight_by_node[source] += weight
            weight_by_node[target] += weight
        node_list.sort(key=lambda n: (-weight_by_node.get(n.id, 0), n.type != "local", n.id))
        kept_ids = {n.id for n in node_list[: max(1, max_nodes)]}
        nodes_hidden = len(node_list) - len(kept_ids)
        node_list = [n for n in node_list if n.id in kept_ids]
        edges = [e for e in edges if e.source in kept_ids and e.target in kept_ids]
    summary = _summary(node_list, edges, scope_count=len(scope_works), unresolved=unresolved)
    summary["external_hidden"] = external_hidden
    summary["citing_hidden"] = citing_hidden
    # True when incoming-citation data was fetched for the scope (so the UI can say "no citing
    # papers fetched" rather than implying none exist).
    summary["citing_available"] = citing_available
    summary["citing_edge_count"] = sum(1 for e in edges if e.relation == "citing")
    summary["nodes_hidden"] = nodes_hidden
    graph = CitationGraph(
        nodes=node_list,
        edges=edges,
        summary=summary,
    )
    if collapse_versions:
        graph = _collapse_versions(db, graph, visible_ids=visible_ids)
    if compute_metrics:
        _attach_node_metrics(db, graph, color_by=color_by, visible_ids=visible_ids)
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
    """Scope members as an id→Work dict — a thin shim over the SHARED resolver (owner decision
    2026-07-13, L-b): scope semantics, the merged-shadow filter, and the visibility clamp live in
    ``app.services.scope_resolution`` for every feature; this keeps only the dict shape the graph
    builders consume. citation_summary / venue_author_summary / visualization import this shim, so
    the whole citation cluster resolves scopes through one code path.
    """
    works = resolve_scope_works(
        db, scope_type, scope_id, visible_ids=visible_ids, work_ids=work_ids
    )
    return {work.id: work for work in works}


def _local_work_index(
    db: Session,
    *,
    scope_works: dict[uuid.UUID, Work],
    references: list[Reference] | None = None,
    visible_ids: set[uuid.UUID] | None = None,
) -> dict[str, Work]:
    """Map ``doi:<doi>`` / ``arxiv:<base>`` identifier keys to local works.

    Built from the already-loaded scope works instead of scanning the whole Work table. When
    ``references`` are given (``include_external`` mode, where a reference may resolve to a local
    work outside the scope), works matching the references' identifiers are fetched in one query
    and added. When ``visible_ids`` is provided, hidden works are excluded so a reference can never
    resolve to (and leak the title/year/DOI of) a work the caller may not see.
    """
    candidates: list[Work] = list(scope_works.values())
    if references is not None:
        dois = {normalize_doi(ref.doi) for ref in references if ref.doi}
        arxiv_bases = {
            base
            for base in (split_arxiv_id(ref.arxiv_id)["base"] for ref in references if ref.arxiv_id)
            if base
        }
        # A reference carrying an arXiv DOI can resolve to a work known only by its arXiv id.
        arxiv_bases |= {
            base for base in (arxiv_base_from_doi(ref.doi) for ref in references if ref.doi) if base
        }
        conditions = []
        if dois:
            conditions.append(func.lower(Work.doi).in_(dois))
        if arxiv_bases:
            conditions.append(Work.arxiv_base_id.in_(arxiv_bases))
        if conditions:
            candidates.extend(
                work
                for work in db.scalars(select(Work).where(or_(*conditions))).all()
                if work.id not in scope_works
            )
    index: dict[str, Work] = {}
    for work in candidates:
        if work.merged_into_id is not None:
            continue  # a merged shadow is never a reference resolution target (Batch D)
        if visible_ids is not None and work.id not in visible_ids:
            continue
        for key in _identifier_keys(doi=work.doi, arxiv_id=work.arxiv_id):
            index.setdefault(key, work)
    return index


def _identifier_keys(*, doi: str | None, arxiv_id: str | None) -> list[str]:
    """Build the ``doi:``/``arxiv:`` lookup keys used to match a work/reference in the local index."""
    keys: list[str] = []
    base = split_arxiv_id(arxiv_id)["base"] if arxiv_id else None
    if doi:
        keys.append(f"doi:{normalize_doi(doi)}")
        # An arXiv DOI (10.48550/arXiv.<id>) and a bare arXiv id spell the same paper — emit the
        # arxiv key too so either spelling on either side still intersects.
        base = base or arxiv_base_from_doi(doi)
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


def _distribute_external_keep(
    ext_by_source: dict[str, list[tuple[str, int]]], limit: int
) -> set[str]:
    """Which external node ids to keep, distributing the ``limit`` budget across scope papers.

    (2026-07-16, owner's algorithm.) N = scope papers that cite ≥1 external work.
      - Absolute pass: A = ceil((limit/2) / N) targets per paper (≥1), assigned greedily by paper id
        and capped at the total ``limit`` — so when ``limit < N`` the budget is exhausted and later
        papers get none (and there is no relative pass).
      - Relative pass: with remainder E = limit - A·N (if > 0), each paper gets R_i = E·C_i/S more
        (C_i = its external-ref count, S = ΣC_i), rounded largest-remainder.
    Each paper keeps its top-k external targets by edge weight; the kept set is their union (shared
    externals count once, so the union may be smaller than the nominal budget — that's fine)."""
    papers = sorted(s for s, refs in ext_by_source.items() if refs)
    n = len(papers)
    if n == 0 or limit <= 0:
        return set()
    a = max(1, math.ceil((limit / 2) / n))
    keep: set[str] = set()
    quota: dict[str, int] = {}
    spent = 0
    for s in papers:
        refs = sorted(ext_by_source[s], key=lambda t: (-t[1], t[0]))
        take = min(a, len(refs), max(0, limit - spent))
        quota[s] = take
        keep.update(tid for tid, _ in refs[:take])
        spent += take
        if spent >= limit:
            break
    remainder = limit - a * n
    if remainder > 0:
        counts = {s: len(ext_by_source[s]) for s in papers}
        total = sum(counts.values())
        if total > 0:
            raw = {s: remainder * counts[s] / total for s in papers}
            extra = {s: int(raw[s]) for s in papers}
            leftover = remainder - sum(extra.values())
            for s in sorted(papers, key=lambda s: (-(raw[s] - extra[s]), s))[: max(0, leftover)]:
                extra[s] += 1
            for s in papers:
                refs = sorted(ext_by_source[s], key=lambda t: (-t[1], t[0]))
                start = quota.get(s, 0)
                keep.update(tid for tid, _ in refs[start : start + extra[s]])
    return keep


def _local_node(work: Work) -> GraphNode:
    """Build a ``type="local"`` node from an in-library work."""
    return GraphNode(
        id=str(work.id),
        label=work.canonical_title or f"Untitled work ({str(work.id)[:8]})",
        type="local",
        work_id=work.id,
        year=work.year,
        doi=work.doi,
        citation_count=work.citation_count,
    )


def _external_node(reference: Reference) -> GraphNode:
    """Build a ``type="external"`` node for a reference that didn't resolve to a local work."""
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
    """Build the base summary dict (counts); callers add extra keys (e.g. hidden-node counts)."""
    return {
        "scope_work_count": scope_count,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "local_node_count": sum(1 for node in nodes if node.type == "local"),
        "external_node_count": sum(1 for node in nodes if node.type == "external"),
        "unresolved_reference_count": unresolved,
    }


def _betweenness(adjacency: dict[str, set[str]]) -> dict[str, float]:
    """Exact Brandes betweenness centrality over an undirected, unweighted graph.

    Standard Brandes accumulation with a BFS from each source; the final scores are halved because
    every shortest path is counted from both endpoints in an undirected graph. Nodes with no edges
    score 0. O(V·E), fine at the graph's node cap. Shared with :mod:`app.services.citation_summary`
    (the bridge-papers block imports this so the Brandes impl lives in one place).
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


def _pagerank(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1.0e-6,
) -> dict[str, float]:
    """Weighted PageRank over the directed citation graph (edge ``A -> B`` = A cites B).

    Pure-python power iteration with edge weights = mention counts and the standard dangling-node
    redistribution; converges in well under ``max_iter`` at the graph's node cap. Returns a score per
    node id summing to ~1.
    """
    ids = [node.id for node in nodes]
    n = len(ids)
    if n == 0:
        return {}
    id_set = set(ids)
    out_links: dict[str, list[tuple[str, float]]] = defaultdict(list)
    out_weight: dict[str, float] = defaultdict(float)
    for edge in edges:
        if edge.source in id_set and edge.target in id_set:
            out_links[edge.source].append((edge.target, float(edge.weight)))
            out_weight[edge.source] += float(edge.weight)
    rank = dict.fromkeys(ids, 1.0 / n)
    base = (1.0 - damping) / n
    for _ in range(max_iter):
        dangling = sum(rank[i] for i in ids if out_weight[i] == 0.0)
        dangling_share = damping * dangling / n
        new = {i: base + dangling_share for i in ids}
        for src, links in out_links.items():
            total = out_weight[src]
            share = damping * rank[src]
            for target, weight in links:
                new[target] += share * (weight / total)
        diff = sum(abs(new[i] - rank[i]) for i in ids)
        rank = new
        if diff < tol:
            break
    return rank


def _attach_node_metrics(
    db: Session, graph: CitationGraph, *, color_by: ColorBy, visible_ids: set[uuid.UUID] | None
) -> None:
    """Attach §8.9 depth encodings to ``graph.nodes`` in place (centrality, color group, warning).

    Centrality is computed over the final node/edge set (post-collapse). ``color_group`` and the
    ``warning`` marker are attached only to local nodes; external nodes stay uncolored/unmarked. All
    color/warning lookups are restricted to the local work ids already present as nodes, which were
    SEE-clamped when the graph was built.
    """
    degree: dict[str, int] = defaultdict(int)
    undirected: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        degree[edge.source] += edge.weight
        degree[edge.target] += edge.weight
        undirected.setdefault(edge.source, set()).add(edge.target)
        undirected.setdefault(edge.target, set()).add(edge.source)
    betweenness = _betweenness(undirected)
    pagerank = _pagerank(graph.nodes, graph.edges)
    for node in graph.nodes:
        node.degree = degree.get(node.id, 0)
        node.betweenness = round(betweenness.get(node.id, 0.0), 6)
        node.pagerank = round(pagerank.get(node.id, 0.0), 6)

    local_ids = [node.work_id for node in graph.nodes if node.work_id is not None]
    if color_by != "none":
        groups = _color_groups(db, local_ids, color_by)
        multi = (
            membership_groups(db, local_ids, color_by) if color_by in MEMBERSHIP_COLOR_KINDS else {}
        )
        for node in graph.nodes:
            if node.work_id is not None:
                node.color_group = groups.get(node.work_id)
                node.color_groups = multi.get(node.work_id)
            # 2026-07-16: external nodes conform to the colour scheme where they carry the value —
            # `year` is the only attribute they have — so they aren't forced into a flat grey. Other
            # color-by modes (status/shelf/tag/topic) have no external data → they stay uncoloured.
            elif node.type == "external" and color_by == "year":
                node.color_group = str(node.year) if node.year is not None else "unknown"
    warned = _warning_work_ids(db, local_ids)
    for node in graph.nodes:
        if node.work_id is not None and node.work_id in warned:
            node.warning = True
    graph.summary["color_by"] = color_by


def _color_groups(
    db: Session, work_ids: list[uuid.UUID], color_by: ColorBy
) -> dict[uuid.UUID, str]:
    """Map each local work id to a categorical color group for ``color_by``.

    ``status``/``topic``/``year`` read directly off the work; ``shelf``/``rack``/``tag`` resolve
    through the shared membership helper (privacy-filtered) and use the alphabetically-first
    membership here — the FULL list additionally lands on ``node.color_groups`` so the UI can
    render multi-membership nodes as a color wheel.
    """
    if not work_ids:
        return {}
    if color_by == "status":
        rows = db.execute(select(Work.id, Work.reading_status).where(Work.id.in_(work_ids))).all()
        return {work_id: (status or "unread") for work_id, status in rows}
    if color_by == "year":
        rows = db.execute(select(Work.id, Work.year).where(Work.id.in_(work_ids))).all()
        return {work_id: (str(year) if year is not None else "unknown") for work_id, year in rows}
    if color_by == "topic":
        rows = db.execute(select(Work.id, Work.topics).where(Work.id.in_(work_ids))).all()
        return {work_id: (str(topics[0]) if topics else "untopiced") for work_id, topics in rows}
    if color_by in MEMBERSHIP_COLOR_KINDS:  # shelf / rack / tag — shared, privacy-filtered
        return {
            work_id: names[0]
            for work_id, names in membership_groups(db, work_ids, color_by).items()
        }
    return {}


def _warning_work_ids(db: Session, work_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Local work ids carrying a review warning: a file-link warning state, or an open duplicate.

    Reuses the D31.4 ``FileWorkLink.warning_state`` (multiwork/multifile) and the open
    ``DuplicateCandidate`` signals — the same signals the ``warning:`` / ``duplicate:`` search
    filters key off — so the graph badge agrees with the library review filters.
    """
    if not work_ids:
        return set()
    id_set = set(work_ids)
    warned: set[uuid.UUID] = set(
        db.scalars(
            select(FileWorkLink.work_id).where(
                FileWorkLink.work_id.in_(work_ids),
                FileWorkLink.warning_state != "none",
            )
        ).all()
    )
    dup_rows = db.execute(
        select(DuplicateCandidate.entity_a_id, DuplicateCandidate.entity_b_id).where(
            DuplicateCandidate.status == "open",
            or_(
                and_(
                    DuplicateCandidate.entity_a_type == "work",
                    DuplicateCandidate.entity_a_id.in_(work_ids),
                ),
                and_(
                    DuplicateCandidate.entity_b_type == "work",
                    DuplicateCandidate.entity_b_id.in_(work_ids),
                ),
            ),
        )
    ).all()
    for entity_a, entity_b in dup_rows:
        if entity_a in id_set:
            warned.add(entity_a)
        if entity_b in id_set:
            warned.add(entity_b)
    return warned


def build_citation_neighborhood(
    db: Session,
    *,
    work_id: uuid.UUID,
    hops: int = 1,
    node_mode: NodeMode = "local_only",
    color_by: ColorBy = "none",
    visible_ids: set[uuid.UUID] | None = None,
) -> CitationGraph | None:
    """Build the local citation neighborhood (``hops`` steps) around one focus work (§8.9).

    Expands breadth-first over local citation links in both directions (works the focus cites and
    works that cite it), capped at :data:`MAX_NEIGHBORHOOD_NODES`, then builds the induced subgraph
    over the collected works via :func:`build_citation_graph` (reusing all resolution + centrality +
    color/warning machinery). SEE-clamped throughout: a hidden work is never a seed, a neighbor, or a
    node. Returns ``None`` when the focus work is missing or not visible (the caller 404s).
    """
    if visible_ids is not None and work_id not in visible_ids:
        return None
    focus = db.get(Work, work_id)
    if focus is None or focus.merged_into_id is not None:
        return None  # a merged shadow (Batch D) is never a neighborhood focus

    collected: set[uuid.UUID] = {work_id}
    frontier: set[uuid.UUID] = {work_id}
    for _ in range(max(1, hops)):
        neighbors = _direct_citation_neighbors(db, frontier, visible_ids=visible_ids)
        fresh = neighbors - collected
        if not fresh:
            break
        collected |= fresh
        frontier = fresh
        if len(collected) >= MAX_NEIGHBORHOOD_NODES:
            break

    ids = list(collected)[:MAX_NEIGHBORHOOD_NODES]
    graph = build_citation_graph(
        db,
        scope_type="selected_papers",
        work_ids=ids,
        node_mode=node_mode,
        compute_metrics=True,
        color_by=color_by,
        visible_ids=visible_ids,
    )
    graph.summary["focus_work_id"] = str(work_id)
    graph.summary["hops"] = max(1, hops)
    return graph


def _direct_citation_neighbors(
    db: Session, frontier: set[uuid.UUID], *, visible_ids: set[uuid.UUID] | None
) -> set[uuid.UUID]:
    """Local works one citation link from any work in ``frontier`` (both directions), SEE-clamped."""
    frontier_works = db.scalars(
        select(Work).where(Work.id.in_(frontier), Work.merged_into_id.is_(None))
    ).all()
    neighbors: set[uuid.UUID] = set()

    # Outgoing: references from the frontier works, resolved to local works (anywhere in the library,
    # via a references-aware index — the same resolution the include-external graph uses).
    refs = db.scalars(
        select(Reference).where(Reference.id.in_(citing_work_ids_subquery(frontier)))
    ).all()
    index = _local_work_index(
        db,
        scope_works={work.id: work for work in frontier_works},
        references=refs,
        visible_ids=visible_ids,
    )
    for ref in refs:
        resolved = _resolve_reference(ref, index)
        if resolved is not None and resolved[0].work_id is not None:
            neighbors.add(resolved[0].work_id)

    # Incoming: references (from any work) that resolve to a frontier work — by persisted
    # ``resolved_work_id`` or an exact DOI match on the frontier works' identifiers. The citing work
    # is a library work; clamp it to the visible set below.
    dois = [normalize_doi(work.doi) for work in frontier_works if work.doi]
    conditions = [Reference.resolved_work_id.in_(frontier)]
    if dois:
        conditions.append(func.lower(Reference.doi).in_(dois))
    citer_ids = db.scalars(
        select(ReferenceCitation.citing_work_id)
        .join(Reference, Reference.id == ReferenceCitation.reference_id)
        .where(or_(*conditions))
    ).all()
    neighbors.update(citer_ids)

    neighbors.discard(None)  # defensive: never carry a NULL citer through
    if neighbors:
        # Drop any merged shadow (Batch D) that a persisted resolved_work_id / DOI match pulled in.
        shadow_ids = set(
            db.scalars(
                select(Work.id).where(Work.id.in_(neighbors), Work.merged_into_id.is_not(None))
            ).all()
        )
        neighbors -= shadow_ids
    if visible_ids is not None:
        neighbors = {work_id for work_id in neighbors if work_id in visible_ids}
    return neighbors
