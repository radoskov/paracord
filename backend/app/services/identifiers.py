"""Shared bibliographic identifier helpers."""

import re
from typing import TYPE_CHECKING

from app.utils.normalization import normalize_doi

if TYPE_CHECKING:
    from app.models.work import Work

_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)
_ARXIV_PREFIXES = ("arXiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/")


def arxiv_base_id(arxiv_id: str | None) -> str | None:
    """Return the version-less arXiv base id (e.g. '1706.03762' from '1706.03762v1').

    Returns None when the input is None or empty.
    """
    if not arxiv_id:
        return None
    cleaned = arxiv_id.strip()
    for prefix in _ARXIV_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    return _ARXIV_VERSION_SUFFIX.sub("", cleaned) or None


def _identifier_locked(work: "Work", field_name: str) -> bool:
    """A field is locked if the whole work is user-confirmed or the field is individually locked."""
    return bool(getattr(work, "user_confirmed", False)) or field_name in (
        getattr(work, "confirmed_fields", None) or []
    )


def backfill_identifiers(
    work: "Work", *, doi: str | None = None, arxiv_id: str | None = None
) -> list[str]:
    """Fill an EMPTY ``arxiv_id`` / ``doi`` from a known source, normalizing first.

    Only sets a field that is currently empty and not user-locked (SPEC §8.12) — a confirmed value
    is never overwritten. Returns the names of the fields that were filled (for auditing).
    """
    changed: list[str] = []
    if arxiv_id and not (work.arxiv_id or "").strip() and not _identifier_locked(work, "arxiv_id"):
        cleaned = arxiv_id.strip()
        work.arxiv_id = cleaned
        work.arxiv_base_id = arxiv_base_id(cleaned)
        changed.append("arxiv_id")
    if doi and not (work.doi or "").strip() and not _identifier_locked(work, "doi"):
        work.doi = normalize_doi(doi)
        changed.append("doi")
    return changed
