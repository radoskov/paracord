"""Structured search-query parsing (SPEC §8.7 / §14).

Turns a free-text query that may contain field operators — ``author:`` ``year:>=2020`` ``venue:``
``tag:`` ``type:`` ``has:pdf`` ``has:references`` ``title:`` — into a structured filter plus the
leftover free text. Quoted phrases (``author:"jane doe"``) are supported. Unknown ``key:value``
tokens fall back to free text, so a stray colon never breaks a search.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

_YEAR_RE = re.compile(r"^(?P<op>>=|<=|=)?(?P<a>\d{4})(?:-(?P<b>\d{4}))?$")
_KNOWN = {"author", "year", "venue", "tag", "type", "has", "title"}


@dataclass
class ParsedQuery:
    text: str = ""  # free-text remainder (matched against title/abstract/venue)
    author: str | None = None
    venue: str | None = None
    title: str | None = None
    work_type: str | None = None
    tag: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    has_pdf: bool | None = None
    has_references: bool | None = None
    flags: list[str] = field(default_factory=list)  # unrecognized has:* values, for transparency


def _apply_year(parsed: ParsedQuery, value: str) -> None:
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
        elif key_l == "year":
            _apply_year(parsed, value)
        elif key_l == "has":
            v = value.lower()
            if v in ("pdf", "file"):
                parsed.has_pdf = True
            elif v in ("references", "refs"):
                parsed.has_references = True
            elif v in ("no-pdf", "nofile"):
                parsed.has_pdf = False
            else:
                parsed.flags.append(value)
    parsed.text = " ".join(free).strip()
    return parsed
