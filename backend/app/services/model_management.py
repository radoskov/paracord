"""AI provider capability detection + model download/management (WORKPLAN_NEXT Stage 8C/8E).

Local-LLM / embedding *weights* are downloadable at runtime; the heavier Python packages are not
pip-installed at runtime (immutable images) — instead we **detect** whether a provider can run and
let the GUI guide enabling it. Ollama needs no Python dependency, only a reachable daemon, so it is
fully drivable from the UI: detect, list, pull, delete.
"""

from __future__ import annotations

import importlib.util
import shutil

import httpx2 as httpx


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _ollama_tags(ollama_url: str) -> list[dict] | None:
    """Return the Ollama daemon's local models, or None if it is unreachable."""
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(f"{ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            return response.json().get("models", [])
    except Exception:  # noqa: BLE001 - unreachable daemon is a normal "not available" state
        return None


def detect_providers(*, ollama_url: str) -> dict:
    """Report which providers can run here and how to enable the ones that can't."""
    ollama_models = _ollama_tags(ollama_url)
    st_available = _module_available("sentence_transformers")
    bertopic_available = _module_available("bertopic")
    ocrmypdf_available = shutil.which("ocrmypdf") is not None
    # PyMuPDF OCR shells out to tesseract, so both must be present for the pymupdf backend to run.
    pymupdf_available = _module_available("fitz") and shutil.which("tesseract") is not None
    return {
        "embedding": {
            "hash_bow": {"available": True, "note": "Default, dependency-free."},
            "sentence_transformers": {
                "available": st_available,
                "note": None
                if st_available
                else "Rebuild with the AI image extra (pip: sentence-transformers).",
            },
            "ollama": {
                "available": ollama_models is not None,
                "note": None
                if ollama_models is not None
                else "Start the Ollama profile (make up-ai) and set its URL.",
            },
        },
        "summary": {
            "extractive": {"available": True, "note": "Default, dependency-free."},
            "local_llm": {
                "available": ollama_models is not None,
                "note": None
                if ollama_models is not None
                else "Start the Ollama profile (make up-ai) and pull a model.",
            },
        },
        "topic": {
            "tfidf": {"available": True, "note": "Default, dependency-free."},
            "embedding": {
                "available": True,
                "note": "Clusters on dense embedding vectors when a real embedding model is active; "
                "falls back to TF-IDF for the hash-BOW baseline.",
            },
            "bertopic": {
                "available": True,
                "note": "BERTopic is not installed — using the embedding backend (dense clustering "
                "when a real model is active); real BERTopic is deferred.",
            },
        },
        # Extraction / OCR backends. Keyed by the ``ocr_backend`` enum (none|ocrmypdf|pymupdf) so
        # the active-capability status can look up the selected value directly; the grobid entry is
        # also reported for detection visibility (it is always the structured TEI extractor).
        "extraction": {
            "none": {
                "available": True,
                "note": "OCR pre-step disabled — GROBID runs on the PDF as-is.",
            },
            "ocrmypdf": {
                "available": ocrmypdf_available,
                "note": None
                if ocrmypdf_available
                else "ocrmypdf/tesseract not found in this image — rebuild the base image "
                "(bundles tesseract-ocr + ghostscript + ocrmypdf).",
            },
            "pymupdf": {
                "available": pymupdf_available,
                "note": None
                if pymupdf_available
                else "PyMuPDF (fitz) + tesseract not found in this image — rebuild the base image "
                "(bundles PyMuPDF + tesseract-ocr).",
            },
            "grobid": {"available": True, "note": "Default TEI extractor (GROBID service)."},
        },
        "ollama_reachable": ollama_models is not None,
        "bertopic_installed": bertopic_available,
        "sentence_transformers_installed": st_available,
        "ocrmypdf_installed": ocrmypdf_available,
        "pymupdf_installed": pymupdf_available,
    }


def probe_embedding_model(model: str, *, ollama_url: str) -> dict:
    """Check whether an Ollama model is pulled *and* embedding-capable.

    Answers the "which models are for embedding?" problem (#2): a generation-only model like
    ``qwen`` is present but 500s on ``/api/embeddings``. Returns a dict the UI can act on:
    ``{present, embeddings, canonical, error}`` where ``present``/``embeddings`` are None when the
    daemon is unreachable (can't verify) so the caller can distinguish "bad model" from "can't
    check right now".
    """
    from app.services.embeddings import normalize_ollama_model  # noqa: PLC0415 (avoid import cycle)

    canonical = normalize_ollama_model(model)
    tags = _ollama_tags(ollama_url)
    if tags is None:
        return {
            "present": None,
            "embeddings": None,
            "canonical": canonical,
            "error": "Ollama daemon unreachable — cannot verify the model.",
        }
    names = {t.get("name") for t in tags}
    if canonical not in names and model not in names:
        return {
            "present": False,
            "embeddings": None,
            "canonical": canonical,
            "error": f"Model '{canonical}' is not pulled. Pull it first.",
        }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{ollama_url.rstrip('/')}/api/embeddings",
                json={"model": canonical, "prompt": "probe"},
            )
            resp.raise_for_status()
            ok = bool(resp.json().get("embedding"))
        return {
            "present": True,
            "embeddings": ok,
            "canonical": canonical,
            "error": None if ok else "Model returned no embedding vector.",
        }
    except Exception as exc:  # noqa: BLE001 - generation-only models 500 here; that's the signal
        return {
            "present": True,
            "embeddings": False,
            "canonical": canonical,
            "error": f"'{canonical}' is not an embedding model ({exc}).",
        }


def list_models(*, ollama_url: str) -> list[dict]:
    """List locally-available downloadable models (currently Ollama; ST weights live in HF cache)."""
    models: list[dict] = []
    for entry in _ollama_tags(ollama_url) or []:
        models.append(
            {
                "provider": "ollama",
                "name": entry.get("name"),
                "size_bytes": entry.get("size"),
            }
        )
    return models


def pull_model(provider: str, model: str, *, ollama_url: str) -> dict:
    """Download/pull a model. Blocks until done (run inside an RQ job). Raises on failure."""
    if provider == "ollama":
        # A finite (generous) timeout so a hung daemon can't tie up the worker forever (audit).
        with httpx.Client(timeout=3600) as client:
            # stream=false → the daemon completes the pull before responding.
            response = client.post(
                f"{ollama_url.rstrip('/')}/api/pull", json={"name": model, "stream": False}
            )
            response.raise_for_status()
        return {"provider": "ollama", "model": model, "status": "pulled"}
    if provider == "sentence_transformers":
        if not _module_available("sentence_transformers"):
            raise RuntimeError(
                "sentence-transformers is not installed in this image — rebuild with the AI extra."
            )
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        SentenceTransformer(model)  # constructing it downloads the weights into the HF cache
        return {"provider": "sentence_transformers", "model": model, "status": "downloaded"}
    raise ValueError(f"Cannot pull models for provider {provider!r}")


def delete_model(provider: str, model: str, *, ollama_url: str) -> dict:
    """Remove a locally-pulled model (Ollama only)."""
    if provider == "ollama":
        with httpx.Client(timeout=30) as client:
            response = client.request(
                "DELETE", f"{ollama_url.rstrip('/')}/api/delete", json={"name": model}
            )
            response.raise_for_status()
        return {"provider": "ollama", "model": model, "status": "deleted"}
    raise ValueError(f"Cannot delete models for provider {provider!r}")
