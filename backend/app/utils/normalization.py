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
