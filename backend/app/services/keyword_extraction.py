"""Deterministic, dependency-free keyword extraction (SPEC §8.15.1).

A small RAKE-style extractor: split text into candidate phrases at stop words / punctuation, score
each content word by ``degree/frequency`` (co-occurrence degree over its frequency), sum word scores
per phrase, and return the top phrases. Fully local and deterministic (ties broken alphabetically),
so it needs no model download and is safe to run on every extraction. A YAKE/KeyBERT provider can
later replace ``extract_keywords`` behind the same interface.
"""

from __future__ import annotations

import re
from collections import defaultdict

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+\-]{1,}")
_SPLIT = re.compile(r"[^A-Za-z0-9+\-]+")

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
        "can",
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
        "our",
        "many",
        "new",
        "given",
    ]
)


def extract_keywords(text: str, *, top_k: int = 10, max_phrase_words: int = 3) -> list[str]:
    """Return up to ``top_k`` keyphrases for the text (most salient first)."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    # Candidate phrases: runs of content words bounded by stop words / punctuation.
    phrases: list[list[str]] = []
    current: list[str] = []
    for token in _SPLIT.split(cleaned.lower()):
        if not token or not _WORD.fullmatch(token) or token in _STOPWORDS or len(token) < 3:
            if current:
                phrases.append(current)
                current = []
            continue
        current.append(token)
    if current:
        phrases.append(current)

    freq: dict[str, int] = defaultdict(int)
    degree: dict[str, int] = defaultdict(int)
    for phrase in phrases:
        words = phrase[:max_phrase_words] if len(phrase) > max_phrase_words else phrase
        deg = len(words) - 1
        for w in words:
            freq[w] += 1
            degree[w] += deg + 1  # word's own occurrence contributes to its degree
    word_score = {w: degree[w] / freq[w] for w in freq}

    scored: dict[str, float] = {}
    for phrase in phrases:
        words = phrase[:max_phrase_words]
        if not words:
            continue
        key = " ".join(words)
        scored[key] = sum(word_score[w] for w in words)
    # Deterministic: by score desc, then phrase asc.
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [phrase for phrase, _ in ranked[:top_k]]
