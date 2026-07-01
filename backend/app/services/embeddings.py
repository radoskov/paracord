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

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

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


# --- Embedding provider interface (SPEC §8.15, Stage 6) ---------------------
#
# The default provider is the dependency-free hash-BOW embedder above. Heavier local providers
# (sentence-transformers, Ollama) are opt-in via settings and **degrade to hash-BOW** if their
# library/service is unavailable, so a normal install never gains a hard dependency. Stored
# embeddings carry their ``model_name`` so vectors from different providers are never compared.


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, text: str) -> list[float]: ...


class HashBowProvider:
    """Deterministic feature-hashing bag-of-words — the default + test provider."""

    model_name = DEFAULT_EMBEDDING_MODEL

    def embed(self, text: str) -> list[float]:
        return embed_text(text)


class SentenceTransformerProvider:
    """Opt-in local sentence-transformers embedder (no network egress after model download)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415 (optional dep)

        self.model_name = f"st:{model_name}"
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._model.encode(text or "", normalize_embeddings=True)]


def normalize_ollama_model(model_name: str) -> str:
    """Canonicalize an Ollama model name to the tagged form the HTTP API expects.

    Ollama's ``/api/embeddings`` matches the daemon registry by exact name; a bare
    ``nomic-embed-text`` misses the entry the daemon stores as ``nomic-embed-text:latest`` and
    fails, whereas the tagged form works. A bare name and its ``:latest`` tag are the same model,
    so we canonicalize to the tagged form everywhere (wire call *and* the stored ``model_name``
    key) to avoid the silent-degrade-to-hash-BOW bug and to keep one vector namespace per model.
    """
    name = (model_name or "").strip()
    if name and ":" not in name:
        return f"{name}:latest"
    return name


class OllamaProvider:
    """Opt-in Ollama embeddings endpoint (local daemon)."""

    def __init__(self, model_name: str, base_url: str) -> None:
        canonical = normalize_ollama_model(model_name)
        self.model_name = f"ollama:{canonical}"
        self._model = canonical
        self._base_url = base_url.rstrip("/")

    def embed(self, text: str) -> list[float]:
        import httpx2 as httpx  # noqa: PLC0415

        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text or ""},
            )
            response.raise_for_status()
            return [float(x) for x in response.json()["embedding"]]


@dataclass
class ResolvedEmbeddingProvider:
    """The active embedding provider plus what was *requested*, so callers can surface a
    silent degradation ("requested X, using Y") at the point of use (Phase B2).

    ``requested`` is the configured provider key (e.g. ``sentence_transformers``); ``provider`` is
    the object actually in use (hash-BOW when the requested one was unavailable). ``degraded`` is
    True when a non-default provider was requested but the fallback kicked in, with a short
    ``reason``.
    """

    provider: EmbeddingProvider
    requested: str
    degraded: bool
    reason: str | None = None


def resolve_embedding_provider(
    settings: Settings | None = None, *, db=None
) -> ResolvedEmbeddingProvider:
    """Resolve the active embedding provider *with provenance* (requested vs used).

    When ``db`` is given, the owner-managed runtime config (``ai_config``) decides the provider;
    otherwise the static ``Settings`` defaults are used (back-compat / no-DB call sites).
    """
    if db is not None:
        from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

        cfg = get_ai_config(db, settings=settings)
        provider, model, ollama_url = cfg.embedding_provider, cfg.embedding_model, cfg.ollama_url
    else:
        if settings is None:
            from app.core.config import get_settings  # noqa: PLC0415

            settings = get_settings()
        provider = getattr(settings, "embedding_provider", "hash_bow")
        model = getattr(settings, "embedding_model", None)
        ollama_url = settings.ollama_url
    try:
        if provider == "sentence_transformers":
            active = SentenceTransformerProvider(model or "sentence-transformers/all-MiniLM-L6-v2")
            return ResolvedEmbeddingProvider(active, requested=provider, degraded=False)
        if provider == "ollama":
            active = OllamaProvider(model or "nomic-embed-text", ollama_url)
            return ResolvedEmbeddingProvider(active, requested=provider, degraded=False)
    except Exception as exc:  # noqa: BLE001 - optional providers degrade, never break a request
        logger.warning("Embedding provider %r unavailable (%s); using hash-BOW.", provider, exc)
        return ResolvedEmbeddingProvider(
            HashBowProvider(), requested=provider, degraded=True, reason=str(exc) or None
        )
    # Requested provider is the built-in baseline (hash_bow / unknown) — not a degradation.
    return ResolvedEmbeddingProvider(HashBowProvider(), requested=provider, degraded=False)


def get_embedding_provider(settings: Settings | None = None, *, db=None) -> EmbeddingProvider:
    """Return the active embedding provider, falling back to hash-BOW on any failure.

    When ``db`` is given, the owner-managed runtime config (``ai_config``) decides the provider;
    otherwise the static ``Settings`` defaults are used (back-compat / no-DB call sites).
    """
    return resolve_embedding_provider(settings, db=db).provider


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
