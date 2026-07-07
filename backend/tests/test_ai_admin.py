"""Owner-managed AI config + model management API (WORKPLAN_NEXT Stage 8)."""


def test_ai_config_defaults_then_update(client, auth_headers, db):
    owner = auth_headers("owner")

    # Defaults reflect the lexical baselines until an owner changes them.
    got = client.get("/api/v1/admin/ai-config", headers=owner)
    assert got.status_code == 200
    body = got.json()
    assert body["config"]["embedding_provider"] == "hash_bow"
    assert "sentence_transformers" in body["allowed"]["embedding_provider"]

    updated = client.put(
        "/api/v1/admin/ai-config",
        headers=owner,
        json={"embedding_provider": "ollama", "embedding_model": "nomic-embed-text"},
    )
    assert updated.status_code == 200
    assert updated.json()["config"]["embedding_provider"] == "ollama"

    # Persisted: a fresh read sees the new value.
    again = client.get("/api/v1/admin/ai-config", headers=owner).json()
    assert again["config"]["embedding_model"] == "nomic-embed-text"


def test_ai_config_rejects_unknown_provider(client, auth_headers):
    r = client.put(
        "/api/v1/admin/ai-config",
        headers=auth_headers("owner"),
        json={"embedding_provider": "totally-made-up"},
    )
    assert r.status_code == 400


def test_ai_config_is_owner_only(client, auth_headers):
    assert client.get("/api/v1/admin/ai-config", headers=auth_headers("editor")).status_code == 403
    assert client.get("/api/v1/admin/ai-config", headers=auth_headers("reader")).status_code == 403


def test_providers_detection_and_reindex_status(client, auth_headers, monkeypatch):
    from app.services import model_management

    # Force Ollama "unreachable" so the assertion holds regardless of a daemon on the host.
    monkeypatch.setattr(model_management, "_ollama_tags", lambda ollama_url: None)
    owner = auth_headers("owner")
    providers = client.get("/api/v1/admin/ai/providers", headers=owner)
    assert providers.status_code == 200
    body = providers.json()
    assert body["embedding"]["hash_bow"]["available"] is True
    # Ollama forced unreachable → reported unavailable with a how-to-enable note.
    assert body["embedding"]["ollama"]["available"] is False
    assert body["embedding"]["ollama"]["note"]

    # The topic backends are honest: BERTopic is deferred/not installed, and the embedding backend
    # clusters on dense vectors when a real model is active (else TF-IDF fallback).
    assert body["bertopic_installed"] is False
    bertopic_note = body["topic"]["bertopic"]["note"]
    assert "not installed" in bertopic_note
    assert "deferred" in bertopic_note.lower()
    assert "embedding vectors" in body["topic"]["embedding"]["note"]

    status = client.get("/api/v1/admin/ai/reindex/status", headers=owner)
    assert status.status_code == 200
    assert status.json()["model_name"] == "hash-bow-v1"
    assert "indexed" in status.json()


def test_ai_status_endpoint(client, auth_headers, monkeypatch):
    from app.services import model_management

    # Deterministic: force Ollama unreachable regardless of a daemon on the host.
    monkeypatch.setattr(model_management, "_ollama_tags", lambda ollama_url: None)
    # Owner + admin get the folded status; editor/reader are refused.
    for role in ("owner", "admin"):
        r = client.get("/api/v1/admin/ai/status", headers=auth_headers(role))
        assert r.status_code == 200, role
        body = r.json()
        assert body["config"]["embedding_provider"] == "hash_bow"
        assert body["providers"]["embedding"]["hash_bow"]["available"] is True
        assert body["reindex"]["model_name"] == "hash-bow-v1"
        # Honest capability flags: BERTopic isn't installed and its note says so.
        assert body["bertopic_installed"] is False
        assert body["ollama_reachable"] is False
        assert "not installed" in body["providers"]["topic"]["bertopic"]["note"]
        # The active selection per capability is surfaced (hash_bow / extractive / tfidf here).
        assert body["active"]["embedding"]["selected"] == "hash_bow"
        assert body["active"]["embedding"]["available"] is True
        assert body["active"]["topic"]["selected"] == "tfidf"
        # Hybrid-search status (HS6): chunk-level ANN coverage (inactive under hash-BOW/SQLite)
        # and lexical-index warmth are surfaced.
        assert body["chunk_embeddings"]["column"] is None
        assert body["chunk_embeddings"]["model_name"] == "hash-bow-v1"
        assert "loaded" in body["lexical_index"]

    for role in ("editor", "reader"):
        assert (
            client.get("/api/v1/admin/ai/status", headers=auth_headers(role)).status_code == 403
        ), role


def test_models_list_when_ollama_down(client, auth_headers, monkeypatch):
    from app.services import model_management

    # Force Ollama unreachable → an empty model list, not an error.
    monkeypatch.setattr(model_management, "_ollama_tags", lambda ollama_url: None)
    r = client.get("/api/v1/admin/ai/models", headers=auth_headers("owner"))
    assert r.status_code == 200
    assert r.json() == {"models": []}


def test_models_list_when_ollama_up(client, auth_headers, monkeypatch):
    from app.services import model_management

    # Reachable daemon with two local models → surfaced as provider/name/size rows.
    monkeypatch.setattr(
        model_management,
        "_ollama_tags",
        lambda ollama_url: [
            {"name": "nomic-embed-text:latest", "size": 274301056},
            {"name": "qwen3:latest", "size": 5200000000},
        ],
    )
    r = client.get("/api/v1/admin/ai/models", headers=auth_headers("owner"))
    assert r.status_code == 200
    models = r.json()["models"]
    assert {m["name"] for m in models} == {"nomic-embed-text:latest", "qwen3:latest"}
    assert all(m["provider"] == "ollama" for m in models)


# --- Phase B5: OCR / advanced-extraction backend ---


def test_detect_providers_reports_extraction_ocr_availability(monkeypatch):
    """detect_providers keys the OCR enum + reports ocrmypdf via shutil.which and ML via find_spec."""
    from app.services import model_management

    monkeypatch.setattr(model_management.shutil, "which", lambda name: None)
    monkeypatch.setattr(model_management, "_module_available", lambda name: False)
    providers = model_management.detect_providers(ollama_url="http://localhost:11434")

    extraction = providers["extraction"]
    assert extraction["none"]["available"] is True
    assert extraction["ocrmypdf"]["available"] is False
    assert "rebuild the base image" in extraction["ocrmypdf"]["note"]
    assert extraction["pymupdf"]["available"] is False
    assert extraction["grobid"]["available"] is True
    assert "full_ml" not in extraction  # the removed ML-extraction backend (D35)
    assert "nougat" not in extraction
    assert "marker" not in extraction
    assert providers["ocrmypdf_installed"] is False


def test_detect_providers_ocrmypdf_present(monkeypatch):
    from app.services import model_management

    monkeypatch.setattr(
        model_management.shutil,
        "which",
        lambda name: "/usr/bin/ocrmypdf" if name == "ocrmypdf" else None,
    )
    monkeypatch.setattr(model_management, "_module_available", lambda name: False)
    providers = model_management.detect_providers(ollama_url="http://localhost:11434")
    assert providers["extraction"]["ocrmypdf"]["available"] is True
    assert providers["extraction"]["ocrmypdf"]["note"] is None
    assert providers["ocrmypdf_installed"] is True


def test_ai_config_includes_ocr_backend_in_allowed_and_active(client, auth_headers):
    owner = auth_headers("owner")

    cfg = client.get("/api/v1/admin/ai-config", headers=owner).json()
    assert cfg["config"]["ocr_backend"] == "ocrmypdf"  # Settings default
    assert set(cfg["allowed"]["ocr_backend"]) == {"none", "ocrmypdf", "pymupdf"}

    status = client.get("/api/v1/admin/ai/status", headers=owner).json()
    assert "extraction" in status["active"]
    assert status["active"]["extraction"]["selected"] == "ocrmypdf"
    assert "ocr_backend" in status["allowed"]


def test_ai_config_persists_ocr_backend(client, auth_headers):
    owner = auth_headers("owner")
    r = client.put("/api/v1/admin/ai-config", headers=owner, json={"ocr_backend": "none"})
    assert r.status_code == 200
    assert r.json()["config"]["ocr_backend"] == "none"
    again = client.get("/api/v1/admin/ai-config", headers=owner).json()
    assert again["config"]["ocr_backend"] == "none"


def test_ai_config_rejects_unknown_ocr_backend(client, auth_headers):
    r = client.put(
        "/api/v1/admin/ai-config",
        headers=auth_headers("owner"),
        json={"ocr_backend": "made-up"},
    )
    assert r.status_code == 400


def test_ocr_backends_no_longer_include_full_ml():
    """D35: the ML-extraction seam is gone; ocrmypdf + pymupdf are the only OCR backends."""
    from app.services.ai_config import OCR_BACKENDS

    assert "full_ml" not in OCR_BACKENDS
    assert OCR_BACKENDS == ("none", "ocrmypdf", "pymupdf")


def test_legacy_full_ml_ocr_backend_degrades_to_default(db):
    """A row storing the removed ``full_ml`` value degrades to the Settings default on read (D35)."""
    from app.models.ai import AI_CONFIG_SINGLETON_ID, AIConfig
    from app.services.ai_config import get_ai_config

    db.add(AIConfig(id=AI_CONFIG_SINGLETON_ID, ocr_backend="full_ml"))
    db.flush()
    cfg = get_ai_config(db)
    assert cfg.ocr_backend == "ocrmypdf"  # the Settings.ocr_backend default, not the stale value


# --- #2: embedding-model capability validation ---


def test_validate_model_reports_unreachable(client, auth_headers, monkeypatch):
    from app.services import model_management

    monkeypatch.setattr(model_management, "_ollama_tags", lambda ollama_url: None)
    r = client.post(
        "/api/v1/admin/ai/models/validate",
        headers=auth_headers("owner"),
        json={"provider": "ollama", "model": "nomic-embed-text"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is None
    assert body["canonical"] == "nomic-embed-text:latest"
    assert "unreachable" in body["error"].lower()


def test_validate_model_reports_not_pulled(client, auth_headers, monkeypatch):
    from app.services import model_management

    monkeypatch.setattr(
        model_management, "_ollama_tags", lambda ollama_url: [{"name": "qwen3:latest"}]
    )
    r = client.post(
        "/api/v1/admin/ai/models/validate",
        headers=auth_headers("owner"),
        json={"provider": "ollama", "model": "nomic-embed-text"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is False
    assert "not pulled" in body["error"].lower()


def test_validate_non_ollama_provider_is_ok(client, auth_headers):
    r = client.post(
        "/api/v1/admin/ai/models/validate",
        headers=auth_headers("owner"),
        json={"provider": "sentence_transformers", "model": "all-MiniLM-L6-v2"},
    )
    assert r.status_code == 200
    assert r.json()["embeddings"] is True


# --- D6: ollama_url SSRF guard ---


def test_ai_config_accepts_loopback_and_service_ollama_url(client, auth_headers):
    owner = auth_headers("owner")
    for url in (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://ollama:11434",  # docker-service name (single label)
        "http://[::1]:11434",
    ):
        r = client.put("/api/v1/admin/ai-config", headers=owner, json={"ollama_url": url})
        assert r.status_code == 200, url
        assert r.json()["config"]["ollama_url"] == url


def test_ai_config_rejects_external_ollama_url_without_optin(client, auth_headers):
    owner = auth_headers("owner")
    for url in ("http://evil.example.com:11434", "http://169.254.169.254", "ftp://ollama"):
        r = client.put("/api/v1/admin/ai-config", headers=owner, json={"ollama_url": url})
        assert r.status_code == 400, url


def test_ollama_url_external_allowed_with_optin():
    from app.core.config import Settings
    from app.services.ai_config import _validate_ollama_url

    permissive = Settings(allow_external_ollama=True)
    # No raise: an FQDN is permitted once the opt-in is set.
    _validate_ollama_url("http://ollama.lan.example.org:11434", settings=permissive)
    # Scheme is still enforced even with the opt-in.
    import pytest

    with pytest.raises(ValueError):
        _validate_ollama_url("ftp://ollama.example.org", settings=permissive)


def test_ollama_host_classification():
    from app.services.ai_config import _ollama_host_is_local

    assert _ollama_host_is_local("localhost")
    assert _ollama_host_is_local("127.0.0.1")
    assert _ollama_host_is_local("::1")
    assert _ollama_host_is_local("ollama")  # docker-service name
    assert not _ollama_host_is_local("ollama.example.org")
    assert not _ollama_host_is_local("169.254.169.254")
    assert not _ollama_host_is_local("192.168.1.5")


def test_lexical_rebuild_endpoint_owner_only(client, auth_headers):
    """B5: the manual lexical-index rebuild is admin-only and (no worker queue in tests) rebuilds
    synchronously, returning 202."""
    assert (
        client.post("/api/v1/admin/ai/lexical-rebuild", headers=auth_headers("editor")).status_code
        == 403
    )
    r = client.post("/api/v1/admin/ai/lexical-rebuild", headers=auth_headers("owner"))
    assert r.status_code == 202
    assert r.json()["status"] in ("rebuilt", "queued")
