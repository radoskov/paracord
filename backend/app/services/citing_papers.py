"""Fetch the external papers that CITE a work (incoming citations) — batch 10, issue 8.

Crossref exposes only a citation *count*, so the citing-papers list comes from OpenAlex
(``filter=cites:<openalex-id>``) with a Semantic Scholar fallback (``/paper/{id}/citations``). Both
are open (no key) and reached through the SSRF-hardened ``_get`` helper. On-demand + capped; the
result replaces the work's cached ``ExternalCitation`` rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.external_citation import ExternalCitationLink, ExternalPaper
from app.models.work import Work
from app.services.metadata_enrichment import (
    OPENALEX_API,
    SEMANTIC_SCHOLAR_API,
    _arxiv_base,
    _get,
    _idseg,
)
from app.services.reference_matching import (
    MatchFields,
    _relaxed_blocking_key,
    _work_arxiv_base,
    find_reference_match,
)
from app.utils.normalization import arxiv_base_from_doi, normalize_doi, normalize_title

logger = logging.getLogger(__name__)

# Default per-paper cap on fetched citing papers. The runtime value comes from
# app_config.citing_papers_fetch_cap (Admin → Settings, S20); this constant is the fallback for
# direct service calls and the parser default.
MAX_CITING = 1000


@dataclass
class CitingPaper:
    source: str
    external_id: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None


def _bare_doi(value: str | None) -> str | None:
    """Canonical normalization — must agree with ``normalize_doi`` or the same paper dedups to two
    ``ExternalPaper`` rows depending on which decoration the provider returned."""
    if not value:
        return None
    return normalize_doi(value) or None


def _openalex_short_id(full_id: str | None) -> str | None:
    """Extract the bare OpenAlex work id (``W…``) from ``https://openalex.org/W…``."""
    if not full_id:
        return None
    return full_id.rstrip("/").rsplit("/", 1)[-1] or None


# --- pure parsers (unit-tested against fixtures) ----------------------------


def parse_openalex_citing(payload: dict, limit: int = MAX_CITING) -> list[CitingPaper]:
    """Parse an OpenAlex ``/works?filter=cites:`` page into CitingPaper rows."""
    out: list[CitingPaper] = []
    for work in (payload.get("results") or [])[:limit]:
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in (work.get("authorships") or [])
            if (a.get("author") or {}).get("display_name")
        ]
        source_obj = (work.get("primary_location") or {}).get("source") or {}
        out.append(
            CitingPaper(
                source="openalex",
                external_id=_openalex_short_id(work.get("id")),
                title=work.get("display_name") or work.get("title"),
                authors=authors,
                year=work.get("publication_year"),
                doi=_bare_doi(work.get("doi")),
                venue=source_obj.get("display_name"),
            )
        )
    return out


def parse_s2_citing(payload: dict, limit: int = MAX_CITING) -> list[CitingPaper]:
    """Parse a Semantic Scholar ``/paper/{id}/citations`` page into CitingPaper rows."""
    out: list[CitingPaper] = []
    for row in (payload.get("data") or [])[:limit]:
        paper = row.get("citingPaper") or {}
        ext = paper.get("externalIds") or {}
        authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]
        out.append(
            CitingPaper(
                source="semanticscholar",
                external_id=paper.get("paperId"),
                title=paper.get("title"),
                authors=authors,
                year=paper.get("year"),
                doi=_bare_doi(ext.get("DOI")),
                arxiv_id=ext.get("ArXiv") or None,
                venue=paper.get("venue"),
            )
        )
    return out


# --- live fetch (OpenAlex → Semantic Scholar fallback) ----------------------


def _fetch_openalex_citing(
    doi: str, *, limit: int, mailto: str | None
) -> tuple[list[CitingPaper], int | None, bool]:
    """Returns ``(papers, total, answered)`` — ``answered`` is True only when OpenAlex
    authoritatively listed this work's citers (even zero of them); a 404/unresolvable id is
    "did not answer", so the caller keeps any cached list (S12 three-outcome semantics)."""
    params = {"mailto": mailto} if mailto else {}
    resolved = _get(f"{OPENALEX_API}/doi:{_idseg(doi)}", params=params)
    if resolved.status_code == 404:
        return [], None, False
    resolved.raise_for_status()
    resolved_json = resolved.json()
    short_id = _openalex_short_id(resolved_json.get("id"))
    if not short_id:
        return [], None, False
    # Cursor-paged up to ``limit`` (S20): OpenAlex serves 200 per page; meta.next_cursor walks on.
    papers: list[CitingPaper] = []
    total: int | None = None
    cursor: str | None = "*"
    while cursor and len(papers) < limit:
        page = _get(
            OPENALEX_API,
            params={
                "filter": f"cites:{short_id}",
                "per-page": min(limit - len(papers), 200),
                "cursor": cursor,
                "select": "id,display_name,publication_year,doi,authorships,primary_location",
                **({"mailto": mailto} if mailto else {}),
            },
        )
        page.raise_for_status()
        page_json = page.json()
        meta = page_json.get("meta") or {}
        if total is None:
            total = meta.get("count")
        batch = parse_openalex_citing(page_json, limit - len(papers))
        papers.extend(batch)
        if not batch:
            break
        cursor = meta.get("next_cursor")
    if total is None:
        # The true total (may exceed the capped list): fall back to the work's own count.
        total = resolved_json.get("cited_by_count")
    return papers, total, True


def _fetch_s2_citing(
    *, doi: str | None, arxiv: str | None, limit: int
) -> tuple[list[CitingPaper], int | None, bool]:
    """Returns ``(papers, total, answered)`` — see ``_fetch_openalex_citing``."""
    if arxiv:
        identifier = f"arXiv:{_idseg(_arxiv_base(arxiv))}"
    elif doi:
        identifier = f"DOI:{_idseg(doi)}"
    else:
        return [], None, False
    # Offset-paged up to ``limit`` (S20): S2 serves at most 1000 per request; ``next`` walks on.
    papers: list[CitingPaper] = []
    offset = 0
    while len(papers) < limit:
        resp = _get(
            f"{SEMANTIC_SCHOLAR_API}/{identifier}/citations",
            params={
                "fields": "title,year,authors,externalIds,venue",
                "limit": min(limit - len(papers), 1000),
                "offset": offset,
            },
        )
        if resp.status_code == 404:
            return [], None, False
        resp.raise_for_status()
        payload = resp.json()
        batch = parse_s2_citing(payload, limit - len(papers))
        papers.extend(batch)
        next_offset = payload.get("next")
        if not batch or next_offset is None:
            break
        offset = next_offset
    # The citations page carries no total; one cheap follow-up fetches the count snapshot.
    total: int | None = None
    try:
        count_resp = _get(
            f"{SEMANTIC_SCHOLAR_API}/{identifier}", params={"fields": "citationCount"}
        )
        if count_resp.status_code == 200:
            raw = count_resp.json().get("citationCount")
            total = int(raw) if raw is not None else None
    except Exception as exc:  # noqa: BLE001 - the count is a bonus, never fail the list for it
        logger.debug("Semantic Scholar citationCount fetch failed: %s", exc)
    return papers, total, True


def fetch_citing_papers(
    *,
    doi: str | None,
    arxiv: str | None = None,
    limit: int = MAX_CITING,
    settings: Settings | None = None,
) -> tuple[list[CitingPaper], str | None, int | None]:
    """Fetch up to ``limit`` citing papers via OpenAlex, falling back to Semantic Scholar.

    Returns ``(papers, source, total)``. Three outcomes (S12):

    * a provider listed ≥1 citers → its papers/source/total;
    * a provider **authoritatively answered zero** (HTTP 200, empty citers page) and the other had
      nothing better → ``([], source, total-or-0)`` — the caller must replace the cache with empty;
    * no provider answered (no identifier, 404s, network failures) → ``([], None, None)`` — the
      caller must keep whatever it has cached and surface the failure.

    Never raises for network/parse errors — a failed provider is logged and the fallback is tried.
    """
    settings = settings or get_settings()
    mailto = getattr(settings, "crossref_mailto", None) or None
    limit = max(1, limit)  # the cap itself is the caller's (admin-configured) choice — S20

    empty_answer: tuple[str, int | None] | None = None
    if doi:
        try:
            papers, total, answered = _fetch_openalex_citing(doi, limit=limit, mailto=mailto)
            if papers:
                return papers, "openalex", total
            if answered:
                # Authoritative zero — remember it, but still give S2 a chance to know better.
                empty_answer = ("openalex", total)
        except Exception as exc:  # noqa: BLE001 - try the fallback provider
            logger.warning("OpenAlex citing-papers fetch failed for doi %s: %s", doi, exc)

    if doi or arxiv:
        try:
            papers, total, answered = _fetch_s2_citing(doi=doi, arxiv=arxiv, limit=limit)
            if papers:
                return papers, "semanticscholar", total
            if answered and empty_answer is None:
                empty_answer = ("semanticscholar", total)
        except Exception as exc:  # noqa: BLE001 - both providers failed
            logger.warning("Semantic Scholar citing-papers fetch failed: %s", exc)

    if empty_answer is not None:
        source, total = empty_answer
        return [], source, total if total is not None else 0
    return [], None, None


def _dedup_key(paper: CitingPaper) -> str:
    """Stable identity for deduping an external paper: normalized DOI, else source:external_id."""
    if paper.doi:
        return f"doi:{paper.doi.strip().lower()}"
    return f"{paper.source}:{paper.external_id or ''}"


def _upsert_external_paper(
    db: Session, paper: CitingPaper, key: str, now: datetime
) -> ExternalPaper:
    """Fetch-or-create the deduplicated ExternalPaper for ``key``, refreshing its metadata."""
    existing = db.scalar(select(ExternalPaper).where(ExternalPaper.dedup_key == key))
    authors = "; ".join(paper.authors) if paper.authors else None
    if existing is not None:
        # Refresh metadata from the newer fetch (prefer non-empty values).
        existing.title = paper.title or existing.title
        existing.authors = authors or existing.authors
        existing.year = paper.year if paper.year is not None else existing.year
        existing.venue = paper.venue or existing.venue
        existing.doi = paper.doi or existing.doi
        existing.arxiv_id = paper.arxiv_id or existing.arxiv_id
        existing.updated_at = now
        return existing
    created = ExternalPaper(
        dedup_key=key,
        source=paper.source,
        external_id=paper.external_id,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        title=paper.title,
        authors=authors,
        year=paper.year,
        venue=paper.venue,
        first_seen_at=now,
        updated_at=now,
    )
    db.add(created)
    db.flush()
    return created


def _external_match_fields(external: ExternalPaper) -> MatchFields:
    """Adapt an ExternalPaper row to the shared matcher input (authors are a "; "-joined string)."""
    return MatchFields(
        title=external.title,
        normalized_title=normalize_title(external.title) if external.title else None,
        doi=external.doi,
        arxiv_id=external.arxiv_id,
        year=external.year,
        authors=[name.strip() for name in (external.authors or "").split(";") if name.strip()]
        or None,
    )


def resolve_external_paper(
    db: Session,
    external: ExternalPaper,
    *,
    exclude_work_id=None,
    candidate_works: list[Work] | None = None,
    settings: Settings | None = None,
    author_names=None,
    clear_on_miss: bool | None = None,
) -> bool:
    """Run the local matcher for one external citing paper; returns whether the link changed.

    Same algorithm and precision gates as reference→work matching, in the incoming direction: a
    citing paper that IS a library work gets ``resolved_work_id`` set so the UI/graph can show it as
    local. ``exclude_work_id`` guards against degenerate self-matches (a paper can't cite itself —
    provider glitches would otherwise link the cited work as its own citer). No confirm/reject
    workflow here: the resolution is recomputed on every fetch/rescan, so it self-heals.
    """
    match = find_reference_match(
        db,
        _external_match_fields(external),
        settings=settings,
        candidate_works=candidate_works,
        author_names=author_names,
    )
    resolved = match.work_id if match is not None else None
    if resolved is not None:
        # A paper cannot cite itself: never resolve to a work this paper is recorded as citing.
        # During a fetch the link isn't inserted yet, so the caller passes the target work instead.
        excluded = (
            {exclude_work_id}
            if exclude_work_id is not None
            else set(
                db.scalars(
                    select(ExternalCitationLink.work_id).where(
                        ExternalCitationLink.external_paper_id == external.id
                    )
                ).all()
            )
        )
        if resolved in excluded:
            resolved = None
    if clear_on_miss is None:
        clear_on_miss = candidate_works is None
    if not clear_on_miss and resolved is None:
        # A targeted rescan (one new work) must not clear a resolution owned by a full match run;
        # the full rescan job passes clear_on_miss=True with its complete candidate set.
        return False
    if external.resolved_work_id == resolved:
        return False
    external.resolved_work_id = resolved
    return True


def rescan_external_papers_for_new_work(db: Session, work: Work) -> int:
    """Reverse-rescan for the incoming direction: link cached citing papers to a NEW library work.

    Mirrors ``rescan_references_for_new_work`` — when a work is created/extracted/enriched, only the
    unresolved external papers that could plausibly be it (same DOI/arXiv id, or sharing its title
    block) are re-run, against just that work.
    """
    settings = get_settings()
    if not settings.reference_matching_enabled:
        return 0

    conditions = []
    if work.doi:
        conditions.append(ExternalPaper.doi == normalize_doi(work.doi))
    base = _work_arxiv_base(work) or (arxiv_base_from_doi(work.doi) if work.doi else None)
    if base:
        conditions.append(ExternalPaper.arxiv_id.ilike(f"%{base}%"))
        conditions.append(ExternalPaper.doi == f"10.48550/arxiv.{base}")
    if work.normalized_title:
        # No stored normalized title on external papers — a coarse contains-filter on the work's
        # blocking token bounds the candidate set; the matcher applies the real gates per row.
        # The block token comes from normalize_title, so it is [a-z0-9]-only — no LIKE escaping.
        block = _relaxed_blocking_key(work.normalized_title)
        if block:
            conditions.append(func.lower(ExternalPaper.title).like(f"%{block}%"))
    if not conditions:
        return 0

    changed = 0
    for external in db.scalars(select(ExternalPaper).where(or_(*conditions))).all():
        if resolve_external_paper(db, external, candidate_works=[work], settings=settings):
            changed += 1
    return changed


def store_citing_papers(
    db: Session,
    *,
    work: Work,
    papers: list[CitingPaper],
    source: str,
    total: int | None = None,
) -> list[ExternalCitationLink]:
    """Replace the work's citing links with a fresh fetch, deduplicating external papers.

    Each citing paper is upserted into the shared ``external_papers`` table (so a paper that cites
    several works is stored once), matched against the library (in-library citers get
    ``resolved_work_id``), then linked to this work. Orphaned external papers (no remaining link
    after the replace) are removed. When the provider reported its full citation count (``total``),
    the work's cached citation-count snapshot is refreshed too, so the list and the count can't
    drift apart. Caller commits.
    """
    now = datetime.now(UTC)
    work.citing_fetched_at = now  # survives an empty (authoritative-zero) replace — S12
    work.citing_fetched_source = source
    if total is not None:
        work.citation_count = total
        work.citation_count_source = source
        work.citation_count_fetched_at = now
    # Remember which external papers this work linked to, to GC any that become orphaned.
    prev_paper_ids = set(
        db.scalars(
            select(ExternalCitationLink.external_paper_id).where(
                ExternalCitationLink.work_id == work.id
            )
        ).all()
    )
    db.execute(delete(ExternalCitationLink).where(ExternalCitationLink.work_id == work.id))

    links: list[ExternalCitationLink] = []
    seen_keys: set[str] = set()
    for paper in papers:
        key = _dedup_key(paper)
        if key in seen_keys:  # same paper listed twice in one fetch → link once
            continue
        seen_keys.add(key)
        external = _upsert_external_paper(db, paper, key, now)
        resolve_external_paper(db, external, exclude_work_id=work.id)
        link = ExternalCitationLink(external_paper_id=external.id, work_id=work.id, fetched_at=now)
        db.add(link)
        links.append(link)

    db.flush()
    # GC: drop previously-linked external papers that now have no links at all.
    for paper_id in prev_paper_ids:
        still_linked = db.scalar(
            select(ExternalCitationLink.id).where(
                ExternalCitationLink.external_paper_id == paper_id
            )
        )
        if still_linked is None:
            db.execute(delete(ExternalPaper).where(ExternalPaper.id == paper_id))
    return links
