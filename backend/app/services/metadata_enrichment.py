"""External metadata enrichment connectors and merge rules.

Enrichment is opt-in and identifier-based: we only query a source when the work already
has the matching identifier (arXiv id or DOI), so matches are exact and a fuzzy-title guard
is unnecessary. Every external field is recorded as a MetadataAssertion (provenance); a
trusted external value is promoted to the canonical work field only when the work is not
user-confirmed. Outbound requests carry only bibliographic identifiers (see SECURITY.md).
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx2 as httpx
from lxml import etree
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.audit import record_event
from app.services.identifiers import backfill_identifiers
from app.utils.normalization import arxiv_base_id, normalize_title

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper"
SEMANTIC_SCHOLAR_FIELDS = "title,abstract,year,venue,authors,externalIds,citationCount"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


# Work columns that an external assertion may be promoted into.
PROMOTABLE_FIELDS = ("title", "abstract", "year", "venue", "doi")

# Sources trusted to overwrite a canonical (non-user-locked) field.
TRUSTED_SOURCES = {"user", "grobid", "crossref", "arxiv", "openalex", "semanticscholar"}


@dataclass
class ExternalMetadata:
    source: str
    title: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    venue: str | None = None
    # External citation count (Track C P1). None when the source did not report one.
    citation_count: int | None = None


# Priority of sources for the cached citation-count snapshot: the highest-listed source that
# returned a count wins. OpenAlex first (most comprehensive, actively maintained), then Semantic
# Scholar, then Crossref (``is-referenced-by-count`` lags and undercounts).
CITATION_COUNT_PRIORITY = ("openalex", "semanticscholar", "crossref")


def _as_int(value: object) -> int | None:
    """Coerce an external count to a non-negative int, or None if it isn't one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def should_replace_canonical_field(source: str, field_name: str, user_locked: bool) -> bool:
    """Return whether an external assertion may replace a canonical field."""
    if user_locked:
        return False
    return source in TRUSTED_SOURCES and field_name not in {"user_note"}


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed or None


# --- pure parsers (unit-tested against fixtures) ----------------------------


def parse_arxiv_atom(xml: str) -> ExternalMetadata | None:
    """Parse an arXiv Atom API response into ExternalMetadata."""
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None
    entry = root.find("a:entry", ATOM_NS)
    if entry is None:
        return None
    published = entry.findtext("a:published", namespaces=ATOM_NS) or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    authors = [name.text for name in entry.findall("a:author/a:name", ATOM_NS) if name.text]
    raw_id = entry.findtext("a:id", namespaces=ATOM_NS) or ""
    atom_arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else None
    return ExternalMetadata(
        source="arxiv",
        title=_clean(entry.findtext("a:title", namespaces=ATOM_NS)),
        abstract=_clean(entry.findtext("a:summary", namespaces=ATOM_NS)),
        authors=[a for a in (_clean(n) for n in authors) if a],
        doi=_clean(entry.findtext("arxiv:doi", namespaces=ATOM_NS)),
        arxiv_id=_clean(atom_arxiv_id),
        year=year,
    )


def parse_crossref(payload: dict) -> ExternalMetadata | None:
    """Parse a Crossref /works/{doi} JSON payload into ExternalMetadata."""
    message = (payload or {}).get("message") or {}
    if not message:
        return None
    date_parts = (message.get("issued") or {}).get("date-parts") or []
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    authors = [
        " ".join(part for part in (a.get("given"), a.get("family")) if part).strip()
        for a in message.get("author", [])
    ]
    abstract = message.get("abstract")
    if abstract:
        abstract = _clean(re.sub(r"<[^>]+>", " ", abstract))
    return ExternalMetadata(
        source="crossref",
        title=_clean((message.get("title") or [None])[0]),
        abstract=abstract,
        authors=[a for a in authors if a],
        doi=message.get("DOI"),
        year=year,
        venue=_clean((message.get("container-title") or [None])[0]),
        citation_count=_as_int(message.get("is-referenced-by-count")),
    )


def parse_openalex(payload: dict) -> ExternalMetadata | None:
    """Parse an OpenAlex /works/{id} JSON payload into ExternalMetadata."""
    work = payload or {}
    if not work.get("id"):
        return None
    doi = work.get("doi")
    if doi:
        doi = doi.removeprefix("https://doi.org/")
    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or (
        work.get("host_venue") or {}
    ).get("display_name")
    authors = [
        (authorship.get("author") or {}).get("display_name")
        for authorship in work.get("authorships", [])
    ]
    return ExternalMetadata(
        source="openalex",
        title=_clean(work.get("display_name") or work.get("title")),
        abstract=_openalex_abstract(work.get("abstract_inverted_index")),
        authors=[a for a in (_clean(name) for name in authors) if a],
        doi=doi,
        year=work.get("publication_year"),
        venue=_clean(venue),
        citation_count=_as_int(work.get("cited_by_count")),
    )


def _openalex_abstract(inverted_index: dict | None) -> str | None:
    """Rebuild plain text from OpenAlex's word -> [positions] inverted index."""
    if not inverted_index:
        return None
    positioned: list[tuple[int, str]] = [
        (position, word) for word, positions in inverted_index.items() for position in positions
    ]
    positioned.sort()
    return _clean(" ".join(word for _, word in positioned))


def parse_semantic_scholar(payload: dict) -> ExternalMetadata | None:
    """Parse a Semantic Scholar /paper/{id} JSON payload into ExternalMetadata."""
    paper = payload or {}
    if not (paper.get("title") or paper.get("externalIds")):
        return None
    authors = [author.get("name") for author in paper.get("authors", [])]
    return ExternalMetadata(
        source="semanticscholar",
        title=_clean(paper.get("title")),
        abstract=_clean(paper.get("abstract")),
        authors=[a for a in (_clean(name) for name in authors) if a],
        doi=(paper.get("externalIds") or {}).get("DOI"),
        arxiv_id=_clean((paper.get("externalIds") or {}).get("ArXiv")),
        year=paper.get("year"),
        venue=_clean(paper.get("venue")),
        citation_count=_as_int(paper.get("citationCount")),
    )


# --- SSRF-hardened outbound HTTP (SPEC §7, M5) ------------------------------
#
# Identifiers are attacker-influenced (a DOI/arXiv id can be anything the user typed), so they are
# percent-encoded into the path and never allowed to alter the target. Redirects are followed but a
# redirect that leaves the API's own host is refused — closing the SSRF pivot to link-local / metadata
# endpoints (e.g. 169.254.169.254) via a crafted identifier or a hostile API.


class ExternalFetchError(RuntimeError):
    """An outbound enrichment request was refused (e.g. a cross-host redirect)."""


def _idseg(identifier: str) -> str:
    """Percent-encode an identifier for safe use as a single URL path segment."""
    return quote(identifier.strip(), safe="")


# Shared across fetches (httpx.Client is thread-safe) so enrichment reuses pooled connections
# instead of a TCP+TLS handshake per request.
_HTTP_CLIENT = httpx.Client(timeout=30, follow_redirects=True)


# One in-request retry after a rate-limit/overload response (S6c). Longer waits are the RQ-level
# retry's job — sleeping longer than this inside a request/job just pins the worker.
_RETRY_AFTER_CAP_SECONDS = 15.0
_RETRYABLE_STATUS = (429, 503)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The server-requested wait from a 429/503 ``Retry-After`` header, capped; None = don't wait.

    Only the delta-seconds form is honored (the HTTP-date form is rare on these APIs and not worth
    parsing); a missing/garbage header still gets a short default wait so the retry isn't instant.
    """
    if response.status_code not in _RETRYABLE_STATUS:
        return None
    raw = response.headers.get("retry-after", "").strip()
    try:
        wanted = float(raw) if raw else 1.0
    except ValueError:
        wanted = 1.0
    return min(max(wanted, 0.0), _RETRY_AFTER_CAP_SECONDS)


def _get(url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """GET ``url`` following only same-host redirects (SSRF guard).

    Retries ONCE, in-request, when the API answers 429/503, honoring a (capped) ``Retry-After`` —
    the common polite-pool rate-limit case. Anything beyond that single retry is left to the
    caller / the RQ job-level retry.
    """
    expected_host = urlsplit(url).hostname
    response = _HTTP_CLIENT.get(url, params=params, headers=headers or {})
    wait = _retry_after_seconds(response)
    if wait is not None:
        time.sleep(wait)
        response = _HTTP_CLIENT.get(url, params=params, headers=headers or {})
    for hop in [*response.history, response]:
        if urlsplit(str(hop.url)).hostname != expected_host:
            raise ExternalFetchError(
                f"Refusing cross-host redirect from {expected_host} to {urlsplit(str(hop.url)).hostname}"
            )
    return response


# --- live fetchers (injectable; only call out per enabled source) -----------


def fetch_arxiv(arxiv_id: str, **_kwargs) -> ExternalMetadata | None:
    """Fetch metadata for an arXiv id from the public arXiv API."""
    response = _get(ARXIV_API, params={"id_list": arxiv_id, "max_results": 1})
    response.raise_for_status()
    return parse_arxiv_atom(response.text)


def fetch_crossref_by_doi(doi: str, *, mailto: str | None = None) -> ExternalMetadata | None:
    """Fetch metadata for a DOI from the Crossref REST API."""
    # A mailto puts the request in Crossref's "polite pool" (faster, recommended).
    headers = {"User-Agent": f"PaRacORD/0.0 (mailto:{mailto})"} if mailto else {}
    response = _get(f"{CROSSREF_API}/{_idseg(doi)}", headers=headers)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_crossref(response.json())


def fetch_openalex(doi: str, *, mailto: str | None = None, **_kwargs) -> ExternalMetadata | None:
    """Fetch metadata for a DOI from the OpenAlex API."""
    # A mailto puts the request in OpenAlex's faster "polite pool".
    params = {"mailto": mailto} if mailto else {}
    response = _get(f"{OPENALEX_API}/doi:{_idseg(doi)}", params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_openalex(response.json())


def fetch_semantic_scholar(
    *, arxiv_id: str | None = None, doi: str | None = None, **_kwargs
) -> ExternalMetadata | None:
    """Fetch metadata from Semantic Scholar by arXiv id (preferred) or DOI."""
    if arxiv_id:
        identifier = f"arXiv:{_idseg(_arxiv_base(arxiv_id))}"
    elif doi:
        identifier = f"DOI:{_idseg(doi)}"
    else:
        return None
    response = _get(
        f"{SEMANTIC_SCHOLAR_API}/{identifier}", params={"fields": SEMANTIC_SCHOLAR_FIELDS}
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_semantic_scholar(response.json())


def _arxiv_base(arxiv_id: str) -> str:
    """Normalize a stored arXiv id to its bare, version-less form for lookups (S3: one parser)."""
    return arxiv_base_id(arxiv_id) or arxiv_id.strip()


# --- merge into the work ----------------------------------------------------


def _apply_field(work: Work, field_name: str, value: str, source: str) -> None:
    if field_name == "title":
        work.canonical_title = value
        work.normalized_title = normalize_title(value)
        work.canonical_metadata_source = source
    elif field_name == "abstract":
        work.abstract = value
    elif field_name == "year":
        work.year = int(value) if value.isdigit() else work.year
    elif field_name == "venue":
        work.venue = value
    elif field_name == "doi":
        from app.utils.normalization import normalize_doi

        work.doi = normalize_doi(value)


def _store_external(db: Session, work: Work, meta: ExternalMetadata) -> list[str]:
    """Record assertions for an external record and promote trusted fields."""
    # Per-field locking (SPEC §8.12): a field the user confirmed is never overwritten; the legacy
    # all-or-nothing flag still locks every field when set.
    confirmed = set(work.confirmed_fields or [])
    promoted: list[str] = []
    fields = {
        "title": meta.title,
        "abstract": meta.abstract,
        "year": meta.year,
        "venue": meta.venue,
        "doi": meta.doi,
        "authors": "; ".join(meta.authors) or None,
    }
    for field_name, value in fields.items():
        if value in (None, ""):
            continue
        value = str(value)
        # Dedup (issue 1b) + idempotent replace: keep an existing assertion whose value is
        # byte-identical to the incoming one (no id/timestamp churn) and drop every other prior row
        # for this (field, source) — stale-value rows and legacy duplicates alike.
        prior = list(
            db.scalars(
                select(MetadataAssertion).where(
                    MetadataAssertion.entity_type == "work",
                    MetadataAssertion.entity_id == work.id,
                    MetadataAssertion.field_name == field_name,
                    MetadataAssertion.source == meta.source,
                )
            )
        )
        same = next((a for a in prior if a.value == value), None)
        for a in prior:
            if a is not same:
                db.delete(a)
        field_locked = work.user_confirmed or field_name in confirmed
        promote = field_name in PROMOTABLE_FIELDS and should_replace_canonical_field(
            meta.source, field_name, field_locked
        )
        if promote:
            db.execute(
                update(MetadataAssertion)
                .where(
                    MetadataAssertion.entity_type == "work",
                    MetadataAssertion.entity_id == work.id,
                    MetadataAssertion.field_name == field_name,
                )
                .values(selected_as_canonical=False)
            )
        if same is not None:
            same.selected_as_canonical = promote
            same.confidence = 0.9
            same.retrieved_at = datetime.now(UTC)
        else:
            db.add(
                MetadataAssertion(
                    entity_type="work",
                    entity_id=work.id,
                    field_name=field_name,
                    value=value,
                    source=meta.source,
                    confidence=0.9,
                    selected_as_canonical=promote,
                )
            )
        if promote:
            _apply_field(work, field_name, value, meta.source)
            promoted.append(field_name)
    return promoted


def apply_external_metadata(db: Session, work: Work, meta: ExternalMetadata) -> list[str]:
    """Record assertions for an externally-supplied record (e.g. a Find-on-web result, issue 9) and
    promote trusted fields — the same provenance path enrichment uses, so a non-trusted source stays
    a reviewable candidate the user selects with "Use this" rather than silently overwriting."""
    return _store_external(db, work, meta)


def enrich_work(
    db: Session,
    work: Work,
    *,
    settings,
    actor_user_id=None,
    arxiv_fetcher=fetch_arxiv,
    crossref_fetcher=fetch_crossref_by_doi,
    openalex_fetcher=fetch_openalex,
    semantic_scholar_fetcher=fetch_semantic_scholar,
) -> dict:
    """Enrich a work from external sources matched by its arXiv id / DOI.

    Per-source resilient (D8): each source is queried independently and a source that raises
    (down / rate-limited / malformed) is recorded in ``failed`` and skipped — the remaining sources
    are still tried, so one flaky source never aborts the whole enrichment.
    """
    if not getattr(settings, "enrichment_enabled", True):
        return {"sources": [], "promoted": [], "failed": []}

    mailto = getattr(settings, "crossref_mailto", None)
    planned: list[tuple[str, Callable[[], ExternalMetadata | None]]] = []
    if getattr(settings, "enrichment_arxiv", True) and work.arxiv_id:
        planned.append(("arxiv", lambda: arxiv_fetcher(work.arxiv_id)))
    if getattr(settings, "enrichment_crossref", True) and work.doi:
        planned.append(("crossref", lambda: crossref_fetcher(work.doi, mailto=mailto)))
    if getattr(settings, "enrichment_openalex", False) and work.doi:
        planned.append(("openalex", lambda: openalex_fetcher(work.doi, mailto=mailto)))
    if getattr(settings, "enrichment_semantic_scholar", False) and (work.arxiv_id or work.doi):
        planned.append(
            (
                "semanticscholar",
                lambda: semantic_scholar_fetcher(arxiv_id=work.arxiv_id, doi=work.doi),
            )
        )

    metas: list[ExternalMetadata | None] = []
    failed: list[str] = []
    for name, fetch in planned:
        try:
            metas.append(fetch())
        except Exception as exc:  # noqa: BLE001 - one failing source must not abort the rest
            failed.append(name)
            logger.warning("enrich_work source %s failed for work %s: %s", name, work.id, exc)

    sources: list[str] = []
    promoted: list[str] = []
    counts: dict[str, int] = {}
    for meta in metas:
        if meta is None:
            continue
        promoted += _store_external(db, work, meta)
        # DOI is promoted via the assertion machinery above; the arXiv id is not a promotable
        # canonical field, so backfill it directly onto the work when still empty (respecting locks).
        promoted += backfill_identifiers(work, arxiv_id=meta.arxiv_id)
        sources.append(meta.source)
        if meta.citation_count is not None:
            counts[meta.source] = meta.citation_count
        record_event(
            db,
            "metadata.enrichment_called",
            actor_user_id=actor_user_id,
            entity_type="work",
            entity_id=str(work.id),
            details={"source": meta.source, "identifier": work.doi or work.arxiv_id},
        )
    # Refresh the cached citation-count snapshot from the highest-priority source that reported one
    # (newer wins — this run overwrites any prior snapshot). Papers whose sources returned no count
    # keep their existing value untouched.
    for source in CITATION_COUNT_PRIORITY:
        if source in counts:
            work.citation_count = counts[source]
            work.citation_count_source = source
            work.citation_count_fetched_at = datetime.now(UTC)
            break
    if promoted:
        # Enrichment can be the moment this work first gains its DOI/arXiv id/title — exactly the
        # fields the local matcher keys on. Reverse-rescan so still-external references and cached
        # citing papers elsewhere in the library link up now, not at the next full rescan.
        # Local imports: citing_papers imports this module (cycle guard).
        from app.services.app_config import (  # noqa: PLC0415
            effective_accept_policy,
        )
        from app.services.citing_papers import (  # noqa: PLC0415
            rescan_external_papers_for_new_work,
        )
        from app.services.reference_matching import (  # noqa: PLC0415
            rescan_references_for_new_work,
        )

        rescan_references_for_new_work(db, work, accept_policy=effective_accept_policy(db))
        rescan_external_papers_for_new_work(db, work)
    if sources:
        work.updated_at = datetime.now(UTC)
    return {"sources": sources, "promoted": promoted, "failed": failed}
