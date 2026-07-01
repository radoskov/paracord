"""CSL rendering engine: turns CSL-JSON items into styled reference strings via citeproc-py.

The public entry point is :func:`render_bibliography`, which is used by the export service for the
``styled`` output format. Styles are backed by the ``.csl`` files bundled under ``styles/`` (see
this package's ``NOTICE`` for CC-BY-SA attribution); the en-US locale ships with citeproc-py.

Rendering is defensive: if citeproc fails to render an item (bad or missing data, or a style/locale
quirk), that item degrades to a minimal safe string rather than crashing the whole export.
"""

from __future__ import annotations

import logging
import os
from functools import cache

logger = logging.getLogger(__name__)

_STYLES_DIR = os.path.join(os.path.dirname(__file__), "styles")

# Public style keys -> (bundled .csl basename, human label). ``apa``/``ieee``/``chicago`` keep the
# keys used before Phase B4; the rest are added now that real CSL makes them cheap.
_STYLE_REGISTRY: dict[str, tuple[str, str]] = {
    "apa": ("apa", "APA (7th edition)"),
    "ieee": ("ieee", "IEEE"),
    "chicago": ("chicago-author-date", "Chicago (author-date)"),
    "mla": ("modern-language-association", "MLA (9th edition)"),
    "harvard": ("harvard-cite-them-right", "Harvard (Cite Them Right)"),
    "vancouver": ("vancouver", "Vancouver"),
    "nature": ("nature", "Nature"),
}

# Ordered tuple of supported style keys (stable order for the UI dropdown).
CITATION_STYLES: tuple[str, ...] = tuple(_STYLE_REGISTRY)
# Human labels keyed by style, for the frontend selector.
STYLE_LABELS: dict[str, str] = {key: label for key, (_, label) in _STYLE_REGISTRY.items()}


def available_styles() -> list[dict[str, str]]:
    """Return the offered citation styles as ``[{"value", "label"}, ...]`` for the API/UI."""
    return [{"value": key, "label": label} for key, (_, label) in _STYLE_REGISTRY.items()]


def _style_path(style: str) -> str:
    basename, _ = _STYLE_REGISTRY[style]
    return os.path.join(_STYLES_DIR, f"{basename}.csl")


@cache
def _load_style(style: str):
    """Load and cache a parsed :class:`CitationStylesStyle` for a style key."""
    from citeproc import CitationStylesStyle  # lazy: keeps import cost off the hot path

    return CitationStylesStyle(_style_path(style), validate=False)


def render_bibliography(items: list[dict], style: str) -> str:
    """Render CSL-JSON ``items`` as a reference list in ``style`` (a key in ``CITATION_STYLES``).

    Each item must carry a unique ``id``. Rendering is per-item defensive: if citeproc raises for a
    given item, that entry falls back to a minimal ``author, "title", year`` string and a warning is
    logged, so one malformed record never fails the whole export.
    """
    style = (style or "apa").lower()
    if style not in _STYLE_REGISTRY:
        raise ValueError(
            f"Unsupported citation style: {style} (allowed: {', '.join(CITATION_STYLES)})"
        )
    if not items:
        return ""

    from citeproc import (
        Citation,
        CitationItem,
        CitationStylesBibliography,
        formatter,
    )
    from citeproc.source.json import CiteProcJSON

    csl_style = _load_style(style)
    source = CiteProcJSON(items)
    bibliography = CitationStylesBibliography(csl_style, source, formatter.plain)
    for item in items:
        bibliography.register(Citation([CitationItem(item["id"])]))

    try:
        rendered = bibliography.bibliography()
    except Exception:  # noqa: BLE001 — whole-list failure: fall back item-by-item below.
        logger.warning("CSL bibliography render failed for style %r; using fallback", style)
        return "\n".join(_fallback(item) for item in items)

    lines: list[str] = []
    for item, entry in zip(items, rendered, strict=False):
        try:
            text = "".join(str(part) for part in entry).strip()
        except Exception:  # noqa: BLE001 — per-item quirk: degrade just this entry.
            logger.warning("CSL render failed for item %r; using fallback", item.get("id"))
            text = _fallback(item)
        lines.append(text or _fallback(item))
    # If citeproc returned fewer entries than items (rare), append fallbacks for the remainder.
    for item in items[len(lines) :]:
        lines.append(_fallback(item))
    return "\n".join(lines)


def _fallback(item: dict) -> str:
    """Minimal safe citation string when CSL rendering is unavailable for an item."""
    authors = item.get("author") or []
    names = [
        ", ".join(p for p in (a.get("family"), a.get("given")) if p)
        if isinstance(a, dict)
        else str(a)
        for a in authors
    ]
    who = "; ".join(n for n in names if n)
    title = item.get("title") or "Untitled"
    issued = item.get("issued", {}).get("date-parts") or []
    year = issued[0][0] if issued and issued[0] else None
    venue = item.get("container-title")
    doi = item.get("DOI")
    parts = []
    if who:
        parts.append(who + ".")
    parts.append(f'"{title}."')
    if venue:
        parts.append(f"{venue}.")
    if year:
        parts.append(f"{year}.")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(parts).strip()
