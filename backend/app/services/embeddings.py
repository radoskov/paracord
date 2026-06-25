"""Text embedding for semantic search (SPEC §8.15).

The default embedder is a deterministic, dependency-free **feature-hashing bag-of-words** model:
tokens are hashed into a fixed-dimension vector (with a sign bit to reduce collision bias) and
the result is L2-normalized. It needs no model download and never leaves the machine, so it is
safe by default and trivially testable. A heavier local model (sentence-transformers / Ollama)
can be plugged in later behind the same ``embed_text`` interface; embeddings are stored with
their ``model_name`` so vectors from different models are never compared.

Vectors are stored as JSON and ranked with the cosine similarity here (no pgvector dependency),
which is adequate for a single-user library; a pgvector index is a future scaling step.
"""

import hashlib
import math
import re

EMBEDDING_DIM = 256
DEFAULT_EMBEDDING_MODEL = "hash-bow-v1"

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """Return an L2-normalized feature-hashed bag-of-words vector for the text."""
    vector = [0.0] * dim
    for token in _WORD.findall((text or "").lower()):
        # hashlib (not the salted built-in hash) keeps embeddings stable across processes.
        digest = hashlib.md5(token.encode("utf-8")).digest()  # noqa: S324  (non-crypto use)
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0.0 if either is degenerate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
