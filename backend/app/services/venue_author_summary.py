"""Venue + author aggregation over a citation-summary scope (batch 10, issue 7).

Answers "where are these papers typically published?" (venues) and "who are the most common
authors?" for the same scope the citation summary uses (library / shelf / rack / search result /
selected papers / saved filter). Read-only; reuses the SEE-clamped ``_scope_works`` resolver so a
hidden paper never contributes.

Duplicate/near-duplicate grouping is intentionally *basic* and dependency-free:
* venues group case- and punctuation-insensitively (``NeurIPS`` == ``Neurips`` == ``neurips``);
* authors group by last name + first initial (``Vaswani, A.`` == ``Ashish Vaswani``).
Abbreviation↔full-name matching (``NeurIPS`` vs ``Neural Information Processing Systems``) is out of
scope here; each surviving spelling is surfaced as a ``variants`` list so the user can see the merge.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.user import User
from app.services import access
from app.services.citation_graph import _scope_works
from app.services.citation_summary import MAX_NODES, SummaryScope
from app.services.export_service import authors_by_work

DEFAULT_LIMIT = 20


@dataclass
class VenueStat:
    name: str
    count: int
    pct: float
    year_min: int | None = None
    year_max: int | None = None
    variants: list[str] = field(default_factory=list)


@dataclass
class AuthorStat:
    name: str
    count: int
    pct: float
    variants: list[str] = field(default_factory=list)


@dataclass
class VenueAuthorSummary:
    scope_work_count: int
    venues: list[VenueStat] = field(default_factory=list)
    authors: list[AuthorStat] = field(default_factory=list)
    papers_without_venue: int = 0
    papers_without_authors: int = 0
    distinct_venue_count: int = 0
    distinct_author_count: int = 0
    notes: list[str] = field(default_factory=list)


def _venue_key(venue: str) -> str:
    """Case/punctuation-insensitive key so trivially-different spellings group together."""
    return re.sub(r"[^a-z0-9]+", " ", venue.lower()).strip()


def _author_key(name: str) -> str:
    """Group key for an author: ``lastname firstinitial`` (handles "Last, First" and "First Last")."""
    n = name.strip()
    if not n:
        return ""
    if "," in n:
        last, _, rest = n.partition(",")
        first = rest.strip()
    else:
        parts = n.split()
        last = parts[-1] if parts else n
        first = parts[0] if len(parts) > 1 else ""
    last_k = re.sub(r"[^a-z]", "", last.lower())
    first_i = re.sub(r"[^a-z]", "", first.lower())[:1]
    return f"{last_k} {first_i}".strip()


def _representative(originals: Counter[str]) -> str:
    """The most common original spelling (ties broken by the longer, then alphabetical form)."""
    return max(originals.items(), key=lambda kv: (kv[1], len(kv[0]), kv[0]))[0]


def venue_author_summary(
    db: Session,
    actor: User,
    scope: SummaryScope,
    *,
    limit: int = DEFAULT_LIMIT,
) -> VenueAuthorSummary:
    """Aggregate venues and authors across the (SEE-clamped) scope. Read-only."""
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
            f"Analyzed {MAX_NODES} of {total} papers (node cap); refine the scope for the rest."
        )
    if not works:
        return VenueAuthorSummary(scope_work_count=0, notes=["No papers in this scope."])

    # --- Venues (from Work.venue) ---
    venue_counts: Counter[str] = Counter()
    venue_originals: dict[str, Counter[str]] = defaultdict(Counter)
    venue_years: dict[str, list[int]] = defaultdict(list)
    papers_without_venue = 0
    for work in works:
        venue = (work.venue or "").strip()
        if not venue:
            papers_without_venue += 1
            continue
        key = _venue_key(venue)
        if not key:
            papers_without_venue += 1
            continue
        venue_counts[key] += 1
        venue_originals[key][venue] += 1
        if work.year:
            venue_years[key].append(work.year)

    venues: list[VenueStat] = []
    for key, count in venue_counts.most_common(limit):
        years = venue_years.get(key, [])
        originals = venue_originals[key]
        venues.append(
            VenueStat(
                name=_representative(originals),
                count=count,
                pct=round(count / len(works) * 100, 1),
                year_min=min(years) if years else None,
                year_max=max(years) if years else None,
                variants=sorted(originals),
            )
        )

    # --- Authors (from the best 'authors' assertion per work) ---
    work_authors = authors_by_work(db, works)
    author_works: dict[str, set] = defaultdict(set)
    author_originals: dict[str, Counter[str]] = defaultdict(Counter)
    papers_without_authors = 0
    for work in works:
        names = work_authors.get(work.id, [])
        if not names:
            papers_without_authors += 1
            continue
        seen_keys: set[str] = set()
        for name in names:
            key = _author_key(name)
            if not key:
                continue
            author_originals[key][name] += 1
            if key not in seen_keys:  # count each paper once per author
                author_works[key].add(work.id)
                seen_keys.add(key)

    author_counts = Counter({key: len(ids) for key, ids in author_works.items()})
    authors: list[AuthorStat] = []
    for key, count in author_counts.most_common(limit):
        originals = author_originals[key]
        authors.append(
            AuthorStat(
                name=_representative(originals),
                count=count,
                pct=round(count / len(works) * 100, 1),
                variants=sorted(originals),
            )
        )

    return VenueAuthorSummary(
        scope_work_count=len(works),
        venues=venues,
        authors=authors,
        papers_without_venue=papers_without_venue,
        papers_without_authors=papers_without_authors,
        distinct_venue_count=len(venue_counts),
        distinct_author_count=len(author_counts),
        notes=notes,
    )
