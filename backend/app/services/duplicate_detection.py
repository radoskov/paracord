"""Duplicate and version-candidate detection."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DuplicateCandidate:
    work_id: str
    reason: str
    confidence: float


def find_candidates(*, sha256: str | None = None, doi: str | None = None, arxiv_id: str | None = None) -> list[DuplicateCandidate]:
    """Find duplicate/version candidates.

    TODO: Implement DB-backed exact hash, DOI, arXiv, title-author-year, and text-fingerprint checks.
    """
    _ = (sha256, doi, arxiv_id)
    return []
