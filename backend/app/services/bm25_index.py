"""Document-level BM25F+ lexical search engine (HYBRID-SEARCH-DESIGN §2, Arch A).

An eager inverted index over **terms** (bag-of-words, not phrases) with true BM25F field weighting
and the BM25+ delta lower bound:

    weighted_tf(t,d) = Σ_field  w_field · tf(t,d,field) / (1 − b_field + b_field · len_field / avglen_field)
    score(q,d)       = Σ_{t∈q}  idf(t) · [ weighted_tf·(k1+1) / (k1 + weighted_tf) + δ ]

Fields come from the TEI section structure so that title/abstract/methods/conclusion outweigh
introduction/related-work (which is where low-value "utterances" live). Because a term–document
contribution depends only on document statistics, it is **precomputed once at index build** and a
query is just a sum over its terms' postings — sub-millisecond to low-ms at library scale.

Implementation note: this is deliberately pure-Python (no numpy/scipy/bm25s) to honor the project's
minimal-dependency policy — at a personal-library scale (1000s of papers) a pure-Python inverted
index is already milliseconds per query. A numpy/scipy eager-sparse matrix with mmap sharing across
workers (per the original design) remains a future optimization if the corpus grows large; today the
index is held per worker in memory, rebuilt on demand when the corpus signature changes.
"""

from __future__ import annotations

import math
import re
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.work import Work

# --- configuration ----------------------------------------------------------

FIELDS = ("title", "abstract", "body_high", "body_low")

# Section labels routed to the low-value body field (down-weighted). Everything else in the body
# (methods/results/conclusion/discussion/...) is high-value.
_LOW_SECTION = re.compile(r"introduc|related|background|prior work|motivation|acknowledg", re.I)

_WORD = re.compile(r"[a-z][a-z0-9'-]{1,}")
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "you",
        "your",
        "they",
        "their",
        "he",
        "she",
        "his",
        "her",
        "i",
        "not",
        "no",
        "can",
        "will",
        "would",
        "should",
        "could",
        "may",
        "might",
        "must",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "over",
        "under",
        "more",
        "most",
        "such",
        "some",
        "only",
        "other",
        "into",
        "than",
        "also",
        "using",
        "used",
        "use",
        "based",
        "both",
        "which",
        "who",
        "whom",
        "what",
        "when",
        "where",
        "why",
        "how",
    ]
)


@dataclass
class Bm25fConfig:
    k1: float = 1.5
    delta: float = 1.0  # BM25+ lower bound
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "title": 3.0,
            "abstract": 2.0,
            "body_high": 1.5,
            "body_low": 0.5,
        }
    )
    b: dict[str, float] = field(
        default_factory=lambda: {
            "title": 0.75,
            "abstract": 0.75,
            "body_high": 0.75,
            "body_low": 0.75,
        }
    )


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOPWORDS]


def _label_to_field(label: str | None) -> str:
    if label == "title":
        return "title"
    if label == "abstract":
        return "abstract"
    if label and _LOW_SECTION.search(label):
        return "body_low"
    return "body_high"


def _work_field_tokens(db: Session, work: Work) -> dict[str, list[str]]:
    """Bucket a work's section texts into the BM25F fields, tokenized."""
    from app.services.chunking import iter_work_sections  # local import avoids an import cycle

    buckets: dict[str, list[str]] = {f: [] for f in FIELDS}
    for label, text in iter_work_sections(db, work):
        buckets[_label_to_field(label)].extend(tokenize(text))
    return buckets


# --- index ------------------------------------------------------------------


@dataclass
class Bm25fIndex:
    """An immutable BM25F+ inverted index: term -> [(doc_index, precomputed_contribution)]."""

    work_ids: list[str]
    postings: dict[str, list[tuple[int, float]]]

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        visible_ids: set[uuid.UUID] | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``(work_id, score)`` for the top ``limit`` docs matching ``query``.

        ``visible_ids`` (when not None) restricts to visible works. Filtering happens after scoring
        — but scoring already covers *every* doc that contains a query term, so the visible top-N is
        exact (no post-filter under-fill)."""
        terms = set(tokenize(query))
        if not terms or not self.work_ids:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in terms:
            for doc_index, contribution in self.postings.get(term, ()):
                scores[doc_index] += contribution
        visible = {str(x) for x in visible_ids} if visible_ids is not None else None
        results: list[tuple[str, float]] = []
        for doc_index, score in scores.items():
            work_id = self.work_ids[doc_index]
            if visible is None or work_id in visible:
                results.append((work_id, score))
        results.sort(key=lambda item: item[1], reverse=True)
        return results[:limit]


def build_index(db: Session, *, config: Bm25fConfig | None = None) -> Bm25fIndex:
    """Build a BM25F+ index over every work with indexable text. Query-independent; call off the
    read path (rebuild job / lazy warm)."""
    cfg = config or Bm25fConfig()
    doc_fields: list[dict[str, list[str]]] = []
    work_ids: list[str] = []
    field_length_sum: dict[str, int] = {f: 0 for f in FIELDS}

    for work in db.scalars(select(Work)):
        fields = _work_field_tokens(db, work)
        if not any(fields.values()):
            continue
        work_ids.append(str(work.id))
        doc_fields.append(fields)
        for name in FIELDS:
            field_length_sum[name] += len(fields[name])

    n_docs = len(work_ids)
    if n_docs == 0:
        return Bm25fIndex(work_ids=[], postings={})

    avg_length = {f: (field_length_sum[f] / n_docs) or 1.0 for f in FIELDS}

    document_frequency: Counter[str] = Counter()
    for fields in doc_fields:
        terms_in_doc: set[str] = set()
        for name in FIELDS:
            terms_in_doc.update(fields[name])
        document_frequency.update(terms_in_doc)

    idf = {
        term: math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        for term, df in document_frequency.items()
    }

    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for doc_index, fields in enumerate(doc_fields):
        weighted_tf: dict[str, float] = defaultdict(float)
        for name in FIELDS:
            tokens = fields[name]
            if not tokens:
                continue
            length_norm = 1.0 - cfg.b[name] + cfg.b[name] * (len(tokens) / avg_length[name])
            weight = cfg.weights[name]
            for term, count in Counter(tokens).items():
                weighted_tf[term] += weight * count / length_norm
        for term, wtf in weighted_tf.items():
            contribution = idf[term] * (wtf * (cfg.k1 + 1.0) / (cfg.k1 + wtf) + cfg.delta)
            postings[term].append((doc_index, contribution))

    return Bm25fIndex(work_ids=work_ids, postings=dict(postings))


# --- signature-cached manager (warm-on-open; rebuilt when the corpus changes) ------------------

_LOCK = threading.Lock()
_CACHE: dict = {"signature": None, "index": None}


def corpus_signature(db: Session) -> str:
    """A cheap fingerprint of the corpus; when it changes the cached index is rebuilt.

    Combines the work count + latest work update + chunk count (chunks change when TEI/body text is
    (re)extracted, which may not bump Work.updated_at). The engine identity is included so distinct
    databases (e.g. per-test engines) never share a cached index."""
    from app.models.chunk import WorkChunk

    n_works = int(db.scalar(select(func.count()).select_from(Work)) or 0)
    latest = db.scalar(select(func.max(Work.updated_at)))
    n_chunks = int(db.scalar(select(func.count()).select_from(WorkChunk)) or 0)
    return f"{id(db.get_bind())}:{n_works}:{latest.isoformat() if latest else '0'}:{n_chunks}"


def get_index(db: Session, *, config: Bm25fConfig | None = None) -> Bm25fIndex:
    """Return the cached index, rebuilding it if the corpus signature changed. Thread-safe."""
    signature = corpus_signature(db)
    with _LOCK:
        if _CACHE["signature"] == signature and _CACHE["index"] is not None:
            return _CACHE["index"]
    index = build_index(db, config=config)  # build outside the lock
    with _LOCK:
        _CACHE["signature"] = signature
        _CACHE["index"] = index
    return index


def invalidate_cache() -> None:
    """Drop the in-memory index (e.g. after a manual rebuild)."""
    with _LOCK:
        _CACHE["signature"] = None
        _CACHE["index"] = None


def cache_info() -> dict:
    """Report whether the lexical index is warm in this process and its size (no build triggered)."""
    with _LOCK:
        index = _CACHE["index"]
        return {
            "loaded": index is not None,
            "docs": len(index.work_ids) if index is not None else None,
        }


def lexical_search_papers(
    db: Session,
    query: str,
    *,
    visible_ids: set[uuid.UUID] | None,
    limit: int = 10,
    config: Bm25fConfig | None = None,
):
    """BM25F+ paper ranking for ``query``, filtered to visible works. Returns ``PaperHit``s.

    Uses the signature-cached index (built lazily / warmed on library open). Read-only."""
    from app.services.chunk_search import PaperHit  # local import avoids an import cycle

    index = get_index(db, config=config)
    ranked = index.search(query, limit=limit, visible_ids=visible_ids)
    if not ranked:
        return []
    ids = [uuid.UUID(work_id) for work_id, _ in ranked]
    works = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(ids))).all()}
    hits = []
    for work_id, score in ranked:
        work = works.get(uuid.UUID(work_id))
        if work is not None:
            hits.append(PaperHit(work=work, score=score))
    return hits
