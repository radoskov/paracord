"""Local paper summarization (SPEC §8.14).

Tier 0 ("abstract") stores the work's abstract verbatim. Tier 1 ("extractive") runs a small
frequency-based extractive summarizer over the richest text available (abstract + GROBID body
text) — no network calls and no LLM, fully deterministic. Tier 2 ("local_llm") is an **opt-in**
abstractive summary via a local Ollama daemon; when it is disabled or unreachable it **degrades**
to the extractive engine while still recording the requested model + the source sections that fed
it, so the result is always honest about provenance and no hard dependency is introduced.

Every summary is persisted with provenance (``model_name`` + ``prompt_version``).
"""

import logging
import math
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ai import Summary
from app.models.citation import RawTeiDocument
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work
from app.services.tei_parser import extract_body_text

logger = logging.getLogger(__name__)

# Sentinel used as entity_id for library-scoped summaries (no real entity row).
_LIBRARY_SCOPE_ID = uuid.UUID(int=0)

SUPPORTED_SUMMARY_TYPES = ("abstract", "extractive", "local_llm")
PROMPT_VERSION = "v1"
LLM_PROMPT_VERSION = "local-llm-v1"

# A deliberately small English stop-word set; enough to bias scoring toward content words
# without pulling in an NLP dependency.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "which",
        "with",
        "we",
        "our",
        "this",
        "these",
        "those",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "but",
        "not",
        "can",
        "will",
        "may",
        "also",
        "such",
        "using",
        "used",
        "use",
        "based",
        "both",
        "than",
        "been",
        "being",
        "more",
        "most",
    ]
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")


def summarize_extractive(text: str, *, max_sentences: int = 5) -> str:
    """Return the highest-scoring sentences (in original order) by word-frequency salience."""
    cleaned = " ".join((text or "").split())
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s.strip()]
    if len(sentences) <= max_sentences:
        return cleaned

    frequencies: dict[str, int] = {}
    for word in _WORD.findall(cleaned.lower()):
        if word not in _STOPWORDS:
            frequencies[word] = frequencies.get(word, 0) + 1
    if not frequencies:
        return " ".join(sentences[:max_sentences])
    peak = max(frequencies.values())

    scored: list[tuple[float, int]] = []
    for index, sentence in enumerate(sentences):
        words = [w for w in _WORD.findall(sentence.lower()) if w not in _STOPWORDS]
        if not words:
            continue
        # Average normalized salience, damped by length so long sentences don't always win.
        salience = sum(frequencies[w] / peak for w in words) / math.sqrt(len(words))
        scored.append((salience, index))

    top_indices = sorted(index for _, index in sorted(scored, reverse=True)[:max_sentences])
    return " ".join(sentences[index] for index in top_indices)


def work_source_text(db: Session, work: Work) -> str:
    """Gather the richest text available for a work: its abstract plus GROBID body text."""
    parts: list[str] = []
    if work.abstract:
        parts.append(work.abstract)
    tei = db.scalar(
        select(RawTeiDocument)
        .where(RawTeiDocument.work_id == work.id)
        .order_by(RawTeiDocument.created_at.desc())
    )
    if tei is not None:
        body = extract_body_text(tei.tei_xml)
        if body:
            parts.append(body)
    return "\n".join(parts).strip()


def _source_section_labels(db: Session, work: Work) -> list[str]:
    """Which sources feed a work's summary, for provenance (abstract / extracted body text)."""
    labels: list[str] = []
    if work.abstract:
        labels.append("abstract")
    tei = db.scalar(select(RawTeiDocument).where(RawTeiDocument.work_id == work.id))
    if tei is not None:
        labels.append("body")
    return labels


def _ollama_summarize(text: str, *, model: str, base_url: str) -> str:
    """Call a local Ollama daemon for an abstractive summary. Raises on any failure."""
    import httpx2 as httpx

    prompt = (
        "Summarize the following academic paper text in 3-4 sentences, focusing on the problem, "
        "method, and key result. Respond with the summary only.\n\n" + text[:12000]
    )
    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        return (response.json().get("response") or "").strip()


def summarize_work(
    db: Session,
    work: Work,
    *,
    summary_type: str = "extractive",
    max_sentences: int = 5,
    model_name: str | None = None,
    settings: Settings | None = None,
) -> Summary:
    """Create (replacing any prior summary of the same type) a provenance-tagged summary.

    For ``local_llm`` the returned Summary carries a transient ``source_sections`` attribute (the
    sources that fed it); the API surfaces it. When the LLM is disabled/unreachable the text is the
    extractive fallback but the requested model is still recorded, with a fallback note in
    ``source_sections``.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")

    settings = settings or get_settings()
    from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

    ai_cfg = get_ai_config(db, settings=settings)
    source_sections: list[str] = []
    prompt_version = PROMPT_VERSION

    if summary_type == "abstract":
        text = (work.abstract or "").strip()
        stored_model = "tier0-abstract"
    elif summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        source_text = work_source_text(db, work)
        source_sections = _source_section_labels(db, work)
        text = ""
        # The LLM is attempted only when the owner has selected it (ai_config) — otherwise we go
        # straight to the deterministic fallback, so disabled/CI installs never hit the network.
        if ai_cfg.summary_provider == "local_llm" and source_text:
            try:
                text = _ollama_summarize(
                    source_text, model=stored_model, base_url=ai_cfg.ollama_url
                )
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                logger.warning("local_llm summary unavailable (%s); using extractive fallback", exc)
        if not text:
            text = summarize_extractive(source_text, max_sentences=max_sentences)
            if text:
                source_sections.append("extractive-fallback")
    else:
        text = summarize_extractive(work_source_text(db, work), max_sentences=max_sentences)
        stored_model = "tier1-extractive-frequency"

    if not text:
        raise ValueError("No text available to summarize")

    # One stored summary per (work, type): replace the previous one so re-runs stay idempotent.
    db.execute(
        delete(Summary).where(
            Summary.entity_type == "work",
            Summary.entity_id == work.id,
            Summary.summary_type == summary_type,
        )
    )
    summary = Summary(
        entity_type="work",
        entity_id=work.id,
        summary_type=summary_type,
        text=text,
        model_name=stored_model,
        prompt_version=prompt_version,
    )
    db.add(summary)
    db.flush()
    # Transient (non-persisted) provenance for the API response.
    summary.source_sections = source_sections
    return summary


def list_work_summaries(db: Session, work_id: uuid.UUID) -> list[Summary]:
    """Return stored summaries for a work, newest first."""
    return list(
        db.scalars(
            select(Summary)
            .where(Summary.entity_type == "work", Summary.entity_id == work_id)
            .order_by(Summary.created_at.desc())
        ).all()
    )


def _scope_works(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    visible_ids: set[uuid.UUID] | None = None,
) -> list[Work]:
    if scope_type == "library":
        works = list(db.scalars(select(Work)).all())
    elif scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope_id is required for a shelf summary")
        works = list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .where(ShelfWork.shelf_id == scope_id)
            ).all()
        )
    elif scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope_id is required for a rack summary")
        works = list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
                .where(RackShelf.rack_id == scope_id)
                .distinct()
            ).all()
        )
    else:
        raise ValueError(f"Unsupported scope type: {scope_type!r}")
    if visible_ids is not None:
        works = [w for w in works if w.id in visible_ids]
    return works


def summarize_scope(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    summary_type: str = "extractive",
    max_sentences: int = 8,
    visible_ids: set[uuid.UUID] | None = None,
) -> tuple[Summary, int]:
    """Generate (replacing prior) an extractive summary over all works in a scope.

    Returns ``(summary, work_count)`` so the caller can include the count without an
    extra query. ``visible_ids`` (Phase H) restricts the scope to works the caller may see.
    """
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID

    abstracts = [w.abstract for w in works if w.abstract]
    if not abstracts:
        raise ValueError(f"No abstracts available in {scope_type!r} scope to summarize")

    combined = " ".join(abstracts)
    text = summarize_extractive(combined, max_sentences=max_sentences)

    db.execute(
        delete(Summary).where(
            Summary.entity_type == scope_type,
            Summary.entity_id == entity_id,
            Summary.summary_type == summary_type,
        )
    )
    summary = Summary(
        entity_type=scope_type,
        entity_id=entity_id,
        summary_type=summary_type,
        text=text,
        model_name="tier1-extractive-frequency-scope",
        prompt_version=PROMPT_VERSION,
    )
    db.add(summary)
    db.flush()
    return summary, len(abstracts)
