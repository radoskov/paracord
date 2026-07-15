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
LLM_PROMPT_VERSION = "local-llm-v2-map-reduce"

# Per-LLM-call input budget (characters, ≈ chars/4 tokens). Conservative on purpose: small local
# models often run with a 4-8k-token context. Scope summaries CHUNK to this budget (map-reduce)
# instead of silently truncating like v1 did.
LLM_INPUT_CHAR_BUDGET = 11000

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


def _ollama_generate(prompt: str, *, model: str, base_url: str) -> str:
    """Raw single-shot generation against a local Ollama daemon. Raises on any failure."""
    import httpx2 as httpx

    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        return (response.json().get("response") or "").strip()


def _ollama_summarize(text: str, *, model: str, base_url: str) -> str:
    """Per-PAPER SHORT abstractive summary (single-paragraph prompt). Raises on any failure."""
    prompt = (
        "Summarize the following academic paper text in 3-4 sentences, focusing on the problem, "
        "method, and key result. Respond with the summary only.\n\n" + text[:LLM_INPUT_CHAR_BUDGET]
    )
    return _ollama_generate(prompt, model=model, base_url=base_url)


_DETAIL_CHUNK_PROMPT = (
    "This is one section of an academic paper. Summarize it into a single focused paragraph "
    "covering what it establishes (problem, method, data, or result). Respond with the paragraph "
    "only.\n\n"
)
_DETAIL_INTRO_PROMPT = (
    "The paragraphs below are section-by-section summaries of ONE academic paper. Write a 2-3 "
    "sentence high-level overview of the whole paper (problem, approach, key result). Respond "
    "with the overview only.\n\n"
)


def _split_to_budget(block: str, budget: int) -> list[str]:
    """Split a block longer than ``budget`` on sentence boundaries into ≤budget pieces."""
    if len(block) <= budget:
        return [block]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(block):
        if current and len(current) + len(sentence) > budget:
            pieces.append(current.strip())
            current = ""
        current += sentence + " "
    if current.strip():
        pieces.append(current.strip())
    return pieces or [block[:budget]]


def _paragraphs(text: str, budget: int) -> list[str]:
    """Split body text into map blocks: paragraph breaks first, then sentence-split any block
    still over ``budget`` (GROBID body extraction often drops paragraph breaks, leaving one blob).
    """
    raw = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    raw = raw or ([text.strip()] if text.strip() else [])
    out: list[str] = []
    for block in raw:
        out.extend(_split_to_budget(block, budget))
    return out


def _detailed_llm_summary(text: str, *, model: str, base_url: str) -> str:
    """Per-PAPER DETAILED summary (UX batch 4): the WHOLE body — no 12k clip — packed into
    context-window chunks, each condensed into its own paragraph, with a high-level intro
    synthesized from those paragraphs when there is more than one chunk. Raises on any failure.
    """
    chunks = _pack_blocks(_paragraphs(text, LLM_INPUT_CHAR_BUDGET), LLM_INPUT_CHAR_BUDGET)
    if not chunks:
        return ""
    section_summaries = [
        _ollama_generate(_DETAIL_CHUNK_PROMPT + chunk, model=model, base_url=base_url)
        for chunk in chunks
    ]
    section_summaries = [s for s in section_summaries if s.strip()]
    if not section_summaries:
        return ""
    body = "\n\n".join(section_summaries)
    if len(section_summaries) == 1:
        return body
    intro = _ollama_generate(
        _DETAIL_INTRO_PROMPT + body[:LLM_INPUT_CHAR_BUDGET], model=model, base_url=base_url
    )
    return f"{intro}\n\n{body}" if intro.strip() else body


# --- Scope (collection) prompts — UX batch 4 -------------------------------------------------
# v1 fed the concatenated abstracts to the PAPER prompt, so the model opened with "This paper
# addresses…" for a whole shelf. The scope prompts below make the collection framing explicit and
# ask for the cross-collection view (problems / methods / datasets / findings), with a high-level
# overview paragraph first.

_SCOPE_DESCRIPTOR = {
    "library": "the user's entire research library",
    "shelf": "one shelf — a curated collection of papers",
    "rack": "one rack — a group of related shelves of papers",
}


def _scope_final_prompt(scope_type: str, paper_count: int, notes: str, *, from_parts: bool) -> str:
    descriptor = _SCOPE_DESCRIPTOR.get(scope_type, "a collection of papers")
    source_line = (
        "Below are condensed notes derived from the papers' individual summaries."
        if from_parts
        else 'Below are short per-paper summaries, one per line ("Title (year): summary").'
    )
    return (
        f"You are synthesizing {descriptor}, containing {paper_count} papers. {source_line}\n"
        "Write a synthesis of the COLLECTION AS A WHOLE — it is a set of papers, not one paper, "
        'so never write "this paper". Structure the answer as:\n'
        "1. A short high-level overview paragraph of what the collection covers and how the "
        "papers relate.\n"
        "2. Key problems: the main research problems addressed across the collection.\n"
        "3. Methods & algorithms: recurring or notable approaches.\n"
        "4. Datasets & benchmarks: any that are mentioned.\n"
        "5. Key findings: the most important results, attributed to their papers by title where "
        "specific.\n"
        "Be concise but do not omit distinct contributions. Respond with the synthesis only.\n\n"
        + notes
    )


def _scope_chunk_prompt(scope_type: str, part: int, parts: int, notes: str) -> str:
    descriptor = _SCOPE_DESCRIPTOR.get(scope_type, "a collection of papers")
    return (
        f"The following are per-paper summaries from part {part} of {parts} of {descriptor}. "
        "Condense them into compact notes covering the research problems, methods/algorithms, "
        "datasets/benchmarks and key findings, KEEPING the paper-title attributions. Respond "
        "with the condensed notes only.\n\n" + notes
    )


def _pack_blocks(blocks: list[str], budget: int) -> list[str]:
    """Greedily pack digest blocks into chunks of at most ``budget`` characters (≥1 block each)."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > budget:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(block)
        size += len(block) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def summarize_work(
    db: Session,
    work: Work,
    *,
    summary_type: str = "extractive",
    detail: str = "short",
    max_sentences: int = 5,
    model_name: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> Summary:
    """Create (replacing any prior summary of the same type+detail) a provenance-tagged summary.

    ``detail`` (UX batch 4): ``"short"`` = one paragraph (the historical behavior); ``"detailed"``
    = the whole body chunked section-by-section with a synthesized intro (LLM), or a longer
    extractive summary as the fallback. Short and detailed are stored as SEPARATE rows (the
    detailed one under ``{summary_type}_detailed``) so both coexist in the paper view.

    For ``local_llm`` the returned Summary carries a transient ``source_sections`` attribute (the
    sources that fed it); the API surfaces it. When the LLM is disabled/unreachable the text is the
    extractive fallback but the requested model is still recorded, with a fallback note in
    ``source_sections``.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")
    if detail not in ("short", "detailed"):
        raise ValueError(f"Unsupported detail level: {detail}")
    # Detailed summaries are stored under a distinct type so they never clobber the short one.
    stored_type = summary_type if detail == "short" else f"{summary_type}_detailed"
    detailed = detail == "detailed"
    # A longer extractive stands in for a detailed summary when the LLM isn't producing it.
    eff_sentences = max_sentences * 3 if detailed else max_sentences

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
                text = (
                    _detailed_llm_summary(
                        source_text, model=stored_model, base_url=ai_cfg.ollama_url
                    )
                    if detailed
                    else _ollama_summarize(
                        source_text, model=stored_model, base_url=ai_cfg.ollama_url
                    )
                )
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                logger.warning("local_llm summary unavailable (%s); using extractive fallback", exc)
                fallback_reason = str(exc) or "the local LLM is unavailable"
        else:
            fallback_reason = "the local LLM is not enabled"
        if not text:
            text = summarize_extractive(source_text, max_sentences=eff_sentences)
            provider_used = "extractive"
            if text:
                source_sections.append("extractive-fallback")
    else:
        text = summarize_extractive(work_source_text(db, work), max_sentences=eff_sentences)
        stored_model = "tier1-extractive-frequency"

    if not text:
        raise ValueError("No text available to summarize")

    # One stored summary per (work, type+detail): replace the previous one so re-runs stay idempotent.
    db.execute(
        delete(Summary).where(
            Summary.entity_type == "work",
            Summary.entity_id == work.id,
            Summary.summary_type == stored_type,
        )
    )
    fallback = provider_used != provider_requested
    summary = Summary(
        entity_type="work",
        entity_id=work.id,
        summary_type=stored_type,
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
            "detail": detail,
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


def _work_digest(
    db: Session,
    work: Work,
    *,
    use_llm: bool,
    model_name: str,
    created_by_user_id: uuid.UUID | None,
    settings: Settings | None,
    detail: str = "short",
    regenerate: bool = False,
) -> tuple[str, str | None] | None:
    """One paper's digest for the scope map step. Returns ``(text, degraded_reason)`` or None.

    UX batch 4: ``detail`` picks which per-paper summary feeds the scope synthesis ("short" or
    "detailed"); ``regenerate`` forces a fresh one. When not regenerating, an existing genuine
    local_llm summary of that detail level is reused (so a scope run doesn't re-summarize papers
    already summarized in their own view, and vice versa). ``degraded_reason`` is non-None when the
    LLM attempt fell back — the scope loop uses it to stop hammering an unavailable daemon.
    """
    stored_type = "local_llm" if detail == "short" else "local_llm_detailed"
    if use_llm:
        if not regenerate:
            existing = db.scalars(
                select(Summary)
                .where(
                    Summary.entity_type == "work",
                    Summary.entity_id == work.id,
                    Summary.summary_type == stored_type,
                )
                .order_by(Summary.created_at.desc())
            ).first()
            if existing is not None and existing.provider_used == "local_llm" and existing.text:
                return existing.text, None
        try:
            s = summarize_work(
                db,
                work,
                summary_type="local_llm",
                detail=detail,
                model_name=model_name,
                created_by_user_id=created_by_user_id,
                settings=settings,
            )
        except ValueError:
            return None  # nothing to summarize for this paper
        if s.fallback:
            reason = getattr(s, "fallback_reason", None) or "the local LLM is unavailable"
            return s.text, reason
        return s.text, None
    if work.abstract:
        return work.abstract.strip(), None
    text = work_source_text(db, work)
    if not text:
        return None
    return summarize_extractive(text, max_sentences=4), None


def _scope_label(db: Session, scope_type: str, scope_id: uuid.UUID | None) -> str | None:
    """Human label for the summarized scope ("whole library" / shelf name / rack name)."""
    if scope_type == "library":
        return "whole library"
    if scope_id is None:
        return None
    from app.models.organization import Rack, Shelf  # noqa: PLC0415 (avoid import cycle)

    container = db.get(Shelf if scope_type == "shelf" else Rack, scope_id)
    return getattr(container, "name", None)


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
    paper_detail: str = "short",
    regenerate_papers: bool = False,
    progress_cb=None,
    cancel_cb=None,
) -> tuple[Summary, int]:
    """Generate (replacing prior) a scope summary over the scope's papers.

    ``local_llm`` builds per-paper digests (map) then synthesizes the collection (reduce); UX
    batch 4 adds ``paper_detail`` ("short"/"detailed") to choose which per-paper summary feeds the
    map, and ``regenerate_papers`` to force those per-paper summaries to be regenerated rather than
    reused. Degrades to the extractive engine on any LLM failure. Returns ``(summary, work_count)``;
    ``visible_ids`` (Phase H) restricts the scope to works the caller may see.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")
    settings = settings or get_settings()
    from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

    ai_cfg = get_ai_config(db, settings=settings)
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID

    abstracts = [w.abstract for w in works if w.abstract]
    if not abstracts and summary_type != "local_llm":
        raise ValueError(f"No abstracts available in {scope_type!r} scope to summarize")
    combined = " ".join(abstracts)

    provider_requested = summary_type
    provider_used = summary_type
    fallback_reason: str | None = None
    prompt_version = PROMPT_VERSION
    method = "extractive"
    chunk_count = 0
    text = ""
    if summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        # Map step (UX batch 4): build one digest line per paper. When the LLM is on, per-paper
        # summaries are generated through summarize_work — so they are also PERSISTED and show up
        # in each paper's own view — and reused on the next scope run. A single LLM outage stops
        # further attempts (cheap digests for the rest) instead of stacking N timeouts.
        digests: list[str] = []
        llm_ok = ai_cfg.summary_provider == "local_llm"
        if not llm_ok:
            fallback_reason = "the local LLM is not enabled"
        for idx, work in enumerate(works):
            if cancel_cb is not None and cancel_cb():
                from app.workers.queue import JobCancelled  # noqa: PLC0415 (cycle guard)

                raise JobCancelled(f"cancelled after {idx} of {len(works)} papers")
            if progress_cb is not None:
                progress_cb(idx, len(works))
            digest = _work_digest(
                db,
                work,
                use_llm=llm_ok and fallback_reason is None,
                model_name=stored_model,
                created_by_user_id=created_by_user_id,
                settings=settings,
                detail=paper_detail,
                regenerate=regenerate_papers,
            )
            if digest is None:
                continue
            digest_text, degraded_reason = digest
            if degraded_reason and fallback_reason is None and llm_ok:
                fallback_reason = degraded_reason
            title = (work.canonical_title or "Untitled").strip()
            year = f" ({work.year})" if work.year else ""
            digests.append(f"- {title}{year}: {digest_text}")
        if not digests:
            raise ValueError(f"No text available in {scope_type!r} scope to summarize")

        # Reduce step: pack the digests into context-window-sized chunks; condense each chunk,
        # then synthesize the final collection summary (single chunk skips the middle pass).
        if fallback_reason is None:
            try:
                chunks = _pack_blocks(digests, LLM_INPUT_CHAR_BUDGET)
                chunk_count = len(chunks)
                if len(chunks) == 1:
                    text = _ollama_generate(
                        _scope_final_prompt(scope_type, len(works), chunks[0], from_parts=False),
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                    )
                else:
                    partials = [
                        _ollama_generate(
                            _scope_chunk_prompt(scope_type, i + 1, len(chunks), chunk),
                            model=stored_model,
                            base_url=ai_cfg.ollama_url,
                        )
                        for i, chunk in enumerate(chunks)
                    ]
                    final_notes = "\n\n".join(partials)[:LLM_INPUT_CHAR_BUDGET]
                    text = _ollama_generate(
                        _scope_final_prompt(scope_type, len(works), final_notes, from_parts=True),
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                    )
                method = "map_reduce"
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                logger.warning("local_llm scope summary unavailable (%s); extractive fallback", exc)
                fallback_reason = str(exc) or "the local LLM is unavailable"
        if not text:
            # Extractive fallback over the per-paper digests (still better than raw abstracts).
            text = summarize_extractive(
                " ".join(d.lstrip("- ") for d in digests), max_sentences=max_sentences
            )
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
            # UX batch 4: which scope this summarizes (shown as the summary's title) + how.
            "scope_label": _scope_label(db, scope_type, scope_id),
            "method": method,
            "chunks": chunk_count,
            "paper_detail": paper_detail,
        },
    )
    db.add(summary)
    db.flush()
    # A ``fallback_reason`` is transient (not a column): shown only on a fresh degrade.
    summary.fallback_reason = fallback_reason if fallback else None
    return summary, len(works)
