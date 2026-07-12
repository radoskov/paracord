"""Author-name matching for reference→library matching (batch 12, owner items #5/#6).

Two author names match on **(surname, first-initial)**, diacritic-folded, handling both
"Last, First" and "First Last" orderings:

* "London, Jack" ≈ "J. London" ≈ "London, J." — same surname, and the first initials agree (J is
  Jack's initial), or one side has no initial.
* "R. London" ✗ "Jack London" — same surname but the initials disagree (R ≠ J), so they do **not**
  match. This correctly *lowers* the overlap ratio rather than silently accepting a wrong author.

**"et al" (#6):** a reference author list containing "et al" is validated against **one** author —
the best surname match among the candidate's authors — so a truncated "Smith et al." still matches a
paper actually authored by Smith. A citation that truncates authors *without* "et al" is simply a
worse citation: it lowers the overlap ratio and we accept that (no special-casing).
"""

from __future__ import annotations

import re
import unicodedata

_ET_AL_RE = re.compile(r"\bet\.?\s*al\.?", re.IGNORECASE)
_INITIAL_RE = re.compile(r"[A-Za-z]")


def _fold(text: str) -> str:
    """Lowercase + strip diacritics (``Å`` → ``a``) so accented names compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def parse_author_name(name: str) -> tuple[str, str | None] | None:
    """Normalize one display name to ``(surname, first_initial|None)``, or ``None`` if unusable.

    Accepts "Last, First", "First Last", and single-token names. The surname is folded; the initial
    is the first letter of the given name (``None`` when absent, e.g. a lone surname).
    """
    folded = _fold(name)
    if not folded:
        return None
    if "," in folded:
        surname_part, _, given_part = folded.partition(",")
        surname = surname_part.strip()
        given = given_part.strip()
    else:
        tokens = folded.split()
        if len(tokens) == 1:
            return (tokens[0], None)
        surname = tokens[-1]
        given = " ".join(tokens[:-1])
    surname = surname.strip()
    if not surname:
        return None
    initial_match = _INITIAL_RE.search(given)
    return (surname, initial_match.group(0) if initial_match else None)


def names_match(a: tuple[str, str | None], b: tuple[str, str | None]) -> bool:
    """Two parsed names match when surnames are equal AND (either initial absent OR they agree)."""
    if a[0] != b[0]:
        return False
    if a[1] is None or b[1] is None:
        return True
    return a[1] == b[1]


def _has_et_al(names: list[str]) -> bool:
    return any(_ET_AL_RE.search(n) for n in names)


def _parse_all(names: list[str]) -> list[tuple[str, str | None]]:
    parsed = []
    for name in names:
        if _ET_AL_RE.search(name):
            continue  # "et al" is not an author name
        p = parse_author_name(name)
        if p is not None:
            parsed.append(p)
    return parsed


def author_overlap_ratio(reference_authors: list[str], work_authors: list[str]) -> float:
    """Overlap of a reference's authors against a candidate work's authors, in ``[0, 1]``.

    With **"et al"** (#6): validate against the *single* best-matching work author — 1.0 if any work
    author matches a listed reference author, else 0.0. Without "et al": the fraction of the
    reference's listed authors that match some work author (ref-side denominator; the recommended
    default in the workplan). Returns 0.0 when either side has no parseable authors — callers treat
    an uncomputable gate as "skip", not "fail".
    """
    ref_parsed = _parse_all(reference_authors)
    work_parsed = _parse_all(work_authors)
    if not ref_parsed or not work_parsed:
        return 0.0
    if _has_et_al(reference_authors):
        # "et al": one confirmed shared author is enough.
        return 1.0 if any(names_match(r, w) for r in ref_parsed for w in work_parsed) else 0.0
    matched = sum(1 for r in ref_parsed if any(names_match(r, w) for w in work_parsed))
    return matched / len(ref_parsed)
