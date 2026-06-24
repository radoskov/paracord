"""Duplicate and version-candidate detection."""

import re
import uuid
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.duplicate import DuplicateCandidate
from app.models.file import File
from app.models.work import Work
from app.utils.normalization import normalize_doi, normalize_title

FUZZY_TITLE_THRESHOLD = 0.92

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
    candidates: list[DuplicateCandidate] = []
    candidates.extend(_same_doi_candidates(db, work))
    candidates.extend(_same_arxiv_candidates(db, work))
    candidates.extend(_fuzzy_title_candidates(db, work))
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
    return candidates


def _same_doi_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    if not work.doi:
        return []
    doi = normalize_doi(work.doi)
    candidates: list[DuplicateCandidate] = []
    matches = db.scalars(select(Work).where(Work.doi.is_not(None), Work.id != work.id)).all()
    for other in matches:
        if not other.doi or normalize_doi(other.doi) != doi:
            continue
        candidates.append(
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
        )
    return candidates


def _same_arxiv_candidates(db: Session, work: Work) -> list[DuplicateCandidate]:
    if not work.arxiv_id:
        return []
    arxiv = split_arxiv_id(work.arxiv_id)
    if not arxiv["base"]:
        return []

    candidates: list[DuplicateCandidate] = []
    for other in db.scalars(select(Work).where(Work.arxiv_id.is_not(None), Work.id != work.id)):
        other_arxiv = split_arxiv_id(other.arxiv_id)
        if other_arxiv["base"] != arxiv["base"]:
            continue
        candidate_type = "same_arxiv"
        score = 1.0
        signals: dict[str, Any] = {
            "arxiv_base_id": arxiv["base"],
            "arxiv_id": work.arxiv_id,
            "other_arxiv_id": other.arxiv_id,
        }
        if arxiv["version"] != other_arxiv["version"]:
            signals["version_mismatch"] = True
            score = 0.99
        candidates.append(
            _upsert_candidate(
                db,
                candidate_type=candidate_type,
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
    for other in db.scalars(select(Work).where(Work.id != work.id)):
        if work.year and other.year and work.year != other.year:
            continue
        other_title = other.normalized_title or normalize_title(other.canonical_title or "")
        if not other_title:
            continue
        score = SequenceMatcher(None, title, other_title).ratio()
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
    """Return arXiv base ID and optional version suffix."""
    if not arxiv_id:
        return {"base": None, "version": None}
    cleaned = arxiv_id.strip().removeprefix("arXiv:").removeprefix("https://arxiv.org/abs/")
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
