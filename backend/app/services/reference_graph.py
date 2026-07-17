"""Per-paper weighted reference graph (issue_batch_6 #5 / B7).

Builds a local citation graph of one paper's references: the base paper plus a node per reference,
coloured local vs external, sized by a **section-weighted mention count** (how often — and in which
section — the base paper cites each reference). Section weights are applied client-side (per-user,
editable in Profile), so this service returns the raw per-bucket mention counts and lets the UI
compute node size — a weight tweak then re-sizes instantly with no recompute.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.citation import CitationMention, Reference, ReferenceCitation
from app.models.work import Work
from app.services.reference_links import references_for_work

# Free-text TEI section head → bucket. First matching rule wins (order matters: "related work"
# must beat "...work method..."), else "other". The buckets line up with the Profile weights.
SECTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("abstract", ("abstract",)),
    (
        "related",
        (
            "related",
            "state of the art",
            "state-of-the-art",
            "background",
            "prior work",
            "literature",
        ),
    ),
    ("introduction", ("introduction", "intro")),
    ("methods", ("method", "approach", "materials", "architecture", "implementation")),
    (
        "results",
        ("result", "experiment", "evaluation", "discussion", "analysis", "ablation", "findings"),
    ),
]

# Buckets the classifier can emit (the UI weights are keyed by these).
SECTION_BUCKETS = ("abstract", "introduction", "related", "methods", "results", "other")

# Server-side fallback weights (the client normally applies the user's Profile weights instead).
DEFAULT_SECTION_WEIGHTS: dict[str, float] = {
    "abstract": 5.0,
    "methods": 4.0,
    "results": 3.0,
    "introduction": 2.0,
    "other": 2.0,
    "related": 1.0,
}


def classify_section(label: str | None) -> str:
    """Map a free-text section heading to one of :data:`SECTION_BUCKETS` ("other" is the fallback)."""
    if not label:
        return "other"
    low = label.casefold()
    for bucket, keywords in SECTION_RULES:
        if any(k in low for k in keywords):
            return bucket
    return "other"


def _weighted(counts: dict[str, int], weights: dict[str, float]) -> float:
    return sum(weights.get(bucket, 1.0) * n for bucket, n in counts.items())


DEFAULT_MAX_EXTERNAL = 500


def build_reference_graph(
    db: Session,
    work: Work,
    *,
    visible_ids: set[uuid.UUID] | None,
    include_ref_edges: bool = False,
    include_citing: bool = False,
    max_external: int = DEFAULT_MAX_EXTERNAL,
) -> dict:
    """Assemble the base paper + one node per reference, with per-bucket mention counts.

    ``visible_ids`` clamps which resolved references count as **local** (a resolved work the caller
    can't SEE is treated as external metadata). ``include_ref_edges`` adds citation edges between the
    resolved-local references that cite each other (a mini local citation network); base→reference
    edges are always present. ``include_citing`` adds the work's fetched external citing papers as
    incoming ``kind="citing"`` nodes with edges pointing INTO the base paper (batch10 #8).
    """
    references = references_for_work(db, work.id)

    # Section-bucketed mention counts per reference, from the in-text citation mentions.
    counts_by_ref: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ref_id, section in db.execute(
        select(CitationMention.reference_id, CitationMention.section_label).where(
            CitationMention.citing_work_id == work.id
        )
    ).all():
        counts_by_ref[ref_id][classify_section(section)] += 1

    base_id = str(work.id)
    nodes: list[dict] = [
        {
            "id": base_id,
            "label": work.canonical_title or "This paper",
            "year": work.year,
            "kind": "base",
            "resolved_work_id": base_id,
            "section_counts": {},
            "mention_count": 0,
            "weighted": 0.0,
            "citation_count": None,
            "local_degree": None,
            "topic_similarity": None,
            "venue": work.venue,  # 5d: colour-by-venue
            "doi": work.doi,
        }
    ]

    def _is_local(ref: Reference) -> bool:
        return ref.resolved_work_id is not None and (
            visible_ids is None or ref.resolved_work_id in visible_ids
        )

    def _is_likely(ref: Reference) -> bool:
        # A soft "likely local" candidate (batch 12): a fuzzy match awaiting confirmation. It is NOT
        # resolved, so local-only metrics are never computed on the guess — it just gets its own node
        # kind + colour and carries the suggested work id/score for the tooltip.
        return (
            not _is_local(ref)
            and ref.resolution_status == "likely_match"
            and ref.suggested_work_id is not None
            and (visible_ids is None or ref.suggested_work_id in visible_ids)
        )

    # Per-local-work metrics for the selectable Y axes (B7 v2): global citation count, in-library
    # citation degree, and topic similarity to the base paper. Null for external references.
    local_work_ids = {ref.resolved_work_id for ref in references if _is_local(ref)}
    citation_by_work: dict[uuid.UUID, int | None] = {}
    topics_by_work: dict[uuid.UUID, list] = {}
    degree_by_work: dict[uuid.UUID, int] = {}
    venue_by_work: dict[uuid.UUID, str | None] = {}
    title_by_work: dict[uuid.UUID, str | None] = {}
    year_by_work: dict[uuid.UUID, int | None] = {}
    if local_work_ids:
        for wid, cc, topics, venue, w_title, w_year in db.execute(
            select(
                Work.id,
                Work.citation_count,
                Work.topics,
                Work.venue,
                Work.canonical_title,
                Work.year,
            ).where(Work.id.in_(local_work_ids))
        ).all():
            citation_by_work[wid] = cc
            topics_by_work[wid] = list(topics or [])
            venue_by_work[wid] = venue
            title_by_work[wid] = w_title
            year_by_work[wid] = w_year
        degree_stmt = (
            select(
                Reference.resolved_work_id,
                func.count(distinct(ReferenceCitation.citing_work_id)),
            )
            .join(ReferenceCitation, ReferenceCitation.reference_id == Reference.id)
            .where(Reference.resolved_work_id.in_(local_work_ids))
            .group_by(Reference.resolved_work_id)
        )
        if visible_ids is not None:
            degree_stmt = degree_stmt.where(ReferenceCitation.citing_work_id.in_(visible_ids))
        for wid, deg in db.execute(degree_stmt).all():
            degree_by_work[wid] = int(deg)

    base_topics = {str(t).casefold() for t in (work.topics or [])}

    def _topic_similarity(wid: uuid.UUID) -> float | None:
        """Jaccard similarity of this work's topics vs the base paper's (None if either has none)."""
        terms = {str(t).casefold() for t in topics_by_work.get(wid, [])}
        if not base_topics or not terms:
            return None
        union = base_topics | terms
        return round(len(base_topics & terms) / len(union), 4) if union else None

    # Map a resolved-local work id → its reference node id, for the optional ref→ref edge pass.
    work_to_ref_node: dict[uuid.UUID, str] = {}
    for ref in references:
        is_local = _is_local(ref)
        is_likely = _is_likely(ref)
        counts = dict(counts_by_ref.get(ref.id, {}))
        wid = ref.resolved_work_id if is_local else None
        node = {
            "id": str(ref.id),
            # A resolved reference displays the WORK's canonical metadata (UX batch 3): once the
            # paper is imported, the graph must show its real title/year — the extraction-time
            # reference values (often truncated, year frequently missing) are only the fallback.
            "label": (title_by_work.get(wid) if wid else None)
            or ref.title
            or ref.raw_citation
            or "Untitled reference",
            "year": (year_by_work.get(wid) if wid else None) or ref.year,
            "kind": "local" if is_local else "likely_local" if is_likely else "external",
            "resolved_work_id": str(wid) if wid else None,
            # A soft candidate (batch 12): carried for the tooltip + jump-to, but NOT resolved.
            "suggested_work_id": str(ref.suggested_work_id) if is_likely else None,
            "match_score": ref.match_score if is_likely else None,
            "authors": list(ref.authors) if ref.authors else None,
            "section_counts": counts,
            "mention_count": sum(counts.values()),
            "weighted": round(_weighted(counts, DEFAULT_SECTION_WEIGHTS), 3),
            # Selectable-Y metrics (null for external/likely / when unavailable).
            "citation_count": citation_by_work.get(wid) if wid else None,
            "local_degree": degree_by_work.get(wid, 0) if wid else None,
            "topic_similarity": _topic_similarity(wid) if wid else None,
            # 5d colour-by-venue (local: resolved work's venue; external refs don't store one) and
            # 5g click-to-import prefill data.
            "venue": venue_by_work.get(wid) if wid else None,
            "doi": ref.doi,
        }
        nodes.append(node)
        if wid:
            work_to_ref_node[wid] = node["id"]

    # base → reference (the "this paper cites it" star).
    edges: list[dict] = [{"source": base_id, "target": str(ref.id)} for ref in references]

    if include_ref_edges and work_to_ref_node:
        # Local ref→ref citation edges: a resolved-local reference work that itself cites another
        # resolved-local reference work in this set.
        local_ids = list(work_to_ref_node)
        for citing_wid, cited_wid in db.execute(
            select(ReferenceCitation.citing_work_id, Reference.resolved_work_id)
            .join(ReferenceCitation, ReferenceCitation.reference_id == Reference.id)
            .where(
                ReferenceCitation.citing_work_id.in_(local_ids),
                Reference.resolved_work_id.in_(local_ids),
            )
        ).all():
            if citing_wid != cited_wid:
                edges.append(
                    {
                        "source": work_to_ref_node[citing_wid],
                        "target": work_to_ref_node[cited_wid],
                    }
                )

    if include_citing:
        # Incoming external citing papers (batch10 #8): one node per linked (deduplicated)
        # ExternalPaper, with an edge pointing INTO the base paper so the direction is visible.
        from app.models.external_citation import ExternalCitationLink, ExternalPaper

        citing = db.scalars(
            select(ExternalPaper)
            .join(ExternalCitationLink, ExternalCitationLink.external_paper_id == ExternalPaper.id)
            .where(ExternalCitationLink.work_id == work.id)
            .order_by(ExternalPaper.year.desc().nullslast())
        ).all()
        for ec in citing:
            node_id = f"citing:{ec.id}"
            nodes.append(
                {
                    "id": node_id,
                    "label": ec.title or "Citing paper",
                    "year": ec.year,
                    "kind": "citing",
                    # An in-library citer (resolved by the local matcher, clamped to visibility)
                    # carries its work id so the UI can link/badge it as local.
                    "resolved_work_id": str(ec.resolved_work_id)
                    if ec.resolved_work_id is not None
                    and (visible_ids is None or ec.resolved_work_id in visible_ids)
                    else None,
                    # ExternalPaper.authors is a "; "-joined display string.
                    "authors": [a.strip() for a in ec.authors.split(";") if a.strip()]
                    if ec.authors
                    else None,
                    "section_counts": {},
                    "mention_count": 0,
                    "weighted": 0.0,
                    "citation_count": None,
                    "local_degree": None,
                    "topic_similarity": None,
                    "venue": ec.venue,
                    "doi": ec.doi,
                }
            )
            edges.append({"source": node_id, "target": base_id})

    # Cap the external fan-out (item 1, 2026-07-13): keep the ``max_external`` highest-weighted
    # external references and the same number of newest citing papers; local/likely/base nodes are
    # never dropped. Hidden counts ship in the payload so the UI can say "N more hidden".
    cap = max(0, max_external)
    external_nodes = [n for n in nodes if n["kind"] == "external"]
    citing_nodes = [n for n in nodes if n["kind"] == "citing"]
    dropped_ids: set[str] = set()
    if len(external_nodes) > cap:
        external_nodes.sort(key=lambda n: (-(n.get("weighted") or 0.0), n["id"]))
        dropped_ids.update(n["id"] for n in external_nodes[cap:])
    if len(citing_nodes) > cap:
        # Already ordered newest-first by the fetch query.
        dropped_ids.update(n["id"] for n in citing_nodes[cap:])
    external_hidden = sum(1 for n in external_nodes[cap:]) if len(external_nodes) > cap else 0
    citing_hidden = sum(1 for n in citing_nodes[cap:]) if len(citing_nodes) > cap else 0
    if dropped_ids:
        nodes = [n for n in nodes if n["id"] not in dropped_ids]
        edges = [
            e for e in edges if e["source"] not in dropped_ids and e["target"] not in dropped_ids
        ]

    # Membership names (privacy-filtered, ALL of them) for the base + local nodes, so the client
    # can color by shelf/rack/tag entirely client-side (like its kind/venue coloring) and render
    # multi-membership nodes as a color wheel. External nodes have no memberships by definition.
    from app.services.graph_color import membership_groups

    membered_ids = [uuid.UUID(base_id), *work_to_ref_node.keys()]
    memberships: dict[str, dict[str, list[str]]] = {}
    for kind in ("shelf", "rack", "tag"):
        for wid, names in membership_groups(db, membered_ids, kind).items():
            memberships.setdefault(str(wid), {})[kind] = names
    node_by_work = {
        str(uuid.UUID(base_id)): base_id,
        **{str(w): n for w, n in work_to_ref_node.items()},
    }
    by_node_id = {
        node_by_work[wid]: groups for wid, groups in memberships.items() if wid in node_by_work
    }
    for node in nodes:
        groups = by_node_id.get(node["id"])
        if groups:
            node["memberships"] = groups

    return {
        "base_work_id": base_id,
        "nodes": nodes,
        "edges": edges,
        "external_hidden": external_hidden,
        "citing_hidden": citing_hidden,
    }
