"""On-demand external-reference preview (Track C C1).

Given a bibliographic **identifier** (DOI or arXiv id) — never a URL or filesystem path — fetch a
compact metadata preview (title, authors, year, venue, abstract) from the same identifier-based
enrichment connectors used by :mod:`app.services.metadata_enrichment` (arXiv / Crossref / OpenAlex /
Semantic Scholar), so the user can decide before importing a cited-but-missing work.

Egress policy: identical to enrichment — only identifiers leave the server, percent-encoded into the
API path (the SSRF-hardened ``_get`` in :mod:`metadata_enrichment` refuses cross-host redirects). A
call with no identifier does no network I/O and returns ``None`` ("no preview available"). Sources
are queried in priority order and merged (first non-empty field wins); the pass stops early once the
core fields are filled to keep the identifier-only egress polite. Results are cached in-process for a
short TTL so repeated opens of the same reference do not re-hit the upstream APIs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services.metadata_enrichment import (
    ExternalMetadata,
    fetch_arxiv,
    fetch_crossref_by_doi,
    fetch_openalex,
    fetch_semantic_scholar,
)
from app.utils.normalization import normalize_doi

logger = logging.getLogger(__name__)

# How long a fetched preview is served from the in-process cache before a re-fetch (seconds).
PREVIEW_TTL_SECONDS = 600

# key -> (expires_at_monotonic, preview_or_None). A cached ``None`` is a remembered miss.
_PREVIEW_CACHE: dict[str, tuple[float, ExternalPreview | None]] = {}


@dataclass
class ExternalPreview:
    """A compact, read-only metadata preview for an external (cited-but-missing) reference."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    sources: list[str] = field(default_factory=list)


def _cache_key(doi: str | None, arxiv_id: str | None) -> str:
    return f"doi:{doi or ''}|arxiv:{arxiv_id or ''}"


def _merge(preview: ExternalPreview, meta: ExternalMetadata) -> None:
    """Fill any still-empty preview field from ``meta`` (first non-empty source wins)."""
    if not preview.title and meta.title:
        preview.title = meta.title
    if not preview.authors and meta.authors:
        preview.authors = list(meta.authors)
    if preview.year is None and meta.year is not None:
        preview.year = meta.year
    if not preview.venue and meta.venue:
        preview.venue = meta.venue
    if not preview.abstract and meta.abstract:
        preview.abstract = meta.abstract
    if not preview.doi and meta.doi:
        preview.doi = meta.doi
    if not preview.arxiv_id and meta.arxiv_id:
        preview.arxiv_id = meta.arxiv_id
    preview.sources.append(meta.source)


def _is_complete(preview: ExternalPreview) -> bool:
    """Enough for a useful preview — stop querying further sources once these are filled."""
    return bool(preview.title and preview.abstract and preview.authors)


def external_preview(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    settings=None,
    arxiv_fetcher: Callable[..., ExternalMetadata | None] = fetch_arxiv,
    crossref_fetcher: Callable[..., ExternalMetadata | None] = fetch_crossref_by_doi,
    openalex_fetcher: Callable[..., ExternalMetadata | None] = fetch_openalex,
    semantic_scholar_fetcher: Callable[..., ExternalMetadata | None] = fetch_semantic_scholar,
) -> ExternalPreview | None:
    """Fetch (or serve from cache) a metadata preview for a DOI / arXiv id.

    Returns ``None`` when there is no identifier to query, when every source is unreachable/empty, or
    when a source raises (each source is guarded so one flaky API never aborts the preview). Fetchers
    are injectable so tests can supply a mocked connector.
    """
    doi = normalize_doi(doi) if doi else None
    arxiv_id = arxiv_id.strip() if arxiv_id else None
    if not doi and not arxiv_id:
        return None

    if settings is not None and not getattr(settings, "enrichment_enabled", True):
        return None

    key = _cache_key(doi, arxiv_id)
    now = time.monotonic()
    cached = _PREVIEW_CACHE.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    mailto = getattr(settings, "crossref_mailto", None) if settings is not None else None
    planned: list[tuple[str, Callable[[], ExternalMetadata | None]]] = []
    if arxiv_id:
        planned.append(("arxiv", lambda: arxiv_fetcher(arxiv_id)))
    if doi:
        planned.append(("crossref", lambda: crossref_fetcher(doi, mailto=mailto)))
        planned.append(("openalex", lambda: openalex_fetcher(doi, mailto=mailto)))
    if arxiv_id or doi:
        planned.append(
            ("semanticscholar", lambda: semantic_scholar_fetcher(arxiv_id=arxiv_id, doi=doi))
        )

    preview = ExternalPreview(doi=doi, arxiv_id=arxiv_id)
    for name, fetch in planned:
        try:
            meta = fetch()
        except Exception as exc:  # noqa: BLE001 - one failing source must not abort the preview
            logger.warning("external_preview source %s failed for %s: %s", name, key, exc)
            continue
        if meta is not None:
            _merge(preview, meta)
            if _is_complete(preview):
                break

    result = preview if preview.sources else None
    _PREVIEW_CACHE[key] = (now + PREVIEW_TTL_SECONDS, result)
    return result


__all__ = ["ExternalPreview", "PREVIEW_TTL_SECONDS", "external_preview"]
