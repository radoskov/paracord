"""Shared cosine-similarity primitives (Insights audit C4, 2026-07-14).

Topic modeling, the topic graph, the visualization axes/heatmap and the embeddings service each
carried their own cosine implementation over their preferred vector representation. The maths is
identical; only the carrier differs. These are the canonical implementations:

* :func:`dense_cosine` — two equal-length dense vectors (embedding lists);
* :func:`sparse_cosine` — two sparse mappings (term- or index-keyed TF-IDF/hash-BOW vectors);
* :func:`cosine_matrix` — row-wise pairwise similarity of a dense numpy matrix.

All guard degenerate inputs (empty/zero vectors, length mismatch) by returning 0.0 rows/values
rather than raising.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence

import numpy as np


def dense_cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length vectors (0.0 if either is degenerate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def sparse_cosine[K: Hashable](a: Mapping[K, float], b: Mapping[K, float]) -> float:
    """Cosine similarity of two sparse vectors keyed by term/index (0.0 if either is empty)."""
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(value * larger.get(key, 0.0) for key, value in smaller.items())
    if dot == 0.0:
        return 0.0
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    return dot / (norm_a * norm_b)


def cosine_matrix(matrix: np.ndarray) -> np.ndarray:
    """Row-wise cosine-similarity matrix (symmetric, 1.0 diagonal for non-zero rows).

    Zero rows stay zero (their norm is treated as 1 to avoid division by zero), matching the
    scalar helpers' degenerate-input guard. Values are clipped to [-1, 1] against float drift.
    """
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    normed = matrix / safe
    sim = normed @ normed.T
    return np.clip(sim, -1.0, 1.0)
