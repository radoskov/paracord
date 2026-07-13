"""Canonical-reference consolidation — batch-12 "Phase 1b", owner decisions S13/S14.

``Reference`` rows are meant to be one-per-cited-paper, but three histories leave duplicates
sharing a dedup key: rows that predate batch 12 (migration 0059 deliberately did a lossless 1:1
conversion), the unlocked find-then-insert race between concurrent extractions, and legacy
key shapes (an arXiv DOI used to key as ``doi:10.48550/…``, now as ``arxiv:<base>``).

Policy (S13, option B — "fold the safe, queue the contested"):

* A group whose rows carry at most compatible states is **auto-folded** into the oldest row:
  links/mentions repointed, metadata merged non-null-first, the resolution taken from the
  ladder-best row (confirmed > rejected > local > likely[score] > external > unresolved).
* A **contradiction** — two different user-confirmed targets, or a confirmation of the very work
  another twin's rejection refused — is never auto-folded. The extra rows get a
  ``|conflict:<id8>`` dedup-key suffix (S13 sub-decision) so the pending-review list is one LIKE
  query and a future unique index on ``dedup_key`` isn't blocked by an unread queue. An admin
  resolves the group from the Admin → Reference dupes tab by picking the winning resolution.

Everything is audited; folding is idempotent (re-running a scan is safe); signal-less rows
(``dedup_key IS NULL`` — nothing to group on) are exempt by design. Deployment note: this runs
against real data via the startup hook / admin button on the production machine — it must stay
conservative (contradictions untouched, per-group batching, loud logging).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.citation import CitationMention, Reference, ReferenceCitation
from app.models.work import Work
from app.services.audit import record_event
from app.services.reference_links import reference_dedup_key

logger = logging.getLogger(__name__)

# Suffix marking a row parked in the conflict-review queue. Appended to the canonical key so the
# canonical spelling stays free for the surviving row (and a unique index can land regardless).
CONFLICT_MARKER = "|conflict:"

# Matches Reference.dedup_key's column length; the suffix must always fit.
_MAX_KEY_LEN = 512

# Resolution-state precedence for the auto-fold ladder (higher wins).
_STATUS_RANK = {
    "confirmed_match": 5,
    "rejected_match": 4,
    "local_match": 3,
    "likely_match": 2,
    "external": 1,
    "unresolved": 0,
}

# Reference columns merged non-null-first when folding (survivor keeps its value when set).
_MERGE_FIELDS = ("title", "normalized_title", "doi", "arxiv_id", "year", "authors", "raw_citation")


@dataclass
class ConsolidationResult:
    """Outcome of one scan: what was folded and what needs a human."""

    groups_scanned: int = 0
    folded: int = 0  # duplicate rows removed by auto-folds
    conflicts: int = 0  # contradiction groups currently pending review (incl. pre-existing)
    conflict_keys: list[str] = field(default_factory=list)


def canonical_key(reference: Reference) -> str | None:
    """The reference's canonical dedup key, recomputed from its fields.

    Recomputing (instead of trusting the stored column) folds legacy key shapes and strips any
    ``|conflict:`` suffix in one move.
    """
    return reference_dedup_key(
        doi=reference.doi,
        arxiv_id=reference.arxiv_id,
        normalized_title=reference.normalized_title,
        year=reference.year,
    )


def _age_key(reference: Reference) -> tuple[float, str]:
    """Oldest-first sort key, tolerant of naive/aware created_at mixes (SQLite round-trips)."""
    created = reference.created_at
    if created is None:
        return (float("inf"), reference.id.hex)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (created.timestamp(), reference.id.hex)


def _conflict_key(canonical: str, reference_id: uuid.UUID) -> str:
    """A stable per-row parked key: canonical (truncated to fit) + ``|conflict:<id8>``."""
    suffix = f"{CONFLICT_MARKER}{reference_id.hex[:8]}"
    return canonical[: _MAX_KEY_LEN - len(suffix)] + suffix


def _is_contradiction(group: list[Reference]) -> bool:
    """True when two user-touched states in the group disagree (S13: never auto-fold these).

    Contradiction = two confirmations pointing at *different* works, or a confirmation of the
    very work that another twin's rejection explicitly refused. A rejection of some *other*
    candidate does not contradict a confirmation and stays auto-foldable.
    """
    confirmed_targets = {
        r.resolved_work_id
        for r in group
        if r.resolution_status == "confirmed_match" and r.resolved_work_id is not None
    }
    if len(confirmed_targets) > 1:
        return True
    rejected_targets = {
        r.suggested_work_id
        for r in group
        if r.resolution_status == "rejected_match" and r.suggested_work_id is not None
    }
    return bool(confirmed_targets & rejected_targets)


def _ladder_best(group: list[Reference]) -> Reference:
    """The row whose resolution state should survive an auto-fold."""
    return max(
        group,
        key=lambda r: (
            _STATUS_RANK.get(r.resolution_status, 0),
            r.match_score or 0.0,
            r.resolved_work_id is not None,
        ),
    )


def _fold_group(db: Session, group: list[Reference], winner: Reference) -> int:
    """Fold ``group`` into its oldest row, applying ``winner``'s resolution. Returns rows removed.

    Link repointing honors the ``(reference_id, citing_work_id)`` unique constraint: a citing work
    linked to both twins keeps one link. Mentions are repointed wholesale.
    """
    survivor = min(group, key=_age_key)
    losers = [r for r in group if r.id != survivor.id]

    survivor_citers = set(
        db.scalars(
            select(ReferenceCitation.citing_work_id).where(
                ReferenceCitation.reference_id == survivor.id
            )
        ).all()
    )
    for loser in losers:
        for link in db.scalars(
            select(ReferenceCitation).where(ReferenceCitation.reference_id == loser.id)
        ).all():
            if link.citing_work_id in survivor_citers:
                db.delete(link)  # the citing work already cites the survivor
            else:
                link.reference_id = survivor.id
                survivor_citers.add(link.citing_work_id)
        db.execute(
            update(CitationMention)
            .where(CitationMention.reference_id == loser.id)
            .values(reference_id=survivor.id)
        )
        for column in _MERGE_FIELDS:
            if getattr(survivor, column) in (None, "", []):
                value = getattr(loser, column)
                if value not in (None, "", []):
                    setattr(survivor, column, value)

    # The winning resolution state (may come from a loser row).
    survivor.resolved_work_id = winner.resolved_work_id
    survivor.suggested_work_id = winner.suggested_work_id
    survivor.match_score = winner.match_score
    survivor.resolution_status = winner.resolution_status
    survivor.dedup_key = canonical_key(survivor)

    removed_ids = [str(r.id) for r in losers]
    if losers:
        db.execute(delete(Reference).where(Reference.id.in_([r.id for r in losers])))
    db.flush()
    record_event(
        db,
        "reference.consolidated",
        entity_type="reference",
        entity_id=str(survivor.id),
        details={
            "dedup_key": survivor.dedup_key,
            "removed_reference_ids": removed_ids,
            "kept_status": survivor.resolution_status,
        },
    )
    return len(losers)


def consolidate_references(
    db: Session, *, actor_user_id: uuid.UUID | None = None
) -> ConsolidationResult:
    """Scan the whole references table, auto-fold safe duplicate groups, park contradictions.

    Idempotent: a rerun re-reports still-pending contradictions (they keep their parked keys) and
    folds nothing twice. Commits per group batch is the caller's concern — this function flushes
    only; the job wrapper commits.
    """
    result = ConsolidationResult()
    groups: dict[str, list[Reference]] = {}
    for reference in db.scalars(select(Reference)).all():
        key = canonical_key(reference)
        if key is None:
            continue  # signal-less row — nothing to group on (exempt by design)
        groups.setdefault(key, []).append(reference)

    for key, group in sorted(groups.items()):
        if len(group) < 2:
            # A single row may still carry a stale/legacy/parked-but-now-lonely key — refresh it.
            if group[0].dedup_key != key:
                group[0].dedup_key = key
            continue
        result.groups_scanned += 1
        if _is_contradiction(group):
            result.conflicts += 1
            result.conflict_keys.append(key)
            keeper = min(group, key=_age_key)
            if keeper.dedup_key != key:
                keeper.dedup_key = key
            newly_parked = []
            for row in group:
                if row.id == keeper.id:
                    continue
                parked = _conflict_key(key, row.id)
                if row.dedup_key != parked:
                    row.dedup_key = parked
                    newly_parked.append(str(row.id))
            if newly_parked:
                record_event(
                    db,
                    "reference.consolidation_conflict",
                    actor_user_id=actor_user_id,
                    entity_type="reference",
                    entity_id=str(keeper.id),
                    details={"dedup_key": key, "parked_reference_ids": newly_parked},
                )
            continue
        result.folded += _fold_group(db, group, _ladder_best(group))
    db.flush()
    return result


def list_conflicts(db: Session) -> list[dict]:
    """The pending contradiction groups, shaped for the Admin → Reference dupes tab.

    A group = the canonical-key row plus every ``|conflict:``-parked sibling, each with enough
    context (statuses, target-work titles, citing counts) for the admin to pick a winner.
    """
    parked = db.scalars(
        select(Reference).where(Reference.dedup_key.like(f"%{CONFLICT_MARKER}%"))
    ).all()
    if not parked:
        return []
    keys = {canonical_key(r) for r in parked if canonical_key(r)}
    groups: dict[str, list[Reference]] = {k: [] for k in keys}
    for reference in db.scalars(select(Reference)).all():
        key = canonical_key(reference)
        if key in groups:
            groups[key].append(reference)

    work_ids = set()
    for members in groups.values():
        for r in members:
            work_ids.update(w for w in (r.resolved_work_id, r.suggested_work_id) if w)
    titles = {
        w.id: w.canonical_title for w in db.scalars(select(Work).where(Work.id.in_(work_ids))).all()
    }
    citing_counts: dict[uuid.UUID, int] = {}
    member_ids = [r.id for members in groups.values() for r in members]
    for ref_id, _work_id in db.execute(
        select(ReferenceCitation.reference_id, ReferenceCitation.citing_work_id).where(
            ReferenceCitation.reference_id.in_(member_ids)
        )
    ):
        citing_counts[ref_id] = citing_counts.get(ref_id, 0) + 1

    out = []
    for key in sorted(groups):
        members = sorted(groups[key], key=_age_key)
        out.append(
            {
                "dedup_key": key,
                "references": [
                    {
                        "id": str(r.id),
                        "title": r.title,
                        "doi": r.doi,
                        "arxiv_id": r.arxiv_id,
                        "year": r.year,
                        "resolution_status": r.resolution_status,
                        "resolved_work_id": str(r.resolved_work_id) if r.resolved_work_id else None,
                        "resolved_work_title": titles.get(r.resolved_work_id),
                        "suggested_work_id": str(r.suggested_work_id)
                        if r.suggested_work_id
                        else None,
                        "suggested_work_title": titles.get(r.suggested_work_id),
                        "citing_count": citing_counts.get(r.id, 0),
                        "parked": CONFLICT_MARKER in (r.dedup_key or ""),
                    }
                    for r in members
                ],
            }
        )
    return out


def resolve_conflict(
    db: Session, *, winner_reference_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> int:
    """Fold a contradiction group using the admin-chosen row's resolution. Returns rows removed.

    The chosen row's state is applied verbatim to the surviving (oldest) row — including a chosen
    *rejection* — and locked semantics stay what they were (a confirmed winner stays confirmed).
    Raises ``NotFoundError``/``ConflictError`` for a missing row / a row not in any conflict group.
    """
    from app.errors import ConflictError, NotFoundError

    winner = db.get(Reference, winner_reference_id)
    if winner is None:
        raise NotFoundError("Reference not found")
    key = canonical_key(winner)
    if key is None:
        raise ConflictError("This reference has no dedup key — nothing to consolidate")
    group = [r for r in db.scalars(select(Reference)).all() if canonical_key(r) == key]
    if len(group) < 2 or not any(CONFLICT_MARKER in (r.dedup_key or "") for r in group):
        raise ConflictError("This reference is not part of a pending conflict group")
    removed = _fold_group(db, group, winner)
    record_event(
        db,
        "reference.conflict_resolved",
        actor_user_id=actor_user_id,
        entity_type="reference",
        entity_id=str(winner_reference_id),
        details={"dedup_key": key, "removed": removed},
    )
    db.flush()
    return removed
