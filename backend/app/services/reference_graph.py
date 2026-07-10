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

from app.models.citation import CitationMention, Reference
from app.models.work import Work

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


def build_reference_graph(
    db: Session,
    work: Work,
    *,
    visible_ids: set[uuid.UUID] | None,
    include_ref_edges: bool = False,
    include_citing: bool = False,
) -> dict:
    """Assemble the base paper + one node per reference, with per-bucket mention counts.

    ``visible_ids`` clamps which resolved references count as **local** (a resolved work the caller
    can't SEE is treated as external metadata). ``include_ref_edges`` adds citation edges between the
    resolved-local references that cite each other (a mini local citation network); base→reference
    edges are always present. ``include_citing`` adds the work's fetched external citing papers as
    incoming ``kind="citing"`` nodes with edges pointing INTO the base paper (batch10 #8).
    """
    references = list(
        db.scalars(
            select(Reference)
            .where(Reference.citing_work_id == work.id)
            .order_by(Reference.created_at)
        ).all()
    )

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

    # Per-local-work metrics for the selectable Y axes (B7 v2): global citation count, in-library
    # citation degree, and topic similarity to the base paper. Null for external references.
    local_work_ids = {ref.resolved_work_id for ref in references if _is_local(ref)}
    citation_by_work: dict[uuid.UUID, int | None] = {}
    topics_by_work: dict[uuid.UUID, list] = {}
    degree_by_work: dict[uuid.UUID, int] = {}
    venue_by_work: dict[uuid.UUID, str | None] = {}
    if local_work_ids:
        for wid, cc, topics, venue in db.execute(
            select(Work.id, Work.citation_count, Work.topics, Work.venue).where(
                Work.id.in_(local_work_ids)
            )
        ).all():
            citation_by_work[wid] = cc
            topics_by_work[wid] = list(topics or [])
            venue_by_work[wid] = venue
        degree_stmt = (
            select(Reference.resolved_work_id, func.count(distinct(Reference.citing_work_id)))
            .where(Reference.resolved_work_id.in_(local_work_ids))
            .group_by(Reference.resolved_work_id)
        )
        if visible_ids is not None:
            degree_stmt = degree_stmt.where(Reference.citing_work_id.in_(visible_ids))
        for wid, deg in db.execute(degree_stmt).all():
            degree_by_work[wid] = int(deg)

    base_topics = {str(t).casefold() for t in (work.topics or [])}

    def _topic_similarity(wid: uuid.UUID) -> float | None:
        terms = {str(t).casefold() for t in topics_by_work.get(wid, [])}
        if not base_topics or not terms:
            return None
        union = base_topics | terms
        return round(len(base_topics & terms) / len(union), 4) if union else None

    # Map a resolved-local work id → its reference node id, for the optional ref→ref edge pass.
    work_to_ref_node: dict[uuid.UUID, str] = {}
    for ref in references:
        is_local = _is_local(ref)
        counts = dict(counts_by_ref.get(ref.id, {}))
        wid = ref.resolved_work_id if is_local else None
        node = {
            "id": str(ref.id),
            "label": ref.title or ref.raw_citation or "Untitled reference",
            "year": ref.year,
            "kind": "local" if is_local else "external",
            "resolved_work_id": str(wid) if wid else None,
            "section_counts": counts,
            "mention_count": sum(counts.values()),
            "weighted": round(_weighted(counts, DEFAULT_SECTION_WEIGHTS), 3),
            # Selectable-Y metrics (null for external / when unavailable).
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
            select(Reference.citing_work_id, Reference.resolved_work_id).where(
                Reference.citing_work_id.in_(local_ids),
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
                    "resolved_work_id": None,
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

    return {"base_work_id": base_id, "nodes": nodes, "edges": edges}
