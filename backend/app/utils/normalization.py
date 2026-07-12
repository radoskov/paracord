"""Normalization helpers for metadata matching."""

import re


def normalize_title(title: str) -> str:
    """Normalize a title for duplicate/version matching.

    Punctuation is stripped **before** whitespace is collapsed so that a spaced separator such as an
    en dash ("KnowRob – A …") does not leave a double space once the dash is removed — otherwise it
    would fail to compare equal to the colon form ("KnowRob: A …"). Doing it in the other order was a
    latent bug that weakened both duplicate detection and reference matching for dash-punctuated
    titles.
    """
    cleaned = title.lower().strip()
    cleaned = re.sub(r"[^a-z0-9 ]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# DOI URL/scheme prefixes to strip, longest-host first so ``dx.doi.org/`` wins over ``doi.org/``.
_DOI_SCHEME_PREFIXES = ("https://", "http://")
_DOI_HOST_PREFIXES = ("dx.doi.org/", "doi.org/", "doi:")


def normalize_doi(doi: str) -> str:
    """Normalize a DOI string to its bare ``10.x/…`` form.

    Tolerates the common decorations seen in extracted references and metadata: ``http``/``https``
    schemes, the ``doi.org`` and ``dx.doi.org`` resolver hosts, and a ``doi:`` prefix.
    """
    cleaned = doi.strip().lower()
    for prefix in _DOI_SCHEME_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    for prefix in _DOI_HOST_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    return cleaned


# The ONE canonical arXiv-id parser (S3). Previously three divergent copies lived in
# duplicate_detection / identifiers / metadata_enrichment, each tolerating a different subset of
# the real-world decorations — so the same stored id could normalize three ways depending on which
# feature touched it. Those modules now delegate here.
_ARXIV_VERSION_RE = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v(?P<version>\d+))?$",
    re.IGNORECASE,
)


def split_arxiv_id(arxiv_id: str | None) -> dict[str, str | None]:
    """Return arXiv base ID and optional version suffix.

    Tolerates the decorations seen in extracted references and metadata: ``http``/``https``
    schemes, the ``arxiv.org/abs/`` and ``arxiv.org/pdf/`` paths, an ``arXiv:`` prefix (any case),
    and a trailing ``.pdf``. The emitted base is always lowercase.
    """
    if not arxiv_id:
        return {"base": None, "version": None}
    cleaned = arxiv_id.strip().lower()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)
    for prefix in ("arxiv.org/abs/", "arxiv.org/pdf/", "arxiv:"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = cleaned.removesuffix(".pdf")
    match = _ARXIV_VERSION_RE.match(cleaned)
    if not match:
        return {"base": cleaned or None, "version": None}
    version = match.group("version")
    return {
        "base": match.group("base").lower(),
        "version": f"v{version}" if version else None,
    }


def arxiv_base_id(arxiv_id: str | None) -> str | None:
    """The version-less, lowercase arXiv base id (``1706.03762`` from ``arXiv:1706.03762v1``)."""
    return split_arxiv_id(arxiv_id)["base"]


# DataCite registers arXiv e-prints as ``10.48550/arXiv.<id>`` DOIs; Crossref/GROBID consolidation
# increasingly emits that form where older data carries a bare arXiv id. Both spell the same paper,
# so identifier matching must bridge them.
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(?P<base>.+?)(?:v\d+)?$")


def arxiv_base_from_doi(doi: str) -> str | None:
    """The version-less arXiv base id encoded in an arXiv DOI, or ``None`` for ordinary DOIs.

    ``10.48550/arXiv.2101.00001v2`` → ``2101.00001``. Accepts the same decorations
    :func:`normalize_doi` tolerates.
    """
    match = _ARXIV_DOI_RE.match(normalize_doi(doi))
    return match.group("base") if match else None


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


# A strict token subset scores ~100 under token_set_ratio, which is right for a reference that
# truncates a work's subtitle but dangerously permissive for short generic titles ("deep learning"
# would score 100 against every "deep learning for …" work). Containment therefore only counts
# when the shorter title has at least this many tokens.
_MIN_CONTAINMENT_TOKENS = 5


def title_similarity_pct(a: str, b: str) -> float:
    """0-100 similarity between two *titles*, tuned for reference↔work matching.

    Unlike :func:`similarity_pct` (generic field comparison), both sides are first run through
    :func:`normalize_title`, so punctuation variants (dash vs colon, quotes, accents already folded
    upstream) compare as identical. Combines a plain ratio with a word-order-insensitive
    token_sort ratio, plus a length-guarded token_set (containment) ratio so a reference that drops
    a work's subtitle still scores ~100 without letting short generic titles match every superset.
    """
    norm_a = normalize_title(a)
    norm_b = normalize_title(b)
    if not norm_a and not norm_b:
        return 100.0
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 100.0
    try:
        from rapidfuzz.fuzz import ratio, token_set_ratio, token_sort_ratio  # noqa: PLC0415

        score = max(ratio(norm_a, norm_b), token_sort_ratio(norm_a, norm_b))
        if min(len(norm_a.split()), len(norm_b.split())) >= _MIN_CONTAINMENT_TOKENS:
            score = max(score, token_set_ratio(norm_a, norm_b))
        return round(score, 1)
    except ImportError:  # pragma: no cover - rapidfuzz is a declared dependency
        from difflib import SequenceMatcher  # noqa: PLC0415

        return round(SequenceMatcher(None, norm_a, norm_b).ratio() * 100.0, 1)


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
