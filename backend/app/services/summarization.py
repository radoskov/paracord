"""Local, dependency-free paper summarization (SPEC §8.14, tiers 0 and 1).

Tier 0 ("abstract") stores the work's abstract verbatim. Tier 1 ("extractive") runs a small
frequency-based extractive summarizer over the richest text available (abstract + GROBID body
text) — no network calls and no LLM, so it is safe to run locally and is fully deterministic.
Tier 2 (local-LLM abstractive via Ollama) is intentionally not implemented here.

Every summary is persisted with provenance (``model_name`` + ``prompt_version``) so the source
of a stored summary is always recoverable.
"""

import math
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai import Summary
from app.models.citation import RawTeiDocument
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work
from app.services.tei_parser import extract_body_text

# Sentinel used as entity_id for library-scoped summaries (no real entity row).
_LIBRARY_SCOPE_ID = uuid.UUID(int=0)

SUPPORTED_SUMMARY_TYPES = ("abstract", "extractive")
PROMPT_VERSION = "v1"

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


def summarize_work(
    db: Session, work: Work, *, summary_type: str = "extractive", max_sentences: int = 5
) -> Summary:
    """Create (replacing any prior summary of the same type) a provenance-tagged summary."""
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")

    if summary_type == "abstract":
        text = (work.abstract or "").strip()
        model_name = "tier0-abstract"
    else:
        text = summarize_extractive(work_source_text(db, work), max_sentences=max_sentences)
        model_name = "tier1-extractive-frequency"

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
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )
    db.add(summary)
    db.flush()
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


def _scope_works(db: Session, *, scope_type: str, scope_id: uuid.UUID | None) -> list[Work]:
    if scope_type == "library":
        return list(db.scalars(select(Work)).all())
    if scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope_id is required for a shelf summary")
        return list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .where(ShelfWork.shelf_id == scope_id)
            ).all()
        )
    if scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope_id is required for a rack summary")
        return list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
                .where(RackShelf.rack_id == scope_id)
                .distinct()
            ).all()
        )
    raise ValueError(f"Unsupported scope type: {scope_type!r}")


def summarize_scope(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    summary_type: str = "extractive",
    max_sentences: int = 8,
) -> tuple[Summary, int]:
    """Generate (replacing prior) an extractive summary over all works in a scope.

    Returns ``(summary, work_count)`` so the caller can include the count without an
    extra query.
    """
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id)
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
