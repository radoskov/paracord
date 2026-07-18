"""Ollama model discovery: a curated catalog + VRAM estimates, with best-effort live enrichment.

Ollama exposes **no** official search API and reports **no** VRAM requirement, so "help me find the
right model" is served from a hand-curated catalog of popular models (name, parameter count, default
quantization, download size, kind, popularity, blurb) plus a *computed* VRAM estimate. When the host
can reach ollama.com the catalog is enriched best-effort with live families/popularity; any scrape
failure silently falls back to the curated list (the hybrid the user picked, 2026-07-18).

The VRAM number is a deliberately conservative planning aid — quantized weights + a KV-cache/runtime
allowance — not a guarantee; real usage varies with context length, batch size and runtime.
"""

from __future__ import annotations

import contextlib
import re

import httpx2 as httpx

# Bytes-per-parameter for common Ollama quantizations (weights only). Default library tags are
# Q4_K_M unless noted; embedding models ship F16.
_BYTES_PER_PARAM = {
    "Q3_K_M": 0.43,
    "Q4_0": 0.53,
    "Q4_K_M": 0.56,
    "Q5_K_M": 0.68,
    "Q6_K": 0.82,
    "Q8_0": 1.06,
    "F16": 2.0,
    "F32": 4.0,
}


def estimate_vram_gb(params_b: float, quant: str = "Q4_K_M", *, context_tokens: int = 4096) -> float:
    """Rough VRAM (GB) to run a model: quantized weights + a KV-cache/overhead allowance.

    ``params_b`` is the parameter count in billions. Conservative on purpose — a sizing aid, not a
    promise. Returns a value rounded to 0.1 GB.
    """
    bpp = _BYTES_PER_PARAM.get(quant.upper(), _BYTES_PER_PARAM["Q4_K_M"])
    weights_gb = params_b * bpp  # params_b(×1e9) × bpp bytes == params_b × bpp GB
    kv_gb = params_b * 0.12 * (context_tokens / 4096)  # KV cache scales with size & context
    overhead_gb = 0.8  # runtime/graph overhead floor
    return round(weights_gb + kv_gb + overhead_gb, 1)


# Curated set of popular models. Sizes are approximate download sizes of the default (Q4_K_M / F16)
# tag; popularity is a relative 0-100 hand-ranking by pull count. Not exhaustive — the live scrape
# fills in families we don't list.
_CATALOG: list[dict] = [
    # --- general-purpose LLMs (summaries / recommend) --------------------------------------------
    {"name": "llama3.2:3b", "family": "llama3.2", "params_b": 3.0, "quant": "Q4_K_M", "size_bytes": 2_020_000_000, "kind": "llm", "popularity": 96, "blurb": "Llama 3.2 3B — fast, capable small general model."},  # noqa: E501
    {"name": "llama3.2:1b", "family": "llama3.2", "params_b": 1.0, "quant": "Q4_K_M", "size_bytes": 1_300_000_000, "kind": "llm", "popularity": 88, "blurb": "Llama 3.2 1B — tiny, runs on almost anything."},  # noqa: E501
    {"name": "llama3.1:8b", "family": "llama3.1", "params_b": 8.0, "quant": "Q4_K_M", "size_bytes": 4_900_000_000, "kind": "llm", "popularity": 97, "blurb": "Llama 3.1 8B — strong all-round mid-size model."},  # noqa: E501
    {"name": "qwen3:0.6b", "family": "qwen3", "params_b": 0.6, "quant": "Q4_K_M", "size_bytes": 500_000_000, "kind": "llm", "popularity": 80, "blurb": "Qwen3 0.6B — smallest Qwen3, ultra-light."},  # noqa: E501
    {"name": "qwen3:1.7b", "family": "qwen3", "params_b": 1.7, "quant": "Q4_K_M", "size_bytes": 1_400_000_000, "kind": "llm", "popularity": 86, "blurb": "Qwen3 1.7B — light general model."},  # noqa: E501
    {"name": "qwen3:4b", "family": "qwen3", "params_b": 4.0, "quant": "Q4_K_M", "size_bytes": 2_497_280_480, "kind": "llm", "popularity": 93, "blurb": "Qwen3 4B — strong small general model."},  # noqa: E501
    {"name": "qwen3:8b", "family": "qwen3", "params_b": 8.0, "quant": "Q4_K_M", "size_bytes": 5_200_000_000, "kind": "llm", "popularity": 90, "blurb": "Qwen3 8B — capable mid-size model."},  # noqa: E501
    {"name": "qwen3:14b", "family": "qwen3", "params_b": 14.0, "quant": "Q4_K_M", "size_bytes": 9_300_000_000, "kind": "llm", "popularity": 84, "blurb": "Qwen3 14B — larger, needs a real GPU."},  # noqa: E501
    {"name": "qwen2.5:3b", "family": "qwen2.5", "params_b": 3.0, "quant": "Q4_K_M", "size_bytes": 1_900_000_000, "kind": "llm", "popularity": 82, "blurb": "Qwen2.5 3B — solid small general model."},  # noqa: E501
    {"name": "qwen2.5:7b", "family": "qwen2.5", "params_b": 7.0, "quant": "Q4_K_M", "size_bytes": 4_700_000_000, "kind": "llm", "popularity": 88, "blurb": "Qwen2.5 7B — well-rounded mid-size model."},  # noqa: E501
    {"name": "gemma3:1b", "family": "gemma3", "params_b": 1.0, "quant": "Q4_K_M", "size_bytes": 815_000_000, "kind": "llm", "popularity": 84, "blurb": "Gemma 3 1B — tiny Google model."},  # noqa: E501
    {"name": "gemma3:4b", "family": "gemma3", "params_b": 4.0, "quant": "Q4_K_M", "size_bytes": 3_300_000_000, "kind": "llm", "popularity": 91, "blurb": "Gemma 3 4B — capable small model."},  # noqa: E501
    {"name": "gemma3:12b", "family": "gemma3", "params_b": 12.0, "quant": "Q4_K_M", "size_bytes": 8_100_000_000, "kind": "llm", "popularity": 82, "blurb": "Gemma 3 12B — larger, needs a GPU."},  # noqa: E501
    {"name": "phi4", "family": "phi4", "params_b": 14.0, "quant": "Q4_K_M", "size_bytes": 9_100_000_000, "kind": "llm", "popularity": 83, "blurb": "Phi-4 14B — strong reasoning for its size."},  # noqa: E501
    {"name": "phi3.5", "family": "phi3.5", "params_b": 3.8, "quant": "Q4_K_M", "size_bytes": 2_200_000_000, "kind": "llm", "popularity": 79, "blurb": "Phi-3.5 3.8B — compact, capable."},  # noqa: E501
    {"name": "mistral", "family": "mistral", "params_b": 7.0, "quant": "Q4_K_M", "size_bytes": 4_100_000_000, "kind": "llm", "popularity": 89, "blurb": "Mistral 7B — classic fast mid-size model."},  # noqa: E501
    {"name": "deepseek-r1:7b", "family": "deepseek-r1", "params_b": 7.0, "quant": "Q4_K_M", "size_bytes": 4_700_000_000, "kind": "llm", "popularity": 85, "blurb": "DeepSeek-R1 7B — reasoning-focused model."},  # noqa: E501
    {"name": "deepseek-r1:8b", "family": "deepseek-r1", "params_b": 8.0, "quant": "Q4_K_M", "size_bytes": 5_200_000_000, "kind": "llm", "popularity": 83, "blurb": "DeepSeek-R1 8B — reasoning-focused model."},  # noqa: E501
    # --- embedding models (semantic search / related papers) -------------------------------------
    {"name": "nomic-embed-text", "family": "nomic-embed-text", "params_b": 0.137, "quant": "F16", "size_bytes": 274_000_000, "kind": "embedding", "popularity": 98, "blurb": "Nomic embeddings (768-dim) — the recommended default for semantic search."},  # noqa: E501
    {"name": "mxbai-embed-large", "family": "mxbai-embed-large", "params_b": 0.335, "quant": "F16", "size_bytes": 670_000_000, "kind": "embedding", "popularity": 90, "blurb": "mxbai large embeddings (1024-dim) — higher quality, larger."},  # noqa: E501
    {"name": "bge-m3", "family": "bge-m3", "params_b": 0.567, "quant": "F16", "size_bytes": 1_200_000_000, "kind": "embedding", "popularity": 85, "blurb": "BGE-M3 embeddings (1024-dim) — multilingual."},  # noqa: E501
    {"name": "all-minilm", "family": "all-minilm", "params_b": 0.023, "quant": "F16", "size_bytes": 46_000_000, "kind": "embedding", "popularity": 78, "blurb": "all-MiniLM (384-dim) — tiny, fast embeddings."},  # noqa: E501
    {"name": "snowflake-arctic-embed", "family": "snowflake-arctic-embed", "params_b": 0.335, "quant": "F16", "size_bytes": 670_000_000, "kind": "embedding", "popularity": 76, "blurb": "Snowflake Arctic embeddings — strong retrieval quality."},  # noqa: E501
]

_PARAM_TAG = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def params_from_name(name: str) -> float | None:
    """Best-effort parameter count (billions) parsed from a model name/tag, e.g. ``qwen3:4b`` → 4.0.
    Returns None when the name carries no ``<n>b`` size hint."""
    m = _PARAM_TAG.search(name)
    return float(m.group(1)) if m else None


def _entry_view(entry: dict, *, local: set[str], source: str) -> dict:
    """Shape a catalog/scrape entry for the API, adding the VRAM estimate + a `pulled` flag."""
    params_b = entry.get("params_b")
    quant = entry.get("quant") or "Q4_K_M"
    name = entry["name"]
    return {
        "name": name,
        "family": entry.get("family") or name,
        "params_b": params_b,
        "quant": quant,
        "size_bytes": entry.get("size_bytes"),
        "kind": entry.get("kind") or "llm",
        "popularity": entry.get("popularity", 0),
        "vram_gb": estimate_vram_gb(params_b, quant) if params_b else None,
        "blurb": entry.get("blurb") or "",
        "source": source,
        "pulled": _is_pulled(name, local),
    }


def _is_pulled(name: str, local: set[str]) -> bool:
    """Whether `name` matches a locally-pulled tag. Ollama's /api/tags names carry an explicit
    ``:latest`` (and untagged catalog names imply ``:latest``), so compare both forms."""
    candidates = {name, name if ":" in name else f"{name}:latest"}
    return bool(candidates & local)


_LIBRARY_HREF = re.compile(r'href="/library/([a-z0-9._-]+)"', re.IGNORECASE)


def _scrape_ollama_library(query: str, *, timeout: float = 6.0) -> list[str]:
    """Best-effort: return model family slugs from ollama.com search for `query`. Empty on any
    failure (no egress, timeout, markup change) — the caller falls back to the curated catalog."""
    slugs: list[str] = []
    with contextlib.suppress(Exception):
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get("https://ollama.com/search", params={"q": query})
            resp.raise_for_status()
            seen: set[str] = set()
            for slug in _LIBRARY_HREF.findall(resp.text):
                s = slug.lower()
                if s not in seen:
                    seen.add(s)
                    slugs.append(s)
    return slugs[:20]


def search_models(
    query: str | None,
    *,
    local_names: list[str] | None = None,
    allow_scrape: bool = True,
) -> list[dict]:
    """Return catalog matches for `query`, popularity-sorted, each with a VRAM estimate.

    An empty/blank query returns the whole catalog. When `allow_scrape` and the host can reach
    ollama.com, families not already in the catalog are appended (params guessed from the name,
    VRAM estimated when guessable). `local_names` (Ollama /api/tags names) marks pulled entries.
    """
    local = {n.lower() for n in (local_names or [])}
    q = (query or "").strip().lower()

    def matches(entry: dict) -> bool:
        if not q:
            return True
        hay = f"{entry['name']} {entry.get('family', '')} {entry.get('blurb', '')}".lower()
        return q in hay

    results = [_entry_view(e, local=local, source="catalog") for e in _CATALOG if matches(e)]
    known = {r["name"].split(":")[0] for r in results} | {r["family"] for r in results}

    scraped: list[str] = []
    if q and allow_scrape:
        # Defensive: the scrape suppresses its own errors, but never let an unexpected failure here
        # break search — the curated catalog is always a valid answer.
        with contextlib.suppress(Exception):
            scraped = _scrape_ollama_library(q)
    if scraped:
        for slug in scraped:
            if slug in known:
                continue
            known.add(slug)
            params_b = params_from_name(slug)
            results.append(
                _entry_view(
                    {
                        "name": slug,
                        "family": slug,
                        "params_b": params_b,
                        "quant": "Q4_K_M",
                        "size_bytes": None,
                        "kind": "llm",
                        "popularity": 50,  # unranked live hit — below curated, above nothing
                        "blurb": "From ollama.com — pull a specific tag, e.g. "
                        f"{slug}:latest (size/VRAM unknown until pulled).",
                    },
                    local=local,
                    source="ollama.com",
                )
            )

    results.sort(key=lambda r: (-r["popularity"], r["name"]))
    return results
