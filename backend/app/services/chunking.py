"""Section-aware passage chunking for chunk-level semantic search (HYBRID-SEARCH-DESIGN §3.1).

A work is split into passages: its title, its abstract, and each GROBID/TEI body section, with
references/acknowledgment sections skipped. Long sections are packed into ~target-token chunks
(whole sentences, hard-capped) with a small word-level overlap so a concept that straddles a
boundary is still recoverable. Deterministic — no randomness — so re-chunking a work is idempotent.

Chunks are the embedding unit (HS2); the lexical BM25F+ engine (HS4) works document-level and does
not use these.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.chunk import WorkChunk
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.tei_parser import extract_sections

# Target/cap/overlap in whitespace "tokens" (a cheap proxy for real tokens, fine for sizing).
CHUNK_TARGET_TOKENS = 400
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 60

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Section labels that add noise rather than signal to semantic retrieval.
_SKIP_SECTION = re.compile(r"reference|bibliograph|acknowledg", re.IGNORECASE)


def _count_tokens(text: str) -> int:
    return len(text.split())


def _split_sentences(text: str) -> list[str]:
    cleaned = " ".join((text or "").split())
    return [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s.strip()]


def _word_windows(sentence: str, max_tokens: int) -> list[str]:
    """Split an over-long single sentence into consecutive word windows of <= max_tokens."""
    words = sentence.split()
    return [" ".join(words[i : i + max_tokens]) for i in range(0, len(words), max_tokens)]


def chunk_text(
    text: str,
    *,
    target: int = CHUNK_TARGET_TOKENS,
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Pack ``text`` into chunks of whole sentences, ~``target`` tokens each, hard-capped at
    ``max_tokens``, carrying a ``overlap``-word suffix of each chunk into the next.

    A single sentence longer than ``max_tokens`` is first split into word windows so no chunk ever
    exceeds the cap. Returns ``[]`` for empty text.
    """
    units: list[str] = []
    for sentence in _split_sentences(text):
        n = _count_tokens(sentence)
        if n > max_tokens:
            units.extend(_word_windows(sentence, max_tokens))
        elif n > 0:
            units.append(sentence)

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    dirty = False  # a real unit has been added since the last flush

    def flush() -> None:
        nonlocal current, current_tokens, dirty
        chunk = " ".join(current).strip()
        if not chunk:
            return
        chunks.append(chunk)
        words = chunk.split()
        tail = words[-overlap:] if overlap > 0 else []
        current = [" ".join(tail)] if tail else []
        current_tokens = len(tail)
        dirty = False

    for unit in units:
        n = _count_tokens(unit)
        if current and current_tokens + n > max_tokens:
            flush()
        current.append(unit)
        current_tokens += n
        dirty = True
        if current_tokens >= target:
            flush()

    if current and dirty:
        chunks.append(" ".join(current).strip())
    return chunks


def _is_skippable(label: str | None) -> bool:
    return bool(label and _SKIP_SECTION.search(label))


def iter_work_sections(db: Session, work: Work) -> list[tuple[str | None, str]]:
    """Ordered ``(section_label, text)`` sources for a work: title, abstract, then TEI body sections
    (references/acknowledgments skipped). Falls back to just title+abstract when no TEI is stored."""
    sections: list[tuple[str | None, str]] = []
    if work.canonical_title:
        sections.append(("title", work.canonical_title))
    if work.abstract:
        sections.append(("abstract", work.abstract))
    tei = db.scalar(
        select(RawTeiDocument)
        .where(RawTeiDocument.work_id == work.id)
        .order_by(RawTeiDocument.created_at.desc())
    )
    if tei is not None:
        for label, text in extract_sections(tei.tei_xml):
            if _is_skippable(label):
                continue
            sections.append((label, text))
    return sections


def build_chunks_for_work(db: Session, work: Work) -> list[dict]:
    """Compute (but do not persist) the ordered chunk records for a work."""
    chunks: list[dict] = []
    position = 0
    for label, text in iter_work_sections(db, work):
        for piece in chunk_text(text):
            chunks.append(
                {
                    "section": label,
                    "position": position,
                    "text": piece,
                    "token_count": _count_tokens(piece),
                }
            )
            position += 1
    return chunks


def rechunk_work(db: Session, work: Work) -> int:
    """Replace a work's chunks with a freshly-computed set. Idempotent; returns the chunk count.

    Deleting the work's chunks cascades to their embeddings (HS2) via ``work_chunks`` FK, so a
    re-chunk correctly invalidates stale vectors; the embedding job re-embeds the new chunks.
    """
    db.execute(delete(WorkChunk).where(WorkChunk.work_id == work.id))
    records = build_chunks_for_work(db, work)
    for record in records:
        db.add(WorkChunk(work_id=work.id, **record))
    db.flush()
    return len(records)


def chunk_work_by_id(db: Session, work_id: uuid.UUID) -> int:
    """Re-chunk a work by id; no-op (returns 0) if the work is missing."""
    work = db.get(Work, work_id)
    if work is None:
        return 0
    return rechunk_work(db, work)
