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

from sqlalchemy import select
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
) -> dict:
    """Assemble the base paper + one node per reference, with per-bucket mention counts.

    ``visible_ids`` clamps which resolved references count as **local** (a resolved work the caller
    can't SEE is treated as external metadata). ``include_ref_edges`` adds citation edges between the
    resolved-local references that cite each other (a mini local citation network); base→reference
    edges are always present.
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
        }
    ]

    # Map a resolved-local work id → its reference node id, for the optional ref→ref edge pass.
    work_to_ref_node: dict[uuid.UUID, str] = {}
    for ref in references:
        is_local = ref.resolved_work_id is not None and (
            visible_ids is None or ref.resolved_work_id in visible_ids
        )
        counts = dict(counts_by_ref.get(ref.id, {}))
        node = {
            "id": str(ref.id),
            "label": ref.title or ref.raw_citation or "Untitled reference",
            "year": ref.year,
            "kind": "local" if is_local else "external",
            "resolved_work_id": str(ref.resolved_work_id) if is_local else None,
            "section_counts": counts,
            "mention_count": sum(counts.values()),
            "weighted": round(_weighted(counts, DEFAULT_SECTION_WEIGHTS), 3),
        }
        nodes.append(node)
        if is_local:
            work_to_ref_node[ref.resolved_work_id] = node["id"]

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

    return {"base_work_id": base_id, "nodes": nodes, "edges": edges}
