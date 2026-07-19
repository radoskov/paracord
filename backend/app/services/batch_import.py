"""Batch citation import (Phase J item 5).

Turn a paste of raw citation strings / titles (one per line) into a reviewable set of drafts, then
(after the user confirms) into works — optionally added to a target shelf.

Two engines, NO new HTTP code:

  * ``lookup`` — per line, run the find-on-web search fetchers (Crossref + OpenAlex + Semantic
    Scholar) treating the whole line as the query title, then ``deduplicate`` + ``rank`` the
    results (reusing :mod:`app.services.web_find`). The top candidate prefills the draft when its
    score clears the configured match threshold. A whole-batch wall-clock budget bounds the
    fan-out; lines skipped for the budget degrade to ``title_only`` and set the ``degraded`` flag.
  * ``grobid`` — ONE ``/api/processCitationList`` call parses every line at once
    (:func:`app.services.tei_parser.parse_citation_list`). If GROBID is unreachable, every line
    degrades to ``title_only`` and the ``grobid_unavailable`` flag is set (never a hard failure).

``preview_lines`` performs NO DB writes. ``commit_drafts`` writes works (deduped via the shared
``bibliography_import._find_existing``), records an ``ImportBatch``, and — when a ``target_shelf_id``
is given — adds each work to that shelf through the shared ACL-checked helper.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.metadata import MetadataAssertion
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services import web_find
from app.services.audit import record_event
from app.services.bibliography_import import _find_existing
from app.services.default_shelf import place_on_default_if_loose
from app.services.grobid_client import GrobidClient, GrobidUnavailableError
from app.services.identifiers import arxiv_base_id
from app.services.shelf_membership import add_work_to_shelf_checked
from app.services.tei_parser import parse_citation_list
from app.utils.normalization import normalize_doi, normalize_title
from app.workers.queue import enqueue_embedding, enqueue_enrichment

logger = logging.getLogger(__name__)

EngineKind = Literal["lookup", "grobid", "bibtex", "identifier"]
MatchStatus = Literal["matched", "title_only", "no_match"]


@dataclass
class DraftCandidate:
    """A ranked candidate (projection of :class:`web_find.WebCandidate`) for a single line."""

    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    venue: str | None
    source: str
    sources: list[str]
    confidence: float


@dataclass
class ParsedDraft:
    """One reviewable line: the suggested metadata + the candidates we found for it."""

    line_index: int
    raw_line: str
    engine: EngineKind
    suggested_title: str | None
    suggested_authors: list[str]
    suggested_year: int | None
    suggested_doi: str | None
    suggested_venue: str | None
    suggested_abstract: str | None
    match_status: MatchStatus
    candidates: list[DraftCandidate] = field(default_factory=list)
    # BibTeX-engine extras: identifiers/type parsed from the entry ride along so the preview→commit
    # round-trip doesn't lose them, and an already-in-library hit is flagged for the reviewer.
    suggested_arxiv_id: str | None = None
    suggested_work_type: str | None = None
    existing_work_id: uuid.UUID | None = None


@dataclass
class BatchPreview:
    drafts: list[ParsedDraft]
    degraded: bool = False
    grobid_unavailable: bool = False


@dataclass
class ConfirmedDraft:
    """A draft the user confirmed for commit (the editable fields + include flag)."""

    title: str | None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    abstract: str | None = None
    include: bool = True
    # BibTeX-engine passthrough (not user-editable in the review UI).
    arxiv_id: str | None = None
    work_type: str | None = None
    # Optional per-draft shelf override; falls back to the commit's global target_shelf_id.
    target_shelf_id: uuid.UUID | None = None


def clean_lines(lines: list[str], *, settings: Settings) -> list[str]:
    """Strip/blank-filter the input and cap it at ``web_find_batch_max_lines``."""
    cap = int(getattr(settings, "web_find_batch_max_lines", 200))
    cleaned = [stripped for raw in lines if (stripped := raw.strip())]
    return cleaned[:cap]


# A DOI anywhere in the line (optionally prefixed with "doi:"); the captured group is the bare DOI.
_DOI_IN_TEXT = re.compile(r"(?:doi:\s*)?(10\.\d{4,9}/[^\s,;)\]]+)", re.IGNORECASE)
# A parenthesized 4-digit year, e.g. "(2021)" — the common "Title (Year)" citation shape.
_YEAR_PAREN = re.compile(r"\((1[5-9]\d{2}|20\d{2})\)")


def _augment_citation_fields(
    raw_line: str, *, title: str | None, year: int | None, doi: str | None
) -> tuple[str, int | None, str | None]:
    """Recover the year + DOI that a citation string carries explicitly (``Title (2021) doi:10.…``)
    but the reference parser often leaves stuffed in the title.

    Fills ``year``/``doi`` from the raw line only when the parser didn't, then strips a DOI token and
    a parenthesized year out of the title (a real title has neither) so the title field isn't the
    whole raw string. Conservative: it only removes what it recognizes, so ordinary titles are
    untouched.
    """
    title = (title or raw_line).strip()
    if not doi:
        m = _DOI_IN_TEXT.search(raw_line)
        if m:
            doi = m.group(1).rstrip(".,;)]}")
    if not year:
        ym = _YEAR_PAREN.search(raw_line)
        if ym:
            year = int(ym.group(1))
    # Tidy the title: drop any DOI token (with/without a "doi:" prefix) and a parenthesized year.
    cleaned = _DOI_IN_TEXT.sub("", title)
    cleaned = re.sub(r"\bdoi:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = _YEAR_PAREN.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,;–-")
    if cleaned:
        title = cleaned
    return title, year, doi


def _candidate_from_web(cand: web_find.WebCandidate) -> DraftCandidate:
    return DraftCandidate(
        title=cand.title,
        authors=list(cand.authors),
        year=cand.year,
        doi=cand.doi,
        venue=None,  # web_find candidates do not surface a venue
        source=cand.source,
        sources=list(cand.sources),
        confidence=cand.score,
    )


def _lookup_one_line(
    line: str, *, settings: Settings, fetchers: dict | None
) -> list[DraftCandidate]:
    """Search the find-on-web fetchers for one line and return ranked candidates.

    Treats the whole line as the query title (no authors/year). Reuses web_find's
    search_* + deduplicate + rank so we never duplicate the HTTP layer. ``fetchers`` lets tests
    inject per-source callables that take ``(title, authors, year)``.
    """
    fetchers = fetchers or {}
    mailto = getattr(settings, "crossref_mailto", None)
    max_candidates = int(getattr(settings, "web_find_max_candidates", 10))
    registry = {
        "crossref": fetchers.get(
            "crossref", lambda: web_find.search_crossref(line, [], None, mailto=mailto)
        ),
        "openalex": fetchers.get(
            "openalex", lambda: web_find.search_openalex(line, [], None, mailto=mailto)
        ),
        "semanticscholar": fetchers.get(
            "semanticscholar", lambda: web_find.search_semantic_scholar(line, [], None)
        ),
    }
    collected: list[web_find.WebCandidate] = []
    for name, runner in registry.items():
        try:
            collected.extend(runner() or [])
        except Exception as exc:  # noqa: BLE001 - one source must never abort a line
            logger.warning("batch-import lookup source %s failed: %s", name, exc)
    deduped = web_find.deduplicate(collected)
    ranked = web_find.rank(
        deduped,
        query_title=line,
        query_year=None,
        query_authors=[],
        max_candidates=max_candidates,
    )
    return [_candidate_from_web(c) for c in ranked]


def _preview_lookup(lines: list[str], *, settings: Settings, fetchers: dict | None) -> BatchPreview:
    """Run ``_lookup_one_line`` per line, bounded by a whole-batch wall-clock budget.

    A line whose candidates' top score clears ``web_find_batch_match_threshold`` becomes a
    "matched" draft prefilled from that candidate; otherwise (or once the budget is exhausted)
    the draft degrades to "title_only" (and sets ``degraded`` on the batch when the budget cut it
    short) rather than failing.
    """
    threshold = float(getattr(settings, "web_find_batch_match_threshold", 0.6))
    total_budget = float(getattr(settings, "web_find_total_budget", 120.0))
    drafts: list[ParsedDraft] = []
    degraded = False
    start = time.monotonic()
    for index, line in enumerate(lines):
        budget_exhausted = (time.monotonic() - start) > total_budget
        candidates: list[DraftCandidate] = []
        if budget_exhausted:
            degraded = True
        else:
            candidates = _lookup_one_line(line, settings=settings, fetchers=fetchers)
        top = candidates[0] if candidates else None
        if top is not None and top.confidence >= threshold:
            drafts.append(
                ParsedDraft(
                    line_index=index,
                    raw_line=line,
                    engine="lookup",
                    suggested_title=top.title or line,
                    suggested_authors=list(top.authors),
                    suggested_year=top.year,
                    suggested_doi=top.doi,
                    suggested_venue=top.venue,
                    suggested_abstract=None,
                    match_status="matched",
                    candidates=candidates,
                )
            )
        else:
            drafts.append(
                ParsedDraft(
                    line_index=index,
                    raw_line=line,
                    engine="lookup",
                    suggested_title=line,
                    suggested_authors=[],
                    suggested_year=None,
                    suggested_doi=None,
                    suggested_venue=None,
                    suggested_abstract=None,
                    match_status="title_only",
                    candidates=candidates,
                )
            )
    return BatchPreview(drafts=drafts, degraded=degraded)


def _preview_grobid(
    lines: list[str], *, settings: Settings, grobid: GrobidClient | None
) -> BatchPreview:
    """Parse every line in one ``processCitationList`` call and build a draft per line.

    A line resolved to a title becomes "matched"; a line GROBID couldn't extract a title for (or,
    when GROBID itself is unreachable, every line) degrades to "title_only" rather than failing
    the whole batch.
    """
    client = grobid or GrobidClient(settings.grobid_url, settings=settings)
    try:
        tei_xml = client.process_citation_list_sync(lines)
    except GrobidUnavailableError:
        # Degrade every line to title_only rather than failing the whole batch — but still salvage an
        # explicit DOI/year from the raw line so a "Title (2021) doi:10.…" line isn't wholly a title.
        drafts = []
        for index, line in enumerate(lines):
            title, year, doi = _augment_citation_fields(line, title=None, year=None, doi=None)
            drafts.append(
                ParsedDraft(
                    line_index=index,
                    raw_line=line,
                    engine="grobid",
                    suggested_title=title,
                    suggested_authors=[],
                    suggested_year=year,
                    suggested_doi=doi,
                    suggested_venue=None,
                    suggested_abstract=None,
                    match_status="matched" if doi else "title_only",
                )
            )
        return BatchPreview(drafts=drafts, grobid_unavailable=True)

    references = parse_citation_list(tei_xml)
    drafts = []
    for index, line in enumerate(lines):
        ref = references[index] if index < len(references) else None
        grobid_title = (ref.title if ref else None) or None
        # Recover a DOI/year the parser left in the raw string and clean them out of the title
        # (fixes "Title (2021) doi:10.…" landing whole in the title with no year/doi).
        title, year, doi = _augment_citation_fields(
            line,
            title=grobid_title,
            year=ref.year if ref else None,
            doi=ref.doi if ref else None,
        )
        drafts.append(
            ParsedDraft(
                line_index=index,
                raw_line=line,
                engine="grobid",
                suggested_title=title,
                suggested_authors=list(ref.authors) if ref else [],
                suggested_year=year,
                suggested_doi=doi,
                suggested_venue=ref.venue if ref else None,
                suggested_abstract=None,
                # Matched when the parser found a title OR we salvaged a DOI from the raw line.
                match_status="matched" if (grobid_title or doi) else "title_only",
            )
        )
    return BatchPreview(drafts=drafts)


def preview_lines(
    lines: list[str],
    *,
    engine: EngineKind,
    settings: Settings,
    fetchers: dict | None = None,
    grobid: GrobidClient | None = None,
) -> BatchPreview:
    """Turn raw lines into reviewable drafts. NO DB writes.

    ``engine="lookup"`` searches the find-on-web sources per line (bounded by the web_find
    wall-clock budget); ``engine="grobid"`` parses all lines in one ``processCitationList`` call.
    Tests inject ``fetchers`` (per-source callables) / ``grobid`` (a client) to avoid real network.
    """
    cleaned = clean_lines(lines, settings=settings)
    if not cleaned:
        return BatchPreview(drafts=[])
    if engine == "grobid":
        return _preview_grobid(cleaned, settings=settings, grobid=grobid)
    return _preview_lookup(cleaned, settings=settings, fetchers=fetchers)


def commit_drafts(
    db: Session,
    drafts: list[ConfirmedDraft],
    *,
    actor: User,
    engine: EngineKind,
    target_shelf_id: uuid.UUID | None = None,
    enrich: bool = True,
    settings: Settings | None = None,
) -> ImportBatch:
    """Create works from confirmed drafts (deduped) and record an ImportBatch.

    Per included draft: dedup via the shared ``_find_existing`` (DOI then normalized title); a match
    is counted and (if ``target_shelf_id``) added to the shelf; otherwise a new ``Work`` is created
    with ``created_by_user_id=actor.id``, an authors ``MetadataAssertion``, an embedding enqueue, and
    (when ``enrich`` and the work has a DOI) an enrichment enqueue — then added to the target shelf.

    If ``target_shelf_id`` is set, the FIRST add goes through ``add_work_to_shelf_checked`` so a
    missing shelf (404) / lack of modify access (403) aborts the whole commit before any partial
    work — the ACL is enforced in exactly one place. Does NOT commit the transaction.
    """
    from app.services.app_config import enforce_batch_limit

    enforce_batch_limit(db, len(drafts))
    input_type = f"batch_{engine}"
    event_type = f"import.batch_{engine}"
    created = 0
    matched = 0
    skipped = 0
    added_to_shelf = 0
    now = datetime.now(UTC)
    # Create the batch up front so each newly minted work can carry its import_batch_id (Phase B6);
    # stats are finalized after the loop.
    batch = ImportBatch(
        created_by_user_id=actor.id,
        input_type=input_type,
        status="running",
        started_at=now,
    )
    db.add(batch)
    db.flush()

    def _add_to_shelf(work_id: uuid.UUID, per_item_shelf_id: uuid.UUID | None = None) -> None:
        nonlocal added_to_shelf
        effective = per_item_shelf_id or target_shelf_id
        if effective is None:
            # No explicit shelf → keep it off the floor by placing loose papers on the default (#1).
            place_on_default_if_loose(db, work_id, actor_id=actor.id)
            return
        add_work_to_shelf_checked(
            db,
            shelf_id=effective,
            work_id=work_id,
            actor=actor,
            settings=settings,
        )
        added_to_shelf += 1

    for draft in drafts:
        if not draft.include:
            skipped += 1
            continue
        title = (draft.title or "").strip()
        if not title:
            skipped += 1
            continue
        doi = normalize_doi(draft.doi) if draft.doi else None
        normalized = normalize_title(title)
        existing = _find_existing(db, doi=doi, normalized_title=normalized)
        if existing is not None:
            matched += 1
            _add_to_shelf(existing.id, draft.target_shelf_id)
            continue
        work = Work(
            canonical_title=title,
            normalized_title=normalized,
            year=draft.year,
            doi=doi,
            venue=draft.venue,
            abstract=draft.abstract,
            canonical_metadata_source=input_type,
            created_by_user_id=actor.id,
            import_batch_id=batch.id,
        )
        if draft.arxiv_id:
            work.arxiv_id = draft.arxiv_id
            work.arxiv_base_id = arxiv_base_id(draft.arxiv_id)
        if draft.work_type:
            work.work_type = draft.work_type
        db.add(work)
        db.flush()
        if draft.authors:
            db.add(
                MetadataAssertion(
                    entity_type="work",
                    entity_id=work.id,
                    field_name="authors",
                    value="; ".join(draft.authors),
                    source=input_type,
                    confidence=1.0,
                    selected_as_canonical=True,
                )
            )
        created += 1
        enqueue_embedding(work.id)
        if enrich and doi:
            enqueue_enrichment(work.id)
        _add_to_shelf(work.id, draft.target_shelf_id)

    stats = {
        "lines": len(drafts),
        "created": created,
        "matched": matched,
        "skipped": skipped,
        "added_to_shelf": added_to_shelf,
    }
    if target_shelf_id is not None:
        stats["target_shelf_id"] = str(target_shelf_id)
    batch.status = "completed"
    batch.stats = stats
    batch.finished_at = now
    db.flush()
    record_event(
        db,
        event_type,
        actor_user_id=actor.id,
        entity_type="import_batch",
        entity_id=str(batch.id),
        details=stats,
    )
    return batch
