"""Keyword extraction (SPEC §8.15.1).

Two candidate scorers are fused so the result is neither "long generic noun phrases" (pure RAKE) nor
purely frequency-driven:

* **YAKE** — a statistical single-document keyphrase scorer (position, casing, dispersion,
  relatedness-to-context). Corpus-free, so it runs on one paper in isolation, and specifically
  designed to surface *distinctive* terms rather than merely frequent ones. Optional dependency: if
  ``yake`` is not importable the module degrades to the RAKE scorer alone, never hard-failing.
* **RAKE** — the original degree/frequency co-occurrence scorer, kept as a second opinion and the
  sole scorer when YAKE is unavailable.

The two rankings are combined by **Reciprocal Rank Fusion** (same idea as the hybrid search engine),
which sidesteps the incomparable score scales. After fusion the candidates are:

1. **filtered** — dropped if longer than ``max_phrase_words``, containing no content word, or mostly
   stop words after normalization;
2. **trimmed** — leading/trailing stop words stripped off the phrase boundary;
3. **boosted** — phrases that also appear in the title / abstract / section headings score higher
   (a strong distinctiveness signal);
4. optionally **reranked by corpus IDF** when a caller supplies one (downweights terms common across
   the whole library — the TF-IDF idea, corpus-aware); and
5. **de-duplicated** — near-identical phrasings (high token-set Jaccard overlap) collapse to the
   best-scoring representative.

Deterministic throughout (ties broken alphabetically), so re-running on the same text is stable.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+\-]{1,}")
_SPLIT = re.compile(r"[^A-Za-z0-9+\-]+")

# Reciprocal-rank-fusion constant (mirrors hybrid_search.RRF_K): larger => flatter contribution.
_RRF_K = 60
# A phrase found verbatim in the title/abstract/headings gets its fused score multiplied by this.
_BOOST_FACTOR = 1.5
# Two phrases whose token sets overlap by at least this Jaccard ratio are treated as duplicates.
_DEDUP_JACCARD = 0.6
# A phrase kept only if at most this fraction of its (pre-trim) tokens are stop words.
_MAX_STOPWORD_RATIO = 0.5

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "which",
        "with",
        "we",
        "our",
        "those",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "but",
        "not",
        "can",
        "will",
        "may",
        "also",
        "such",
        "using",
        "used",
        "use",
        "based",
        "both",
        "than",
        "been",
        "being",
        "more",
        "most",
        "could",
        "would",
        "should",
        "about",
        "between",
        "over",
        "under",
        "across",
        "after",
        "before",
        "via",
        "per",
        "each",
        "any",
        "all",
        "some",
        "other",
        "many",
        "new",
        "given",
        "however",
        "thus",
        "therefore",
        "while",
        "where",
        "when",
        "how",
        "what",
        "who",
        "whose",
        "whom",
        "if",
        "so",
        "because",
        "although",
        "though",
        "within",
        "without",
    ]
)


def _content_words(text: str) -> list[str]:
    """Lower-cased content tokens (drops stop words and 1-2 char tokens)."""
    return [
        w
        for w in _SPLIT.split((text or "").lower())
        if _WORD.fullmatch(w) and w not in _STOPWORDS and len(w) >= 3
    ]


def _candidate_phrases(text: str, max_phrase_words: int) -> list[list[str]]:
    """RAKE-style candidates: runs of content words bounded by stop words / punctuation."""
    phrases: list[list[str]] = []
    current: list[str] = []
    for token in _SPLIT.split((text or "").lower()):
        if not token or not _WORD.fullmatch(token) or token in _STOPWORDS or len(token) < 3:
            if current:
                phrases.append(current[:max_phrase_words])
                current = []
            continue
        current.append(token)
    if current:
        phrases.append(current[:max_phrase_words])
    return phrases


def _rake_ranking(text: str, max_phrase_words: int) -> list[str]:
    """RAKE degree/frequency scoring; returns phrases best-first."""
    phrases = _candidate_phrases(text, max_phrase_words)
    freq: dict[str, int] = defaultdict(int)
    degree: dict[str, int] = defaultdict(int)
    for phrase in phrases:
        deg = len(phrase) - 1
        for w in phrase:
            freq[w] += 1
            degree[w] += deg + 1
    word_score = {w: degree[w] / freq[w] for w in freq}
    scored: dict[str, float] = {}
    for phrase in phrases:
        if phrase:
            scored[" ".join(phrase)] = sum(word_score[w] for w in phrase)
    return [p for p, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]


def _yake_ranking(text: str, max_phrase_words: int, top_n: int) -> list[str]:
    """YAKE keyphrases best-first, or ``[]`` if the optional ``yake`` dependency is unavailable."""
    try:
        import yake
    except ImportError:
        return []
    try:
        extractor = yake.KeywordExtractor(n=max_phrase_words, top=top_n, dedupLim=0.9)
        # YAKE returns (keyphrase, score) with LOWER score = more relevant; it is already sorted.
        return [kw.lower() for kw, _score in extractor.extract_keywords(text)]
    except Exception:  # noqa: BLE001 - never let a scorer quirk break extraction
        logger.warning("YAKE extraction failed; falling back to RAKE only", exc_info=True)
        return []


def _rrf(rankings: list[list[str]]) -> dict[str, float]:
    """Reciprocal-rank-fuse several best-first phrase rankings into a combined score map."""
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, phrase in enumerate(ranking):
            fused[phrase] += 1.0 / (_RRF_K + rank)
    return fused


def _trim_stopwords(phrase: str) -> str:
    """Strip leading/trailing stop words (and sub-3-char tokens) from a phrase boundary."""
    words = phrase.split()
    while words and (words[0] in _STOPWORDS or len(words[0]) < 3):
        words.pop(0)
    while words and (words[-1] in _STOPWORDS or len(words[-1]) < 3):
        words.pop()
    return " ".join(words)


def _is_valid_phrase(phrase: str, max_phrase_words: int) -> bool:
    """Reject over-long, content-word-free, or mostly-stop-word phrases.

    "Content word" is approximated dependency-free as a non-stop-word token of length >= 3 (true
    noun detection would need a POS tagger / heavier model, deliberately avoided here).
    """
    words = phrase.split()
    if not words or len(words) > max_phrase_words:
        return False
    content = [w for w in words if w not in _STOPWORDS and len(w) >= 3]
    if not content:
        return False
    stop = sum(1 for w in words if w in _STOPWORDS)
    return stop / len(words) <= _MAX_STOPWORD_RATIO


def build_corpus_idf(texts: list[str]) -> dict[str, float]:
    """Inverse-document-frequency per content word over a corpus (for optional TF-IDF reranking).

    Mirrors ``topic_modeling._tfidf``'s smoothed IDF. Callers with a library-wide view (a batch
    keyword pass) can build this once and hand it to ``extract_keywords`` so corpus-common terms are
    downweighted; the per-paper extraction path omits it (no full-corpus scan on the hot path).
    """
    import math

    n_docs = len(texts) or 1
    doc_freq: dict[str, int] = defaultdict(int)
    for text in texts:
        for term in set(_content_words(text)):
            doc_freq[term] += 1
    return {term: math.log((1 + n_docs) / (1 + df)) + 1.0 for term, df in doc_freq.items()}


def _stem(token: str) -> str:
    """Crude, dependency-free singular/plural fold so 'network' and 'networks' match in dedup."""
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _dedupe(ordered: list[tuple[str, float]]) -> list[str]:
    """Collapse near-identical phrasings (high stem-normalized token-set Jaccard), best-scored first."""
    kept: list[str] = []
    kept_sets: list[set[str]] = []
    for phrase, _score in ordered:
        tokens = {_stem(w) for w in phrase.split()}
        if any(
            (len(tokens & seen) / len(tokens | seen)) >= _DEDUP_JACCARD
            for seen in kept_sets
            if tokens or seen
        ):
            continue
        kept.append(phrase)
        kept_sets.append(tokens)
    return kept


def extract_keywords(
    text: str,
    *,
    top_k: int = 10,
    max_phrase_words: int = 4,
    boost_text: str | None = None,
    corpus_idf: dict[str, float] | None = None,
) -> list[str]:
    """Return up to ``top_k`` keyphrases for ``text`` (most salient first).

    ``boost_text`` (title + abstract + section headings) lifts phrases that also appear there.
    ``corpus_idf`` (from :func:`build_corpus_idf`) optionally reranks by term rarity across a corpus.
    """

    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []

    # Fuse the two scorers' rankings (YAKE may be empty if the dependency is absent).
    yake_ranked = _yake_ranking(cleaned, max_phrase_words, max(top_k * 3, 30))
    rake_ranked = _rake_ranking(cleaned, max_phrase_words)
    fused = _rrf([r for r in (yake_ranked, rake_ranked) if r])
    if not fused:
        return []

    boost_content = set(_content_words(boost_text)) if boost_text else set()

    adjusted: dict[str, float] = {}
    for phrase, score in fused.items():
        trimmed = _trim_stopwords(phrase)
        if not _is_valid_phrase(trimmed, max_phrase_words):
            continue
        words = trimmed.split()
        value = score
        # Boost phrases whose content words all appear in the title/abstract/headings.
        if boost_content and all(w in boost_content for w in words if w not in _STOPWORDS):
            value *= _BOOST_FACTOR
        # Optional corpus-IDF rerank: scale by the mean rarity of the phrase's content words.
        if corpus_idf:
            idfs = [corpus_idf.get(w, 1.0) for w in words if w not in _STOPWORDS]
            if idfs:
                value *= sum(idfs) / len(idfs)
        # Keep the best score if trimming mapped two candidates onto the same phrase.
        adjusted[trimmed] = max(adjusted.get(trimmed, 0.0), value)

    ranked = sorted(adjusted.items(), key=lambda kv: (-kv[1], kv[0]))
    return _dedupe(ranked)[:top_k]
