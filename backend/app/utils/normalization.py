"""Normalization helpers for metadata matching."""

import re


def normalize_title(title: str) -> str:
    """Normalize a title for duplicate/version matching."""
    cleaned = title.lower().strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]", "", cleaned)
    return cleaned


def normalize_doi(doi: str) -> str:
    """Normalize a DOI string."""
    return doi.strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")


_HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)")


def normalize_for_similarity(text: str) -> str:
    """Normalize text before a fuzzy comparison.

    Joins hyphenated line breaks ("infor-\\nmation" -> "information"), collapses all
    whitespace to single spaces, and lowercases so that two values differing only by
    end-of-line hyphenation or whitespace compare as identical.
    """
    joined = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    collapsed = re.sub(r"\s+", " ", joined)
    return collapsed.strip().lower()


def similarity_pct(a: str, b: str) -> float:
    """Return a 0-100 similarity between two values after similarity-normalization.

    Uses ``rapidfuzz`` when installed (falls back to stdlib ``difflib``). Combines a
    token-set ratio (robust to word reordering) with a plain ratio and keeps the
    higher of the two, so reformatted-but-identical text scores ~100.
    """
    norm_a = normalize_for_similarity(a)
    norm_b = normalize_for_similarity(b)
    if not norm_a and not norm_b:
        return 100.0
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 100.0
    try:
        from rapidfuzz.fuzz import ratio, token_set_ratio  # noqa: PLC0415

        return round(max(ratio(norm_a, norm_b), token_set_ratio(norm_a, norm_b)), 1)
    except ImportError:  # pragma: no cover - rapidfuzz is a declared dependency
        from difflib import SequenceMatcher  # noqa: PLC0415

        return round(SequenceMatcher(None, norm_a, norm_b).ratio() * 100.0, 1)
