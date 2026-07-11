"""Duplicate and version-candidate detection."""

import re
import uuid
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.work import Work
from app.utils.normalization import normalize_doi, normalize_title

FUZZY_TITLE_THRESHOLD = 0.92


def _title_ratio(a: str, b: str) -> float:
    """Similarity in [0, 1]. Uses rapidfuzz when installed (fast C impl), else stdlib difflib."""
    try:
        from rapidfuzz.fuzz import ratio  # noqa: PLC0415 (optional dependency)

        return ratio(a, b) / 100.0
    except ImportError:
        return SequenceMatcher(None, a, b).ratio()


def _blocking_key(normalized_title: str) -> str:
    """First token of the normalized title — the blocking key that bounds fuzzy comparisons.

    Only works whose normalized title starts with the same token are compared, turning the former
    O(n²) all-pairs scan into a per-block scan. This is the standard blocking tradeoff: titles that
    differ in their first word are not fuzzy-matched (DOI/arXiv exact matching still catches those).
    """
    return normalized_title.split(" ", 1)[0] if normalized_title else ""


_ARXIV_VERSION_RE = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v(?P<version>\d+))?$",
    re.IGNORECASE,
)


def scan_duplicate_candidates(
    db: Session,
    *,
    work: Work | None = None,
    file: File | None = None,
) -> list[DuplicateCandidate]:
    """Generate duplicate/version candidates for a work and/or file."""
    candidates: list[DuplicateCandidate] = []
    if work is not None:
        candidates.extend(find_work_candidates(db, work=work))
    if file is not None:
        candidates.extend(find_file_candidates(db, file=file))
    return candidates


def find_work_candidates(db: Session, *, work: Work) -> list[DuplicateCandidate]:
    """Find work-level candidates by DOI, arXiv base ID, and fuzzy title."""
    # A merged shadow (Batch D) is already resolved into its base — never re-propose it.
    if work.merged_into_id is not None:
        return []
    candidates: list[DuplicateCandidate] = []
    candidates.extend(_same_doi_candidates(db, work))
    candidates.extend(_same_arxiv_candidates(db, work))
    candidates.extend(_shared_file_candidates(db, work))
    candidates.extend(_fuzzy_title_candidates(db, work))
    return candidates


def _shared_file_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    """Flag other works attached to the SAME PDF (same File row via SHA-256 dedup).

    File dedup collapses an identical PDF to one ``File`` linked to several works, which the
    file-level detectors (which compare two distinct File rows) can't see. This detector reads
    ``FileWorkLink`` so two papers sharing one PDF are flagged even when their title/DOI differ.
    """
    file_ids = db.scalars(select(FileWorkLink.file_id).where(FileWorkLink.work_id == work.id)).all()
    if not file_ids:
        return []
    rows = db.execute(
        select(FileWorkLink.work_id, File.sha256)
        .join(File, File.id == FileWorkLink.file_id)
        .join(Work, Work.id == FileWorkLink.work_id)
        .where(
            FileWorkLink.file_id.in_(file_ids),
            FileWorkLink.work_id != work.id,
            Work.merged_into_id.is_(None),
        )
    ).all()
    candidates: list[DuplicateCandidate] = []
    seen: set[uuid.UUID] = set()  # one candidate per work pair, even if they share several files
    for other_id, sha in rows:
        if other_id in seen:
            continue
        seen.add(other_id)
        candidates.append(
            _upsert_candidate(
                db,
                candidate_type="shared_file",
                entity_a_type="work",
                entity_a_id=work.id,
                entity_b_type="work",
                entity_b_id=other_id,
                score=1.0,
                signals={"sha256": sha} if sha else {},
            )
        )
    return candidates


def find_file_candidates(db: Session, *, file: File) -> list[DuplicateCandidate]:
    """Find file-level candidates by exact SHA-256 and text fingerprint."""
    candidates: list[DuplicateCandidate] = []
    if file.sha256:
        matches = db.scalars(
            select(File).where(File.sha256 == file.sha256, File.id != file.id)
        ).all()
        for other in matches:
            candidates.append(
                _upsert_candidate(
                    db,
                    candidate_type="exact_file",
                    entity_a_type="file",
                    entity_a_id=file.id,
                    entity_b_type="file",
                    entity_b_id=other.id,
                    score=1.0,
                    signals={"sha256": file.sha256},
                )
            )

    if file.text_fingerprint:
        matches = db.scalars(
            select(File).where(
                File.text_fingerprint == file.text_fingerprint,
                File.id != file.id,
            )
        ).all()
        for other in matches:
            candidates.append(
                _upsert_candidate(
                    db,
                    candidate_type="text_fingerprint",
                    entity_a_type="file",
                    entity_a_id=file.id,
                    entity_b_type="file",
                    entity_b_id=other.id,
                    score=0.98,
                    signals={"text_fingerprint": file.text_fingerprint},
                )
            )
    candidates.extend(_multiwork_file_candidates(db, file))
    return candidates


def _multiwork_file_candidates(db: Session, file: File) -> list[DuplicateCandidate]:
    signals = _multiwork_signals(file)
    if not signals:
        return []
    return [
        _upsert_candidate(
            db,
            candidate_type="multiwork_file",
            entity_a_type="file",
            entity_a_id=file.id,
            entity_b_type="file",
            entity_b_id=file.id,
            score=signals.pop("score"),
            signals=signals,
        )
    ]


def _multiwork_signals(file: File) -> dict[str, Any]:
    preview = (file.preview_text or "").lower()
    abstract_count = preview.count("abstract")
    references_count = preview.count("references")
    title_like_markers = sum(
        marker in preview
        for marker in [
            "proceedings",
            "table of contents",
            "contents",
            "session ",
            "paper ",
        ]
    )
    if abstract_count >= 2 or references_count >= 2:
        return {
            "score": 0.78,
            "reason": "multiple_section_markers",
            "abstract_count": abstract_count,
            "references_count": references_count,
        }
    if (file.page_count or 0) >= 40 and title_like_markers >= 2:
        return {
            "score": 0.68,
            "reason": "proceedings_like_preview",
            "page_count": file.page_count,
            "title_like_marker_count": title_like_markers,
        }
    return {}


def _same_doi_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    if not work.doi:
        return []
    doi = normalize_doi(work.doi)
    # SQL-pushdown: DOIs are stored normalized so exact equality works.
    others = db.scalars(
        select(Work).where(Work.doi == doi, Work.id != work.id, Work.merged_into_id.is_(None))
    ).all()
    return [
        _upsert_candidate(
            db,
            candidate_type="same_doi",
            entity_a_type="work",
            entity_a_id=work.id,
            entity_b_type="work",
            entity_b_id=other.id,
            score=1.0,
            signals={"doi": doi},
        )
        for other in others
    ]


def _same_arxiv_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    base = work.arxiv_base_id or (split_arxiv_id(work.arxiv_id)["base"] if work.arxiv_id else None)
    if not base:
        return []

    # SQL-pushdown: filter by the indexed arxiv_base_id column when available.
    if work.arxiv_base_id:
        others = db.scalars(
            select(Work).where(
                Work.arxiv_base_id == base,
                Work.id != work.id,
                Work.merged_into_id.is_(None),
            )
        ).all()
    else:
        others = [
            other
            for other in db.scalars(
                select(Work).where(
                    Work.arxiv_id.is_not(None),
                    Work.id != work.id,
                    Work.merged_into_id.is_(None),
                )
            ).all()
            if split_arxiv_id(other.arxiv_id)["base"] == base
        ]

    candidates: list[DuplicateCandidate] = []
    arxiv = split_arxiv_id(work.arxiv_id)
    for other in others:
        other_arxiv = split_arxiv_id(other.arxiv_id)
        score = 1.0
        signals: dict[str, Any] = {
            "arxiv_base_id": base,
            "arxiv_id": work.arxiv_id,
            "other_arxiv_id": other.arxiv_id,
        }
        if arxiv["version"] != other_arxiv["version"]:
            signals["version_mismatch"] = True
            score = 0.99
        candidates.append(
            _upsert_candidate(
                db,
                candidate_type="same_arxiv",
                entity_a_type="work",
                entity_a_id=work.id,
                entity_b_type="work",
                entity_b_id=other.id,
                score=score,
                signals=signals,
            )
        )
    return candidates


def _fuzzy_title_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    title = work.normalized_title or normalize_title(work.canonical_title or "")
    if not title:
        return []

    candidates: list[DuplicateCandidate] = []
    block = _blocking_key(title)
    # Blocking: only compare works whose normalized title starts with the same first token.
    stmt = select(Work).where(Work.id != work.id, Work.merged_into_id.is_(None))
    if block:
        stmt = stmt.where(Work.normalized_title.like(f"{block}%"))
    for other in db.scalars(stmt):
        if work.year and other.year and work.year != other.year:
            continue
        other_title = other.normalized_title or normalize_title(other.canonical_title or "")
        if not other_title:
            continue
        score = _title_ratio(title, other_title)
        if score < FUZZY_TITLE_THRESHOLD:
            continue
        candidates.append(
            _upsert_candidate(
                db,
                candidate_type="fuzzy_title",
                entity_a_type="work",
                entity_a_id=work.id,
                entity_b_type="work",
                entity_b_id=other.id,
                score=score,
                signals={
                    "normalized_title": title,
                    "other_normalized_title": other_title,
                    "year": work.year,
                    "other_year": other.year,
                },
            )
        )
    return candidates


def split_arxiv_id(arxiv_id: str | None) -> dict[str, str | None]:
    """Return arXiv base ID and optional version suffix.

    Tolerates the decorations seen in extracted references and metadata: ``http``/``https`` schemes,
    the ``arxiv.org/abs/`` and ``arxiv.org/pdf/`` paths, an ``arXiv:`` prefix (any case), and a
    trailing ``.pdf``. Prefix stripping is case-insensitive (the id is lowercased first; the version
    regex is ``re.IGNORECASE`` and the emitted base is lowercase anyway).
    """
    if not arxiv_id:
        return {"base": None, "version": None}
    cleaned = arxiv_id.strip().lower()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)
    for prefix in ("arxiv.org/abs/", "arxiv.org/pdf/", "arxiv:"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = cleaned.removesuffix(".pdf")
    match = _ARXIV_VERSION_RE.match(cleaned)
    if not match:
        return {"base": cleaned.lower(), "version": None}
    version = match.group("version")
    return {
        "base": match.group("base").lower(),
        "version": f"v{version}" if version else None,
    }


def _upsert_candidate(
    db: Session,
    *,
    candidate_type: str,
    entity_a_type: str,
    entity_a_id: uuid.UUID,
    entity_b_type: str,
    entity_b_id: uuid.UUID,
    score: float,
    signals: dict[str, Any],
) -> DuplicateCandidate:
    entity_a_type, entity_a_id, entity_b_type, entity_b_id = _canonical_pair(
        entity_a_type,
        entity_a_id,
        entity_b_type,
        entity_b_id,
    )
    candidate = db.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.candidate_type == candidate_type,
            DuplicateCandidate.entity_a_type == entity_a_type,
            DuplicateCandidate.entity_a_id == entity_a_id,
            DuplicateCandidate.entity_b_type == entity_b_type,
            DuplicateCandidate.entity_b_id == entity_b_id,
        )
    )
    if candidate is None:
        candidate = DuplicateCandidate(
            candidate_type=candidate_type,
            entity_a_type=entity_a_type,
            entity_a_id=entity_a_id,
            entity_b_type=entity_b_type,
            entity_b_id=entity_b_id,
            score=score,
            signals=signals,
            status="open",
        )
        db.add(candidate)
        db.flush()
    elif candidate.status == "open":
        candidate.score = max(candidate.score, score)
        candidate.signals = {**(candidate.signals or {}), **signals}
    return candidate


def _canonical_pair(
    entity_a_type: str,
    entity_a_id: uuid.UUID,
    entity_b_type: str,
    entity_b_id: uuid.UUID,
) -> tuple[str, uuid.UUID, str, uuid.UUID]:
    left = (entity_a_type, str(entity_a_id))
    right = (entity_b_type, str(entity_b_id))
    if left <= right:
        return entity_a_type, entity_a_id, entity_b_type, entity_b_id
    return entity_b_type, entity_b_id, entity_a_type, entity_a_id
