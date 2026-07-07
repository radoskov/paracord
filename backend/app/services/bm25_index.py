"""Document-level BM25F+ lexical search engine (HYBRID-SEARCH-DESIGN §2, Arch A).

An **eager sparse** inverted index over terms (bag-of-words, not phrases) with true BM25F field
weighting and the BM25+ delta lower bound:

    weighted_tf(t,d) = Σ_field  w_field · tf(t,d,field) / (1 − b_field + b_field · len_field / avglen_field)
    score(q,d)       = Σ_{t∈q}  idf(t) · [ weighted_tf·(k1+1) / (k1 + weighted_tf) + δ ]

Because a term–document contribution depends only on document statistics, it is **precomputed once at
build time** into a scipy CSR matrix (rows = terms, cols = docs). A query is then a sparse
row-selection + column-sum — vectorized in C, sub-millisecond at library scale.

Fields come from the TEI section structure so title/abstract/methods/conclusion outweigh
introduction/related-work. On Postgres the matrix is persisted as mmap-friendly ``.npy`` arrays and
**memory-mapped read-only** by every worker process (one shared physical copy via the OS page cache);
the per-worker vocabulary dict is small. SQLite/test runs build in-memory and never touch disk. The
index is rebuilt when the corpus signature changes; a warm call (``POST /search/warm``) primes it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.work import Work

logger = logging.getLogger(__name__)

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
        default_factory=lambda: {"title": 3.0, "abstract": 2.0, "body_high": 1.5, "body_low": 0.5}
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


# --- index ------------------------------------------------------------------


@dataclass
class Bm25fIndex:
    """Eager BM25F+ index: a CSR ``(n_terms × n_docs)`` matrix of precomputed contributions."""

    work_ids: list[str]
    vocab: dict[str, int]  # term -> row index
    matrix: sparse.csr_matrix  # shape (n_terms, n_docs), float32
    signature: str | None = None
    key: str | None = None

    def search(
        self, query: str, *, limit: int = 10, visible_ids: set[uuid.UUID] | None = None
    ) -> list[tuple[str, float]]:
        """Return ``(work_id, score)`` for the top ``limit`` docs matching ``query``.

        Scoring covers every doc containing a query term, so a ``visible_ids`` filter yields an exact
        visible top-N (no post-filter under-fill)."""
        rows = [self.vocab[t] for t in set(tokenize(query)) if t in self.vocab]
        if not rows or not self.work_ids:
            return []
        scores = np.asarray(self.matrix[rows].sum(axis=0)).ravel()
        candidates = np.nonzero(scores > 0.0)[0]
        if visible_ids is not None:
            visible = {str(x) for x in visible_ids}
            candidates = [d for d in candidates if self.work_ids[d] in visible]
        else:
            candidates = list(candidates)
        candidates.sort(key=lambda d: scores[d], reverse=True)
        return [(self.work_ids[d], float(scores[d])) for d in candidates[:limit]]


def build_index(db: Session, *, config: Bm25fConfig | None = None) -> Bm25fIndex:
    """Build the eager BM25F+ CSR index over every work with indexable text. Query-independent; call
    off the read path (lazy warm / background rebuild).

    Body text is read from the already-materialized ``work_chunks`` rows rather than re-parsing each
    work's TEI XML (D13b) — the chunker persists section-labelled passages, so this is one bulk SELECT
    instead of a per-work XML parse. Title/abstract come from the ``works`` row directly (so a work
    that isn't chunked yet is still indexed by its title+abstract, and the title/abstract chunks are
    not double-counted against the overlap-carrying body chunks)."""
    from app.models.chunk import WorkChunk  # local import avoids an import cycle

    cfg = config or Bm25fConfig()
    doc_fields: list[dict[str, list[str]]] = []
    work_ids: list[str] = []
    field_length_sum: dict[str, int] = {f: 0 for f in FIELDS}

    # Body sections, grouped by work, from the materialized chunks (title/abstract taken from the
    # work row below, so those chunk rows are excluded here to avoid double counting).
    body_by_work: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    for work_id, section, chunk_text in db.execute(
        select(WorkChunk.work_id, WorkChunk.section, WorkChunk.text)
    ).all():
        if section in ("title", "abstract"):
            continue
        body_by_work[str(work_id)].append((section, chunk_text))

    for work_id, title, abstract in db.execute(
        select(Work.id, Work.canonical_title, Work.abstract).where(Work.merged_into_id.is_(None))
    ).all():
        fields: dict[str, list[str]] = {f: [] for f in FIELDS}
        fields["title"].extend(tokenize(title or ""))
        fields["abstract"].extend(tokenize(abstract or ""))
        for section, chunk_text in body_by_work.get(str(work_id), ()):
            fields[_label_to_field(section)].extend(tokenize(chunk_text or ""))
        if not any(fields.values()):
            continue
        work_ids.append(str(work_id))
        doc_fields.append(fields)
        for name in FIELDS:
            field_length_sum[name] += len(fields[name])

    n_docs = len(work_ids)
    if n_docs == 0:
        return Bm25fIndex(work_ids=[], vocab={}, matrix=sparse.csr_matrix((0, 0), dtype=np.float32))

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

    vocab: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
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
            row = vocab.setdefault(term, len(vocab))
            contribution = idf[term] * (wtf * (cfg.k1 + 1.0) / (cfg.k1 + wtf) + cfg.delta)
            rows.append(row)
            cols.append(doc_index)
            data.append(contribution)

    matrix = sparse.coo_matrix(
        (np.asarray(data, dtype=np.float32), (np.asarray(rows), np.asarray(cols))),
        shape=(len(vocab), n_docs),
    ).tocsr()
    return Bm25fIndex(work_ids=work_ids, vocab=vocab, matrix=matrix)


# --- mmap persistence (Postgres only; shared read-only across worker processes) ----------------


def _paths(directory: str, key: str) -> dict[str, str]:
    base = os.path.join(directory, f"bm25-{key}")
    return {
        "data": base + ".data.npy",
        "indices": base + ".indices.npy",
        "indptr": base + ".indptr.npy",
        "meta": base + ".meta.json",
    }


def save_index(index: Bm25fIndex, directory: str) -> None:
    """Persist the CSR arrays (mmap-friendly ``.npy``) + metadata. Signature-suffixed filenames and a
    meta-file-written-last commit avoid torn reads. Best-effort (raises only programmer errors)."""
    os.makedirs(directory, exist_ok=True)
    matrix = index.matrix.tocsr()
    paths = _paths(directory, index.key or "default")
    arrays = {
        "data": matrix.data.astype(np.float32, copy=False),
        "indices": matrix.indices.astype(np.int32, copy=False),
        "indptr": matrix.indptr.astype(np.int32, copy=False),
    }
    for name, array in arrays.items():
        tmp = f"{paths[name]}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "wb") as handle:
            np.save(handle, array)
        os.replace(tmp, paths[name])
    meta = {
        "work_ids": index.work_ids,
        "vocab": index.vocab,
        "shape": list(matrix.shape),
        "signature": index.signature,
    }
    tmp_meta = f"{paths['meta']}.{uuid.uuid4().hex}.tmp"
    with open(tmp_meta, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    os.replace(tmp_meta, paths["meta"])  # meta written last = commit marker
    # Superseded signatures are never read again — prune them so the dir doesn't grow unbounded.
    # Unlinking is safe even while other workers still mmap the old arrays (mmap survives unlink).
    current = f"bm25-{index.key or 'default'}."
    for name in os.listdir(directory):
        if name.startswith("bm25-") and not name.startswith(current):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(directory, name))


def load_index(directory: str, key: str) -> Bm25fIndex | None:
    """Load + mmap the persisted index (whatever signature is on disk); None if absent. Never raises.

    Returns the index tagged with the signature stored in its metadata so the caller can decide
    whether it is current or stale — serving a stale index while a background rebuild runs is how the
    read path avoids blocking on a rebuild (D13a)."""
    paths = _paths(directory, key)
    try:
        if not os.path.exists(paths["meta"]):
            return None
        with open(paths["meta"], encoding="utf-8") as handle:
            meta = json.load(handle)
        data = np.load(paths["data"], mmap_mode="r")
        indices = np.load(paths["indices"], mmap_mode="r")
        indptr = np.load(paths["indptr"], mmap_mode="r")
        matrix = sparse.csr_matrix((data, indices, indptr), shape=tuple(meta["shape"]))
        return Bm25fIndex(
            work_ids=meta["work_ids"],
            vocab={term: int(row) for term, row in meta["vocab"].items()},
            matrix=matrix,
            signature=meta.get("signature"),
            key=key,
        )
    except Exception as exc:  # noqa: BLE001 - a bad/partial cache must fall back to a rebuild
        logger.warning("Could not load persisted BM25F+ index (%s); will rebuild.", exc)
        return None


# --- signature-cached manager (warm-on-open; rebuilt when the corpus changes) ------------------

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "index": None}


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def corpus_signature(db: Session) -> str:
    """A cheap content fingerprint; when it changes the index is rebuilt. Content-only (no engine
    identity) so all worker processes on the same database agree and share the persisted file."""
    from app.models.chunk import WorkChunk

    n_works = int(db.scalar(select(func.count()).select_from(Work)) or 0)
    latest = db.scalar(select(func.max(Work.updated_at)))
    n_chunks = int(db.scalar(select(func.count()).select_from(WorkChunk)) or 0)
    return f"{n_works}:{latest.isoformat() if latest else '0'}:{n_chunks}"


def _disk_key(db: Session) -> str:
    """Filename key: stable across worker processes AND signatures on one DB, distinct per database.

    Signature-independent (unlike before) so the persisted file lives at a fixed path: a background
    rebuild overwrites it in place and the read path can load the last-known index even when its
    signature is stale (D13a)."""
    url = str(db.get_bind().url) if db.get_bind() is not None else ""
    return hashlib.sha1(url.encode()).hexdigest()[:16]  # noqa: S324


def _enqueue_rebuild() -> None:
    """Best-effort enqueue of a background BM25F+ rebuild (D13a). Never raises."""
    try:
        from app.workers.queue import enqueue_bm25_rebuild  # noqa: PLC0415 (keep Redis import lazy)

        enqueue_bm25_rebuild()
    except Exception as exc:  # noqa: BLE001 - a search must keep serving the stale index regardless
        logger.warning("Could not enqueue BM25F+ rebuild (%s); serving the stale index.", exc)


def rebuild_persisted_index(db: Session, *, config: Bm25fConfig | None = None) -> Bm25fIndex:
    """Build the BM25F+ index and (on Postgres) persist it. Entry point for the background job (D13).

    Runs entirely off the search read path: a worker rebuilds the sparse matrix and overwrites the
    on-disk copy, which the API processes then mmap-load on their next search."""
    signature = corpus_signature(db)
    index = build_index(db, config=config)
    index.signature = signature
    if _is_postgres(db):
        index.key = _disk_key(db)
        try:
            save_index(index, get_settings().search_index_dir)
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning("Could not persist BM25F+ index (%s).", exc)
    return index


def get_index(db: Session, *, config: Bm25fConfig | None = None) -> Bm25fIndex:
    """Return the lexical index, **never blocking a search on a rebuild** (D13a). Thread-safe.

    In-memory cache is keyed by ``(engine identity, signature)`` (so distinct test databases never
    collide). Behavior by dialect:

    * **SQLite / tests** (no Redis, no shared disk): build synchronously whenever the signature
      changed, so results are immediately consistent and deterministic.
    * **Postgres**: serve the persisted/last-known index and, when the corpus signature has moved,
      enqueue a background rebuild and serve the (slightly) stale index meanwhile — a search after an
      edit may transiently miss that edit until the worker refreshes the on-disk copy. Only a genuine
      cold start (nothing built yet) builds synchronously so the first search has something to serve.
    """
    signature = corpus_signature(db)
    bind = db.get_bind()
    mem_key = (id(bind), signature)
    with _LOCK:
        if _CACHE["key"] == mem_key and _CACHE["index"] is not None:
            return _CACHE["index"]
        # A previously-built index for THIS database (possibly a stale signature) we can serve now.
        stale = _CACHE["index"] if (_CACHE["key"] and _CACHE["key"][0] == id(bind)) else None

    if not _is_postgres(db):
        index = build_index(db, config=config)
        index.signature = signature
        with _LOCK:
            _CACHE["key"] = mem_key
            _CACHE["index"] = index
        return index

    directory = get_settings().search_index_dir
    disk_key = _disk_key(db)

    on_disk = load_index(directory, disk_key)
    if on_disk is not None and on_disk.signature == signature:
        with _LOCK:
            _CACHE["key"] = mem_key
            _CACHE["index"] = on_disk
        return on_disk

    serveable = stale or on_disk
    if serveable is not None:
        # Corpus changed but we have an index to serve → refresh in the background, serve stale now.
        _enqueue_rebuild()
        with _LOCK:
            # Cache under the served index's own (stale) signature so the next search re-checks the
            # disk and picks up the worker's fresh build as soon as it lands, instead of pretending
            # the stale index matches the current signature.
            _CACHE["key"] = (id(bind), serveable.signature)
            _CACHE["index"] = serveable
        return serveable

    # Cold start: nothing built yet → one-time synchronous build so the first search has a result.
    index = build_index(db, config=config)
    index.signature = signature
    index.key = disk_key
    try:
        save_index(index, directory)
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        logger.warning("Could not persist BM25F+ index (%s); using in-memory only.", exc)
    with _LOCK:
        _CACHE["key"] = mem_key
        _CACHE["index"] = index
    return index


def invalidate_cache() -> None:
    """Drop the in-memory index (e.g. after a manual rebuild)."""
    with _LOCK:
        _CACHE["key"] = None
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
    works = {
        w.id: w
        for w in db.scalars(
            select(Work).where(Work.id.in_(ids), Work.merged_into_id.is_(None))
        ).all()
    }
    hits = []
    for work_id, score in ranked:
        work = works.get(uuid.UUID(work_id))
        if work is not None:
            hits.append(PaperHit(work=work, score=score))
    return hits
