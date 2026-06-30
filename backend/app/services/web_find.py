"""Find-on-web: aggregate candidate matches for a paper from legitimate scholarly sources.

Legitimacy policy (REQUIRED — do not relax)
--------------------------------------------
This module ONLY queries legitimate, openly-documented scholarly sources for bibliographic
metadata and open-access PDF links:

  * Crossref + OpenAlex          — metadata
  * arXiv + Unpaywall + Semantic Scholar — open-access PDF links
  * the resolved publisher landing/PDF URL those sources surface

Shadow libraries (Sci-Hub, LibGen, Library.lol, Z-Library, Anna's Archive, …) are NEVER a
source and are HARD-REFUSED on every redirect hop of every download (see ``DENIED_HOSTS`` /
``_is_denied_host``). Publisher PDFs are fetched server-side, so any IP-based institutional
access the host network has applies — but we never circumvent a paywall through an illicit
mirror. If a host serves an HTML login/paywall instead of a PDF, the download falls back to
manual upload; nothing is stored.

SSRF hardening reuses :mod:`app.services.metadata_enrichment`'s ``_get`` (same-host-redirect
refusal) for the read-only search egress. The download path additionally validates the URL
against the set the search actually surfaced and re-checks the denylist on every hop.

Download-host allowlist policy (batch 2 #5 hardening — REQUIRED)
---------------------------------------------------------------
A download is permitted only when, in addition to being surfaced-by-search and passing the
%PDF/size validation, its final (post-redirect) host is on a POSITIVE allowlist:

    final host ∈ (DEFAULT_ALLOWED_HOSTS ∪ DB-managed additional hosts)   AND
    no hop (incl. the final host) is on the shadow-library denylist.

``DEFAULT_ALLOWED_HOSTS`` below is a conservative, built-in set of well-known, safe open-access
hosts (arXiv, Unpaywall, OpenAlex, Semantic Scholar, DOI resolver, PubMed Central, Europe PMC,
the bio/medRxiv preprint servers, Zenodo, DOAJ, …) plus the OA-PDF hosts the existing fetchers
surface. Owners/admins may extend the allowlist with additional hosts at runtime (stored in the
``web_find_allowed_hosts`` table and merged in via :func:`app.services.web_find_allowed_hosts.\
merged_allowed_hosts`). The DENYLIST ALWAYS WINS: a host on the denylist is refused even if it
was somehow added to the allowlist.

Allowlist entries (defaults and DB rows alike) match the request host with the same suffix-aware
logic as the denylist (see :func:`_host_matches`): an exact host, a parent-domain suffix
(``arxiv.org`` also matches ``export.arxiv.org``), or an explicit ``*.example.org`` wildcard form.
"""

import difflib
import hashlib
import inspect
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx2 as httpx
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.audit import record_event
from app.services.metadata_enrichment import (
    ATOM_NS,
    _get,
    _idseg,
)
from app.services.storage import attach_uploaded_pdf_to_work
from app.utils.normalization import normalize_doi, normalize_title
from app.workers.queue import enqueue_extraction

logger = logging.getLogger(__name__)

CROSSREF_SEARCH_API = "https://api.crossref.org/works"
OPENALEX_SEARCH_API = "https://api.openalex.org/works"
ARXIV_SEARCH_API = "https://export.arxiv.org/api/query"
UNPAYWALL_API = "https://api.unpaywall.org/v2"
SEMANTIC_SCHOLAR_SEARCH_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_SEARCH_FIELDS = "title,year,authors,externalIds,openAccessPdf,isOpenAccess"

# Shadow-library denylist. Matched against the registrable host (suffix match), so e.g.
# "sci-hub.se", "sci-hub.ru", "www.sci-hub.st" are all refused. NEVER remove an entry.
DENIED_HOST_SUFFIXES = (
    "sci-hub",  # sci-hub.* (any TLD)
    "scihub",
    "libgen",  # libgen.* (any TLD)
    "library.lol",
    "z-lib",  # z-lib.* / z-library
    "zlibrary",
    "annas-archive",  # annas-archive.* (any TLD)
    "booksc",
    "bookfi",
    "b-ok",
    "1lib",
    "gen.lib.rus.ec",
)

# Built-in default download-host allowlist (batch 2 #5 hardening). Conservative but practical:
# well-known, safe open-access hosts plus the OA-PDF hosts the existing search fetchers surface.
# Entries match suffix-aware (see ``_host_matches``): a bare host also matches its subdomains, so
# e.g. "arxiv.org" covers "export.arxiv.org" and "openalex.org" covers "api.openalex.org". An
# owner/admin may extend this set at runtime via the ``web_find_allowed_hosts`` table. NEVER add a
# host that the denylist would also match — the denylist always wins regardless.
DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        "arxiv.org",
        "export.arxiv.org",
        "api.unpaywall.org",
        "openalex.org",
        "api.openalex.org",
        "api.semanticscholar.org",
        "semanticscholar.org",
        "pdfs.semanticscholar.org",
        "doi.org",
        "ncbi.nlm.nih.gov",
        "pmc.ncbi.nlm.nih.gov",
        "europepmc.org",
        "www.biorxiv.org",
        "biorxiv.org",
        "www.medrxiv.org",
        "medrxiv.org",
        "zenodo.org",
        "doaj.org",
        "crossref.org",
        "api.crossref.org",
    }
)


@dataclass
class WebCandidate:
    """One ranked candidate match surfaced by the find-on-web search."""

    source: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    pdf_url: str | None = None
    landing_url: str | None = None
    is_oa: bool = False
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    candidate_id: str = ""

    def __post_init__(self) -> None:
        if not self.sources:
            self.sources = [self.source]
        if not self.candidate_id:
            self.candidate_id = self._compute_id()

    def _compute_id(self) -> str:
        basis = self.doi or f"{normalize_title(self.title or '')}|{self.year or ''}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# --- host guards ------------------------------------------------------------


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_denied_host(url: str) -> bool:
    """Return True if ``url``'s host is (or is a subdomain of) a known shadow library."""
    host = _host(url)
    if not host:
        return True  # no host at all → refuse
    for suffix in DENIED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix) or suffix in host.split("."):
            return True
        # bare-second-level forms like "sci-hub.se": match the leftmost label too.
        first_label = host.split(".")[0]
        if first_label == suffix or first_label.startswith(suffix):
            return True
    return False


def _host_matches(host: str, pattern: str) -> bool:
    """Return True if ``host`` matches an allowlist ``pattern`` (suffix-aware, denylist-style).

    A pattern matches when the host is exactly the pattern, or is a subdomain of it. The explicit
    ``*.example.org`` wildcard form matches subdomains only (NOT the bare apex), which lets an
    operator allow a vendor's subdomains without allowing the apex itself.
    """
    host = (host or "").lower().strip().rstrip(".")
    pattern = (pattern or "").lower().strip().rstrip(".")
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        return bool(base) and host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


def _is_allowed_host(url: str, allowed_hosts: set[str]) -> bool:
    """Return True if ``url``'s host matches any allowlist pattern in ``allowed_hosts``."""
    host = _host(url)
    if not host:
        return False
    return any(_host_matches(host, pattern) for pattern in allowed_hosts)


# --- helpers ----------------------------------------------------------------


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = " ".join(str(text).split())
    return collapsed or None


def _year_from(value) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value)[:4])
    except (ValueError, TypeError):
        return None


# --- per-source search fetchers ---------------------------------------------
#
# Each fetcher takes the search terms, returns list[WebCandidate], and NEVER raises: on any
# failure it logs and returns []. Outbound requests go through ``_get`` so the cross-host
# redirect SSRF guard is inherited; query terms go in ``params`` (never path interpolation).


def search_crossref(
    title: str, authors: list[str], year: int | None, *, mailto: str | None = None, rows: int = 5
) -> list[WebCandidate]:
    """Search Crossref by bibliographic title + author (metadata only)."""
    try:
        params: dict[str, object] = {"query.bibliographic": title, "rows": rows}
        if authors:
            params["query.author"] = " ".join(authors[:3])
        headers = {"User-Agent": f"PaRacORD/0.0 (mailto:{mailto})"} if mailto else {}
        response = _get(CROSSREF_SEARCH_API, params=params, headers=headers)
        response.raise_for_status()
        items = ((response.json() or {}).get("message") or {}).get("items") or []
    except Exception as exc:  # noqa: BLE001 - a source must never abort the search
        logger.warning("find-on-web crossref search failed: %s", exc)
        return []
    candidates: list[WebCandidate] = []
    for item in items:
        doi = item.get("DOI")
        date_parts = (item.get("issued") or {}).get("date-parts") or []
        cyear = date_parts[0][0] if date_parts and date_parts[0] else None
        author_names = [
            " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
            for a in item.get("author", [])
        ]
        candidates.append(
            WebCandidate(
                source="crossref",
                title=_clean((item.get("title") or [None])[0]),
                authors=[a for a in author_names if a],
                year=_year_from(cyear),
                doi=normalize_doi(doi) if doi else None,
                landing_url=f"https://doi.org/{doi}" if doi else None,
            )
        )
    return candidates


def search_openalex(
    title: str, authors: list[str], year: int | None, *, mailto: str | None = None, rows: int = 5
) -> list[WebCandidate]:
    """Search OpenAlex by title; surfaces ``best_oa_location`` PDF + OA flag."""
    try:
        params: dict[str, object] = {"search": title, "per-page": rows}
        if mailto:
            params["mailto"] = mailto
        response = _get(OPENALEX_SEARCH_API, params=params)
        response.raise_for_status()
        results = (response.json() or {}).get("results") or []
    except Exception as exc:  # noqa: BLE001 - a source must never abort the search
        logger.warning("find-on-web openalex search failed: %s", exc)
        return []
    candidates: list[WebCandidate] = []
    for work in results:
        doi = work.get("doi")
        if doi:
            doi = normalize_doi(doi)
        oa = work.get("open_access") or {}
        best = work.get("best_oa_location") or {}
        author_names = [
            (a.get("author") or {}).get("display_name") for a in work.get("authorships", [])
        ]
        candidates.append(
            WebCandidate(
                source="openalex",
                title=_clean(work.get("display_name") or work.get("title")),
                authors=[a for a in (_clean(n) for n in author_names) if a],
                year=_year_from(work.get("publication_year")),
                doi=doi,
                pdf_url=best.get("pdf_url"),
                landing_url=best.get("landing_page_url")
                or (f"https://doi.org/{doi}" if doi else None),
                is_oa=bool(oa.get("is_oa")),
            )
        )
    return candidates


def search_arxiv(
    title: str, authors: list[str], year: int | None, *, rows: int = 5
) -> list[WebCandidate]:
    """Search arXiv by title; arXiv PDFs are open access."""
    try:
        # Quote the title into the ti: field; arXiv's query language is space-separated.
        safe_title = re.sub(r'["\\]', " ", title)
        query = f'ti:"{safe_title}"'
        response = _get(ARXIV_SEARCH_API, params={"search_query": query, "max_results": rows})
        response.raise_for_status()
        root = etree.fromstring(response.text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a source must never abort the search
        logger.warning("find-on-web arxiv search failed: %s", exc)
        return []
    candidates: list[WebCandidate] = []
    for entry in root.findall("a:entry", ATOM_NS):
        raw_id = entry.findtext("a:id", namespaces=ATOM_NS) or ""
        arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id
        published = entry.findtext("a:published", namespaces=ATOM_NS) or ""
        author_names = [n.text for n in entry.findall("a:author/a:name", ATOM_NS) if n.text]
        candidates.append(
            WebCandidate(
                source="arxiv",
                title=_clean(entry.findtext("a:title", namespaces=ATOM_NS)),
                authors=[a for a in (_clean(n) for n in author_names) if a],
                year=_year_from(published),
                doi=_clean(entry.findtext("arxiv:doi", namespaces=ATOM_NS)),
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
                landing_url=raw_id or None,
                is_oa=True,
            )
        )
    return candidates


def search_semantic_scholar(
    title: str, authors: list[str], year: int | None, *, limit: int = 5
) -> list[WebCandidate]:
    """Search Semantic Scholar; surfaces ``openAccessPdf`` links."""
    try:
        response = _get(
            SEMANTIC_SCHOLAR_SEARCH_API,
            params={"query": title, "fields": SEMANTIC_SCHOLAR_SEARCH_FIELDS, "limit": limit},
        )
        response.raise_for_status()
        data = (response.json() or {}).get("data") or []
    except Exception as exc:  # noqa: BLE001 - a source must never abort the search
        logger.warning("find-on-web semantic scholar search failed: %s", exc)
        return []
    candidates: list[WebCandidate] = []
    for paper in data:
        doi = (paper.get("externalIds") or {}).get("DOI")
        oa_pdf = paper.get("openAccessPdf") or {}
        author_names = [a.get("name") for a in paper.get("authors", [])]
        candidates.append(
            WebCandidate(
                source="semanticscholar",
                title=_clean(paper.get("title")),
                authors=[a for a in (_clean(n) for n in author_names) if a],
                year=_year_from(paper.get("year")),
                doi=normalize_doi(doi) if doi else None,
                pdf_url=oa_pdf.get("url"),
                landing_url=f"https://doi.org/{doi}" if doi else None,
                is_oa=bool(paper.get("isOpenAccess") or oa_pdf.get("url")),
            )
        )
    return candidates


def search_unpaywall(doi: str, *, email: str | None = None) -> dict | None:
    """DOI-keyed OA enricher: returns ``{pdf_url, landing_url, is_oa}`` or None."""
    if not email:
        return None
    try:
        response = _get(f"{UNPAYWALL_API}/{_idseg(doi)}", params={"email": email})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json() or {}
    except Exception as exc:  # noqa: BLE001 - a source must never abort the search
        logger.warning("find-on-web unpaywall lookup failed: %s", exc)
        return None
    best = payload.get("best_oa_location") or {}
    return {
        "pdf_url": best.get("url_for_pdf") or best.get("url"),
        "landing_url": best.get("url_for_landing_page"),
        "is_oa": bool(payload.get("is_oa")),
    }


# --- dedup + ranking --------------------------------------------------------


def _dedup_key(candidate: WebCandidate) -> str:
    if candidate.doi:
        return f"doi:{candidate.doi}"
    return f"t:{normalize_title(candidate.title or '')}|{candidate.year or ''}"


def deduplicate(candidates: list[WebCandidate]) -> list[WebCandidate]:
    """Merge candidates that describe the same paper (by DOI, else norm-title+year).

    The merged entry keeps the richest data: it prefers an entry that carries a PDF/OA link,
    and unions the contributing source names.
    """
    merged: dict[str, WebCandidate] = {}
    for cand in candidates:
        key = _dedup_key(cand)
        existing = merged.get(key)
        if existing is None:
            merged[key] = cand
            continue
        # Union sources.
        for src in cand.sources:
            if src not in existing.sources:
                existing.sources.append(src)
        # Backfill missing fields; prefer a PDF/OA link when we don't have one yet.
        if not existing.pdf_url and cand.pdf_url:
            existing.pdf_url = cand.pdf_url
        existing.is_oa = existing.is_oa or cand.is_oa
        existing.doi = existing.doi or cand.doi
        existing.year = existing.year or cand.year
        existing.landing_url = existing.landing_url or cand.landing_url
        if not existing.title and cand.title:
            existing.title = cand.title
        if len(cand.authors) > len(existing.authors):
            existing.authors = cand.authors
    return list(merged.values())


def _author_overlap(query_authors: list[str], cand_authors: list[str]) -> float:
    if not query_authors or not cand_authors:
        return 0.0

    def lastnames(names: list[str]) -> set[str]:
        return {n.split()[-1].lower() for n in names if n.split()}

    q, c = lastnames(query_authors), lastnames(cand_authors)
    if not q or not c:
        return 0.0
    return len(q & c) / len(q)


def score_candidate(
    candidate: WebCandidate,
    *,
    query_title: str,
    query_year: int | None,
    query_authors: list[str],
) -> float:
    """Score a candidate: 60% title similarity + 20% year + 20% author overlap + OA bonus."""
    title_sim = difflib.SequenceMatcher(
        None, normalize_title(query_title or ""), normalize_title(candidate.title or "")
    ).ratio()
    if query_year is None or candidate.year is None:
        year_score = 0.5  # neutral when either side is unknown
    elif candidate.year == query_year:
        year_score = 1.0
    elif abs(candidate.year - query_year) == 1:
        year_score = 0.6
    else:
        year_score = 0.0
    author_score = _author_overlap(query_authors, candidate.authors)
    score = 0.6 * title_sim + 0.2 * year_score + 0.2 * author_score
    if candidate.is_oa or candidate.pdf_url:
        score += 0.05
    return round(min(score, 1.0), 4)


def rank(
    candidates: list[WebCandidate],
    *,
    query_title: str,
    query_year: int | None,
    query_authors: list[str],
    max_candidates: int = 10,
) -> list[WebCandidate]:
    """Score, sort (desc), and cap a deduplicated candidate list."""
    for cand in candidates:
        cand.score = score_candidate(
            cand,
            query_title=query_title,
            query_year=query_year,
            query_authors=query_authors,
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:max_candidates]


# --- work query extraction --------------------------------------------------


def _work_authors(db: Session, work: Work) -> list[str]:
    """Authors for a work from its 'authors' MetadataAssertion (canonical, else any)."""
    rows = list(
        db.scalars(
            select(MetadataAssertion.value)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work.id,
                MetadataAssertion.field_name == "authors",
            )
            .order_by(MetadataAssertion.selected_as_canonical.desc())
        ).all()
    )
    if not rows:
        return []
    return [a.strip() for a in re.split(r";|\band\b", rows[0]) if a.strip()]


# --- orchestrator -----------------------------------------------------------


def find_candidates(
    db: Session,
    work: Work,
    *,
    settings,
    sources: list[str] | None = None,
    fetchers: dict | None = None,
) -> dict:
    """Search enabled sources for candidate matches; return ranked candidates + degraded list.

    ``fetchers`` lets tests inject per-source callables. A source whose fetcher raises, times
    out, or is skipped for the wall-clock budget is reported in ``degraded_sources`` — the
    search never fails as a whole.
    """
    title = (work.canonical_title or "").strip()
    authors = _work_authors(db, work)
    year = work.year
    mailto = getattr(settings, "crossref_mailto", None)
    unpaywall_email = getattr(settings, "web_find_unpaywall_email", None) or mailto
    max_candidates = int(getattr(settings, "web_find_max_candidates", 10))
    per_source_timeout = float(getattr(settings, "web_find_per_source_timeout", 8.0))
    total_budget = float(getattr(settings, "web_find_total_budget", 25.0))

    fetchers = fetchers or {}
    registry = {
        "crossref": fetchers.get(
            "crossref", lambda: search_crossref(title, authors, year, mailto=mailto)
        ),
        "openalex": fetchers.get(
            "openalex", lambda: search_openalex(title, authors, year, mailto=mailto)
        ),
        "arxiv": fetchers.get("arxiv", lambda: search_arxiv(title, authors, year)),
        "semanticscholar": fetchers.get(
            "semanticscholar", lambda: search_semantic_scholar(title, authors, year)
        ),
    }
    selected = sources or list(registry.keys())
    queried: list[str] = []
    degraded: list[str] = []
    collected: list[WebCandidate] = []

    if not title:
        return {"candidates": [], "degraded_sources": [], "queried_sources": []}

    start = time.monotonic()
    for name in selected:
        runner = registry.get(name)
        if runner is None:
            continue
        if time.monotonic() - start > total_budget:
            degraded.append(name)  # wall-clock budget exhausted; skip remaining
            continue
        queried.append(name)
        source_start = time.monotonic()
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001 - one source must never abort the search
            logger.warning("find-on-web source %s failed: %s", name, exc)
            degraded.append(name)
            continue
        if time.monotonic() - source_start > per_source_timeout:
            # Returned, but over its slice; keep results, flag as degraded for transparency.
            logger.info("find-on-web source %s exceeded per-source timeout", name)
            degraded.append(name)
        collected.extend(result or [])

    # Unpaywall OA backfill for DOI-bearing candidates that still lack a PDF link.
    if unpaywall_email and ("unpaywall" not in degraded):
        upw = fetchers.get("unpaywall", lambda doi: search_unpaywall(doi, email=unpaywall_email))
        for cand in collected:
            if cand.doi and not cand.pdf_url:
                try:
                    enrich = upw(cand.doi)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("find-on-web unpaywall backfill failed: %s", exc)
                    enrich = None
                if enrich:
                    cand.pdf_url = cand.pdf_url or enrich.get("pdf_url")
                    cand.landing_url = cand.landing_url or enrich.get("landing_url")
                    cand.is_oa = cand.is_oa or bool(enrich.get("is_oa"))

    deduped = deduplicate(collected)
    ranked = rank(
        deduped,
        query_title=title,
        query_year=year,
        query_authors=authors,
        max_candidates=max_candidates,
    )
    return {
        "candidates": ranked,
        "degraded_sources": sorted(set(degraded)),
        "queried_sources": queried,
    }


# --- download + attach ------------------------------------------------------


class DownloadRefused(RuntimeError):
    """A download URL was refused by the legitimacy/SSRF guard before any bytes were fetched."""


def _stream_pdf(
    url: str, *, timeout: float, max_bytes: int, allowed_hosts: set[str] | None = None
) -> bytes | None:
    """Stream a candidate URL, enforcing the deny/allow lists on every hop + size/PDF validation.

    Returns the PDF bytes, or None when the response is not a real PDF (HTML/login wall, wrong
    content-type, missing %PDF magic). Raises DownloadRefused for a denied host on any hop, a final
    host not on ``allowed_hosts`` (when provided), and ValueError when the size cap is exceeded.

    The denylist is re-checked on EVERY hop (denylist always wins). The allowlist is checked on the
    final (post-redirect) host — and, defensively, on every hop — so a redirect can neither escape
    to a denied host nor land on a host outside the allowlist.
    """
    if _is_denied_host(url):
        raise DownloadRefused(f"Refusing download from shadow-library host {_host(url)!r}")
    headers = {"User-Agent": "PaRacORD/0.0 (find-on-web; legit sources only)"}
    with (
        httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        # Re-check the denylist on every redirect hop AND the final host.
        for hop in [*response.history, response]:
            hop_url = str(hop.url)
            if _is_denied_host(hop_url):
                raise DownloadRefused(
                    f"Refusing redirect to shadow-library host {_host(hop_url)!r}"
                )
            if allowed_hosts is not None and not _is_allowed_host(hop_url, allowed_hosts):
                raise DownloadRefused(
                    f"Host {_host(hop_url)!r} is not in the allowed-downloads list"
                )
        if response.status_code >= 400:
            return None
        content_type = (response.headers.get("content-type") or "").lower()
        if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
            # text/html, login walls, etc. → not a stored PDF.
            return None
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise ValueError("download exceeds max size cap")
        data = bytes(chunks)
    if len(data) < 4 or data[:4] != b"%PDF":
        return None
    return data


def download_and_attach(
    db: Session,
    *,
    work: Work,
    candidate_url: str,
    source: str,
    actor,
    settings,
    allowed_urls: set[str] | None = None,
    file_read=None,
    streamer=None,
) -> dict:
    """Download a surfaced candidate PDF and attach it to ``work``.

    Returns a per-item status dict (never raises): ``attached`` / ``deduped`` /
    ``manual_upload_needed`` / ``error``. ``allowed_urls`` is the anti-SSRF allowlist of URLs
    the search actually surfaced; a URL not in it is refused.
    """
    timeout = float(getattr(settings, "web_find_download_timeout", 60.0))
    max_bytes = int(getattr(settings, "web_find_max_download_bytes", 100 * 1024 * 1024))
    stream = streamer or _stream_pdf
    # Merge the built-in default allowlist with the owner/admin-managed DB rows. Imported lazily to
    # avoid a circular import (web_find_allowed_hosts imports DEFAULT_ALLOWED_HOSTS from this module).
    from app.services.web_find_allowed_hosts import merged_allowed_hosts

    allowed_hosts = merged_allowed_hosts(db)
    # Only hand the allowlist to streamers that accept it (the real _stream_pdf re-checks every
    # redirect hop). An injected/patched test streamer keeps its (url, timeout, max_bytes) shape.
    try:
        stream_accepts_hosts = "allowed_hosts" in inspect.signature(stream).parameters
    except (TypeError, ValueError):
        stream_accepts_hosts = False

    try:
        if not candidate_url:
            return {"status": "error", "reason": "missing download url"}
        if allowed_urls is not None and candidate_url not in allowed_urls:
            return {"status": "error", "reason": "url was not surfaced by this search"}
        # Denylist always wins, checked first.
        if _is_denied_host(candidate_url):
            return {"status": "error", "reason": "refused: shadow-library source"}
        # Positive allowlist on the candidate host; the streamer re-checks the final/redirect host.
        if not _is_allowed_host(candidate_url, allowed_hosts):
            return {
                "status": "error",
                "reason": "Host not in the allowed-downloads list (an owner/admin can add it)",
            }
        try:
            # The real streamer enforces the deny/allow lists on every redirect hop too; an
            # injected test streamer keeps the original (url, timeout, max_bytes) signature.
            if stream_accepts_hosts:
                pdf_bytes = stream(
                    candidate_url,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    allowed_hosts=allowed_hosts,
                )
            else:
                pdf_bytes = stream(candidate_url, timeout=timeout, max_bytes=max_bytes)
        except DownloadRefused as exc:
            return {"status": "error", "reason": str(exc)}
        except ValueError:
            return {"status": "error", "reason": "file exceeds the download size cap"}
        except Exception as exc:  # noqa: BLE001 - network/parse failure → manual fallback
            logger.warning("find-on-web download failed for %s: %s", candidate_url, exc)
            return {"status": "manual_upload_needed", "reason": str(exc)}
        if pdf_bytes is None:
            return {"status": "manual_upload_needed", "reason": "no downloadable PDF (login/HTML)"}

        filename = candidate_url.rstrip("/").rsplit("/", 1)[-1] or "download.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        file_obj, _created, newly_linked = attach_uploaded_pdf_to_work(
            db, work=work, filename=filename, pdf_bytes=pdf_bytes, actor=actor, settings=settings
        )
        db.commit()
        db.refresh(file_obj)
        if newly_linked:
            enqueue_extraction(file_obj.id)
        record_event(
            db,
            "web_find.downloaded",
            actor_user_id=getattr(actor, "id", None),
            entity_type="work",
            entity_id=str(work.id),
            details={"source": source, "file_id": str(file_obj.id), "url": candidate_url},
        )
        db.commit()
        result = {"status": "attached" if newly_linked else "deduped"}
        if file_read is not None:
            result["file"] = file_read(db, file_obj)
        else:
            result["file_id"] = str(file_obj.id)
        return result
    except Exception as exc:  # noqa: BLE001 - per-item isolation; never bubble up
        logger.warning("find-on-web attach failed: %s", exc)
        db.rollback()
        return {"status": "error", "reason": str(exc)}
