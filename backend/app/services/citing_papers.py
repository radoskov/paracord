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

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.external_citation import ExternalCitation
from app.models.work import Work
from app.services.metadata_enrichment import (
    OPENALEX_API,
    SEMANTIC_SCHOLAR_API,
    _arxiv_base,
    _get,
    _idseg,
)

logger = logging.getLogger(__name__)

MAX_CITING = 100  # on-demand cap (owner decision)


@dataclass
class CitingPaper:
    source: str
    external_id: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    venue: str | None = None


def _bare_doi(value: str | None) -> str | None:
    if not value:
        return None
    return (
        value.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/").lower()
        or None
    )


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
                venue=paper.get("venue"),
            )
        )
    return out


# --- live fetch (OpenAlex → Semantic Scholar fallback) ----------------------


def _fetch_openalex_citing(doi: str, *, limit: int, mailto: str | None) -> list[CitingPaper]:
    params = {"mailto": mailto} if mailto else {}
    resolved = _get(f"{OPENALEX_API}/doi:{_idseg(doi)}", params=params)
    if resolved.status_code == 404:
        return []
    resolved.raise_for_status()
    short_id = _openalex_short_id(resolved.json().get("id"))
    if not short_id:
        return []
    page = _get(
        OPENALEX_API,
        params={
            "filter": f"cites:{short_id}",
            "per-page": min(limit, 200),
            "select": "id,display_name,publication_year,doi,authorships,primary_location",
            **({"mailto": mailto} if mailto else {}),
        },
    )
    page.raise_for_status()
    return parse_openalex_citing(page.json(), limit)


def _fetch_s2_citing(*, doi: str | None, arxiv: str | None, limit: int) -> list[CitingPaper]:
    if arxiv:
        identifier = f"arXiv:{_idseg(_arxiv_base(arxiv))}"
    elif doi:
        identifier = f"DOI:{_idseg(doi)}"
    else:
        return []
    resp = _get(
        f"{SEMANTIC_SCHOLAR_API}/{identifier}/citations",
        params={"fields": "title,year,authors,externalIds,venue", "limit": min(limit, 1000)},
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return parse_s2_citing(resp.json(), limit)


def fetch_citing_papers(
    *,
    doi: str | None,
    arxiv: str | None = None,
    limit: int = MAX_CITING,
    settings: Settings | None = None,
) -> tuple[list[CitingPaper], str | None]:
    """Fetch up to ``limit`` citing papers via OpenAlex, falling back to Semantic Scholar.

    Returns ``(papers, source)`` where ``source`` is the provider that answered, or ``None`` when
    neither could (no identifier, or both failed). Never raises for network/parse errors — a failed
    provider is logged and the fallback is tried.
    """
    settings = settings or get_settings()
    mailto = getattr(settings, "crossref_mailto", None) or None
    limit = max(1, min(limit, MAX_CITING))

    if doi:
        try:
            papers = _fetch_openalex_citing(doi, limit=limit, mailto=mailto)
            if papers:
                return papers, "openalex"
        except Exception as exc:  # noqa: BLE001 - try the fallback provider
            logger.warning("OpenAlex citing-papers fetch failed for doi %s: %s", doi, exc)

    if doi or arxiv:
        try:
            papers = _fetch_s2_citing(doi=doi, arxiv=arxiv, limit=limit)
            if papers:
                return papers, "semanticscholar"
        except Exception as exc:  # noqa: BLE001 - both providers failed
            logger.warning("Semantic Scholar citing-papers fetch failed: %s", exc)

    return [], None


def store_citing_papers(
    db: Session, *, work: Work, papers: list[CitingPaper], source: str
) -> list[ExternalCitation]:
    """Replace the work's cached citing papers with a fresh fetch. Caller commits."""
    db.execute(delete(ExternalCitation).where(ExternalCitation.work_id == work.id))
    now = datetime.now(UTC)
    rows = [
        ExternalCitation(
            work_id=work.id,
            source=source,
            external_id=p.external_id,
            title=p.title,
            authors="; ".join(p.authors) if p.authors else None,
            year=p.year,
            doi=p.doi,
            venue=p.venue,
            fetched_at=now,
        )
        for p in papers
    ]
    db.add_all(rows)
    return rows
