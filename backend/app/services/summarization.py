"""Local paper summarization (SPEC §8.14).

Tier 0 ("abstract") stores the work's abstract verbatim. Tier 1 ("extractive") runs a small
frequency-based extractive summarizer over the richest text available (abstract + GROBID body
text) — no network calls and no LLM, fully deterministic. Tier 2 ("local_llm") is an **opt-in**
abstractive summary via a local Ollama daemon; when it is disabled or unreachable it **degrades**
to the extractive engine while still recording the requested model + the source sections that fed
it, so the result is always honest about provenance and no hard dependency is introduced.

Every summary is persisted with provenance (``model_name`` + ``prompt_version``).
"""

import hashlib
import logging
import math
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ai import Summary
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.scope_resolution import resolve_scope_works
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


def _work_source(db: Session, work: Work) -> tuple[str, list[str]]:
    """Gather the richest text available for a work plus its provenance labels, in ONE TEI fetch.

    Returns ``(text, labels)``: the abstract plus the latest GROBID body text, and which of
    those sources are present (abstract / body).
    """
    parts: list[str] = []
    labels: list[str] = []
    if work.abstract:
        parts.append(work.abstract)
        labels.append("abstract")
    tei = db.scalar(
        select(RawTeiDocument)
        .where(RawTeiDocument.work_id == work.id)
        .order_by(RawTeiDocument.created_at.desc())
    )
    if tei is not None:
        body = extract_body_text(tei.tei_xml)
        if body:
            parts.append(body)
        labels.append("body")
    return "\n".join(parts).strip(), labels


def work_source_text(db: Session, work: Work) -> str:
    """Gather the richest text available for a work: its abstract plus GROBID body text."""
    return _work_source(db, work)[0]


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
    created_by_user_id: uuid.UUID | None = None,
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
    # Provider provenance (Phase B2): what was requested vs actually used, and — when a requested
    # LLM silently degraded to the extractive engine — a short reason the UI can show.
    provider_requested = summary_type
    provider_used = summary_type
    fallback_reason: str | None = None

    if summary_type == "abstract":
        text = (work.abstract or "").strip()
        stored_model = "tier0-abstract"
    elif summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        source_text, source_sections = _work_source(db, work)
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
                fallback_reason = str(exc) or "the local LLM is unavailable"
        else:
            fallback_reason = "the local LLM is not enabled"
        if not text:
            text = summarize_extractive(source_text, max_sentences=max_sentences)
            provider_used = "extractive"
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
    fallback = provider_used != provider_requested
    summary = Summary(
        entity_type="work",
        entity_id=work.id,
        summary_type=summary_type,
        text=text,
        model_name=stored_model,
        prompt_version=prompt_version,
        provider_requested=provider_requested,
        provider_used=provider_used,
        fallback=fallback,
        source_sections=source_sections,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_by_user_id=created_by_user_id,
        params={
            "summary_type": summary_type,
            "max_sentences": max_sentences,
            "model_name": model_name,
        },
    )
    db.add(summary)
    db.flush()
    # A ``fallback_reason`` is transient (not a column): the UI shows it only on a fresh degrade.
    summary.fallback_reason = fallback_reason if fallback else None
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


# Scope resolution is shared now (S1/S2) — one query-returning resolver, required
# visibility clamp, shadow filter applied centrally.
def _scope_works(db, *, scope_type, scope_id, visible_ids):
    return resolve_scope_works(db, scope_type, scope_id, visible_ids=visible_ids)


def latest_scope_summary(db: Session, *, scope_type: str, scope_id: uuid.UUID | None):
    """The most recent stored summary for a scope, or None (read path for the S15 async flow)."""
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID
    return db.scalars(
        select(Summary)
        .where(Summary.entity_type == scope_type, Summary.entity_id == entity_id)
        .order_by(Summary.created_at.desc())
    ).first()


def summarize_scope(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    summary_type: str = "extractive",
    max_sentences: int = 8,
    model_name: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    visible_ids: set[uuid.UUID] | None = None,
    settings: Settings | None = None,
) -> tuple[Summary, int]:
    """Generate (replacing prior) a scope summary over all works' abstracts.

    Mirrors ``summarize_work``: ``local_llm`` calls the configured Ollama model (when enabled) over
    the combined abstracts and degrades to the extractive engine on any failure, recording the
    requested model + a fallback reason. Returns ``(summary, work_count)``; ``visible_ids`` (Phase H)
    restricts the scope to works the caller may see.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")
    settings = settings or get_settings()
    from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

    ai_cfg = get_ai_config(db, settings=settings)
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID

    abstracts = [w.abstract for w in works if w.abstract]
    if not abstracts:
        raise ValueError(f"No abstracts available in {scope_type!r} scope to summarize")
    combined = " ".join(abstracts)

    provider_requested = summary_type
    provider_used = summary_type
    fallback_reason: str | None = None
    prompt_version = PROMPT_VERSION
    text = ""
    if summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        if ai_cfg.summary_provider == "local_llm":
            try:
                text = _ollama_summarize(combined, model=stored_model, base_url=ai_cfg.ollama_url)
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                logger.warning("local_llm scope summary unavailable (%s); extractive fallback", exc)
                fallback_reason = str(exc) or "the local LLM is unavailable"
        else:
            fallback_reason = "the local LLM is not enabled"
        if not text:
            text = summarize_extractive(combined, max_sentences=max_sentences)
            provider_used = "extractive"
            stored_model = "tier1-extractive-frequency-scope"
    else:
        text = summarize_extractive(combined, max_sentences=max_sentences)
        stored_model = "tier1-extractive-frequency-scope"

    db.execute(
        delete(Summary).where(
            Summary.entity_type == scope_type,
            Summary.entity_id == entity_id,
            Summary.summary_type == summary_type,
        )
    )
    fallback = provider_used != provider_requested
    summary = Summary(
        entity_type=scope_type,
        entity_id=entity_id,
        summary_type=summary_type,
        text=text,
        model_name=stored_model,
        prompt_version=prompt_version,
        provider_requested=provider_requested,
        provider_used=provider_used,
        fallback=fallback,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_by_user_id=created_by_user_id,
        params={
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id is not None else None,
            "summary_type": summary_type,
            "max_sentences": max_sentences,
            "model_name": model_name,
            # Read back by GET /ai/summaries/latest (S15 async completion).
            "work_count": len(works),
        },
    )
    db.add(summary)
    db.flush()
    # A ``fallback_reason`` is transient (not a column): shown only on a fresh degrade.
    summary.fallback_reason = fallback_reason if fallback else None
    return summary, len(abstracts)
