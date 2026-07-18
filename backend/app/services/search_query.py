"""Structured search-query parsing (SPEC §8.7 / §14).

Turns a free-text query that may contain field operators — ``author:`` ``year:>=2020`` ``venue:``
``tag:`` ``type:`` ``title:`` ``doi:`` ``arxiv:`` ``status:`` ``shelf:`` ``rack:`` ``row:`` ``cites:``
``cited_by_local:`` ``abstract:`` ``summary:`` ``fulltext:`` ``file:`` ``duplicate:`` ``version:``
``warning:`` ``has:pdf`` ``has:references`` ``has:notes`` ``has:annotations`` ``has:summary``
``has:abstract`` ``has:grobid`` ``has:ocr`` — into a structured filter plus the leftover free
text. Quoted phrases (``author:"jane doe"``) are supported. Unknown ``key:value`` tokens fall back
to free text, so a stray colon never breaks a search.

The parser is a SAFE allowlist: only keys in :data:`_KNOWN` become structured filters and their
values are carried as plain strings — ``list_works`` binds them through the ORM (never string
interpolation) so there is no SQL-injection surface.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

_YEAR_RE = re.compile(r"^(?P<op>>=|<=|=)?(?P<a>\d{4})(?:-(?P<b>\d{4}))?$")
_KNOWN = {
    "author",
    "year",
    "venue",
    "tag",
    "type",
    "has",
    "title",
    "doi",
    "arxiv",
    "status",
    "shelf",
    "rack",
    "row",
    "cites",
    "cited_by_local",
    "keyword",
    "topic",
    "abstract",
    "summary",
    "fulltext",
    "file",
    "duplicate",
    "version",
    "warning",
}

# Values that read as "yes/any" for the boolean review-state operators (``duplicate:``/``version:``);
# anything else that is not an explicit negative is treated as an affirmative presence filter.
_TRUE = {"true", "yes", "y", "1", "open", "any", "*"}
_FALSE = {"false", "no", "n", "0", "none"}


@dataclass
class ParsedQuery:
    """Structured result of :func:`parse_search_query`: recognized field filters plus leftover free text."""

    text: str = ""  # free-text remainder (matched against title/abstract/venue)
    author: str | None = None
    venue: str | None = None
    title: str | None = None
    work_type: str | None = None
    tag: str | None = None
    doi: str | None = None
    arxiv: str | None = None
    reading_status: str | None = None
    shelf: str | None = None
    rack: str | None = None
    row: str | None = None
    cites: str | None = None
    cited_by_local: str | None = None
    keyword: str | None = None  # keyword:<text> — match within the extracted keywords list
    topic: str | None = None  # topic:<text> — match within the modelled topics list
    abstract: str | None = None  # abstract:<text> — match within the abstract column
    summary: str | None = None  # summary:<text> — match within a stored summary's text
    fulltext: str | None = None  # fulltext:<text> — match within extracted body chunks
    file_name: str | None = None  # file:<name> — match a linked file's original_filename
    year_min: int | None = None
    year_max: int | None = None
    has_pdf: bool | None = None
    has_references: bool | None = None
    has_annotations: bool | None = None
    has_summary: bool | None = None
    has_abstract: bool | None = None
    has_grobid: bool | None = None  # has:grobid — the work has GROBID TEI
    has_ocr: bool | None = None  # has:ocr — a linked file gained an OCR text layer
    duplicate: bool | None = None  # duplicate:<yes|no> — has an open duplicate candidate
    version: bool | None = None  # version:<yes|no> — is part of a version group
    # warning:<text|*> — review-warning filter. "*"/"any" matches any warning; a literal matches a
    # ``warning_state`` substring. Stored as the raw token; the builder interprets it.
    warning: str | None = None
    flags: list[str] = field(default_factory=list)  # unrecognized has:* values, for transparency


def _apply_year(parsed: ParsedQuery, value: str) -> None:
    """Parse a ``year:`` value (``YYYY``, ``>=YYYY``, ``<=YYYY``, or ``YYYY-YYYY``) into min/max bounds."""
    m = _YEAR_RE.match(value)
    if not m:
        return
    a = int(m.group("a"))
    if m.group("b"):  # range YYYY-YYYY
        parsed.year_min, parsed.year_max = a, int(m.group("b"))
    elif m.group("op") == ">=":
        parsed.year_min = a
    elif m.group("op") == "<=":
        parsed.year_max = a
    else:  # exact year
        parsed.year_min = parsed.year_max = a


def _apply_has(parsed: ParsedQuery, value: str) -> None:
    """Map a ``has:<value>`` token to its boolean flag; unrecognized values are recorded in ``flags``."""
    v = value.lower()
    if v in ("pdf", "file"):
        parsed.has_pdf = True
    elif v in ("references", "refs"):
        parsed.has_references = True
    elif v in ("no-pdf", "nofile"):
        parsed.has_pdf = False
    elif v in ("notes", "annotations"):
        parsed.has_annotations = True
    elif v == "summary":
        parsed.has_summary = True
    elif v == "abstract":
        parsed.has_abstract = True
    elif v in ("grobid", "tei"):
        parsed.has_grobid = True
    elif v == "ocr":
        parsed.has_ocr = True
    else:
        parsed.flags.append(value)


def _as_bool(value: str) -> bool:
    """Interpret a review-state operator value: explicit negatives are False, else True."""
    return value.lower() not in _FALSE


def parse_search_query(q: str | None) -> ParsedQuery:
    """Parse a query string into a :class:`ParsedQuery`."""
    parsed = ParsedQuery()
    if not (q or "").strip():
        return parsed
    try:
        tokens = shlex.split(q)
    except ValueError:
        tokens = q.split()
    free: list[str] = []
    for token in tokens:
        key, sep, value = token.partition(":")
        key_l = key.lower()
        if not sep or key_l not in _KNOWN or not value:
            free.append(token)
            continue
        if key_l == "author":
            parsed.author = value
        elif key_l == "venue":
            parsed.venue = value
        elif key_l == "title":
            parsed.title = value
        elif key_l == "type":
            parsed.work_type = value
        elif key_l == "tag":
            parsed.tag = value
        elif key_l == "keyword":
            parsed.keyword = value
        elif key_l == "topic":
            parsed.topic = value
        elif key_l == "doi":
            parsed.doi = value
        elif key_l == "arxiv":
            parsed.arxiv = value
        elif key_l == "status":
            parsed.reading_status = value
        elif key_l == "shelf":
            parsed.shelf = value
        elif key_l == "rack":
            parsed.rack = value
        elif key_l == "row":
            parsed.row = value
        elif key_l == "cites":
            parsed.cites = value
        elif key_l == "cited_by_local":
            parsed.cited_by_local = value
        elif key_l == "abstract":
            parsed.abstract = value
        elif key_l == "summary":
            parsed.summary = value
        elif key_l == "fulltext":
            parsed.fulltext = value
        elif key_l == "file":
            parsed.file_name = value
        elif key_l == "duplicate":
            parsed.duplicate = _as_bool(value)
        elif key_l == "version":
            parsed.version = _as_bool(value)
        elif key_l == "warning":
            parsed.warning = value
        elif key_l == "year":
            _apply_year(parsed, value)
        elif key_l == "has":
            _apply_has(parsed, value)
    parsed.text = " ".join(free).strip()
    return parsed
