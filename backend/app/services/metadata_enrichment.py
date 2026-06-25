"""External metadata enrichment connectors and merge rules.

Enrichment is opt-in and identifier-based: we only query a source when the work already
has the matching identifier (arXiv id or DOI), so matches are exact and a fuzzy-title guard
is unnecessary. Every external field is recorded as a MetadataAssertion (provenance); a
trusted external value is promoted to the canonical work field only when the work is not
user-confirmed. Outbound requests carry only bibliographic identifiers (see SECURITY.md).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx2 as httpx
from lxml import etree
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.audit import record_event
from app.utils.normalization import normalize_title

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Work columns that an external assertion may be promoted into.
PROMOTABLE_FIELDS = ("title", "abstract", "year", "venue", "doi")


@dataclass
class ExternalMetadata:
    source: str
    title: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    year: int | None = None
    venue: str | None = None


def should_replace_canonical_field(source: str, field_name: str, user_locked: bool) -> bool:
    """Return whether an external assertion may replace a canonical field."""
    if user_locked:
        return False
    return source in {"user", "grobid", "crossref", "arxiv"} and field_name not in {"user_note"}


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
    return ExternalMetadata(
        source="arxiv",
        title=_clean(entry.findtext("a:title", namespaces=ATOM_NS)),
        abstract=_clean(entry.findtext("a:summary", namespaces=ATOM_NS)),
        authors=[a for a in (_clean(n) for n in authors) if a],
        doi=_clean(entry.findtext("arxiv:doi", namespaces=ATOM_NS)),
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
    )


# --- live fetchers (injectable; only call out per enabled source) -----------


def fetch_arxiv(arxiv_id: str, **_kwargs) -> ExternalMetadata | None:
    """Fetch metadata for an arXiv id from the public arXiv API."""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(ARXIV_API, params={"id_list": arxiv_id, "max_results": 1})
    response.raise_for_status()
    return parse_arxiv_atom(response.text)


def fetch_crossref_by_doi(doi: str, *, mailto: str | None = None) -> ExternalMetadata | None:
    """Fetch metadata for a DOI from the Crossref REST API."""
    # A mailto puts the request in Crossref's "polite pool" (faster, recommended).
    headers = {"User-Agent": f"PaRacORD/0.0 (mailto:{mailto})"} if mailto else {}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        response = client.get(f"{CROSSREF_API}/{doi}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_crossref(response.json())


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
        work.doi = value


def _store_external(db: Session, work: Work, meta: ExternalMetadata) -> list[str]:
    """Record assertions for an external record and promote trusted fields."""
    locked = work.user_confirmed
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
        # Idempotent: replace any prior assertion for this (field, source).
        db.execute(
            delete(MetadataAssertion).where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work.id,
                MetadataAssertion.field_name == field_name,
                MetadataAssertion.source == meta.source,
            )
        )
        promote = field_name in PROMOTABLE_FIELDS and should_replace_canonical_field(
            meta.source, field_name, locked
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


def enrich_work(
    db: Session,
    work: Work,
    *,
    settings,
    actor_user_id=None,
    arxiv_fetcher=fetch_arxiv,
    crossref_fetcher=fetch_crossref_by_doi,
) -> dict:
    """Enrich a work from external sources matched by its arXiv id / DOI."""
    if not getattr(settings, "enrichment_enabled", True):
        return {"sources": [], "promoted": []}

    metas: list[ExternalMetadata | None] = []
    if getattr(settings, "enrichment_arxiv", True) and work.arxiv_id:
        metas.append(arxiv_fetcher(work.arxiv_id))
    if getattr(settings, "enrichment_crossref", True) and work.doi:
        metas.append(crossref_fetcher(work.doi, mailto=getattr(settings, "crossref_mailto", None)))

    sources: list[str] = []
    promoted: list[str] = []
    for meta in metas:
        if meta is None:
            continue
        promoted += _store_external(db, work, meta)
        sources.append(meta.source)
        record_event(
            db,
            "metadata.enrichment_called",
            actor_user_id=actor_user_id,
            entity_type="work",
            entity_id=str(work.id),
            details={"source": meta.source, "identifier": work.doi or work.arxiv_id},
        )
    if sources:
        work.updated_at = datetime.utcnow()
    return {"sources": sources, "promoted": promoted}
