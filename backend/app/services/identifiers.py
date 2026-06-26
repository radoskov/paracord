"""Shared bibliographic identifier helpers."""

import re

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
