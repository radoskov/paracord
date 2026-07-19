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


def test_models_search_ranks_and_flags_pulled(client, auth_headers, monkeypatch):
    from app.services import model_catalog, model_management

    # No network in tests: kill the live scrape; report one already-pulled model via the daemon.
    monkeypatch.setattr(model_catalog, "_scrape_ollama_library", lambda q, *, timeout=6.0: [])
    monkeypatch.setattr(
        model_management,
        "_ollama_tags",
        lambda ollama_url: [{"name": "nomic-embed-text:latest", "size": 274301056}],
    )
    r = client.get("/api/v1/admin/ai/models/search?q=embed", headers=auth_headers("owner"))
    assert r.status_code == 200
    models = r.json()["models"]
    # Matches on name/family/blurb (so bge-m3 matches "embed" via its blurb), all catalog-sourced.
    assert models and all(m["source"] == "catalog" for m in models)
    assert "nomic-embed-text" in {m["name"] for m in models}
    # Popularity-sorted + estimated VRAM present; the pulled model is flagged.
    assert [m["popularity"] for m in models] == sorted(
        (m["popularity"] for m in models), reverse=True
    )
    by_name = {m["name"]: m for m in models}
    assert by_name["nomic-embed-text"]["pulled"] is True
    assert by_name["nomic-embed-text"]["vram_gb"] is not None


def test_models_search_is_owner_only(client, auth_headers):
    assert (
        client.get(
            "/api/v1/admin/ai/models/search?q=qwen", headers=auth_headers("editor")
        ).status_code
        == 403
    )


# --- Model mount/unmount (VRAM control) ---


def test_vram_budget_persists_and_rejects_negative(client, auth_headers, db):
    owner = auth_headers("owner")
    r = client.put("/api/v1/admin/ai-config", headers=owner, json={"vram_budget_gb": 8})
    assert r.status_code == 200
    assert (
        client.get("/api/v1/admin/ai-config", headers=owner).json()["config"]["vram_budget_gb"] == 8
    )
    assert (
        client.put(
            "/api/v1/admin/ai-config", headers=owner, json={"vram_budget_gb": -1}
        ).status_code
        == 400
    )


def test_query_cache_and_auto_unmount_persist_including_falsy(client, auth_headers, db):
    """Falsy-but-valid settings (auto_unmount off, cache size 0) must persist, not be dropped as
    'unset' by the generic overlay/persist loops."""
    owner = auth_headers("owner")
    r = client.put(
        "/api/v1/admin/ai-config",
        headers=owner,
        json={"query_cache_size": 512, "auto_unmount": False, "auto_unmount_minutes": 15},
    )
    assert r.status_code == 200
    cfg = client.get("/api/v1/admin/ai-config", headers=owner).json()["config"]
    assert cfg["query_cache_size"] == 512
    assert cfg["auto_unmount"] is False
    assert cfg["auto_unmount_minutes"] == 15
    # A cache size of 0 (disable) is a real value and must survive the round-trip, not reset to default.
    assert (
        client.put(
            "/api/v1/admin/ai-config", headers=owner, json={"query_cache_size": 0}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/admin/ai-config", headers=owner).json()["config"]["query_cache_size"]
        == 0
    )
    # Invalid values are rejected.
    assert (
        client.put(
            "/api/v1/admin/ai-config", headers=owner, json={"query_cache_size": -5}
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/v1/admin/ai-config", headers=owner, json={"auto_unmount_minutes": 0}
        ).status_code
        == 400
    )


def test_summary_llm_timeout_and_reasoning_persist(client, auth_headers, db):
    owner = auth_headers("owner")
    r = client.put(
        "/api/v1/admin/ai-config",
        headers=owner,
        json={"summary_llm_timeout": 600, "summary_reasoning": True},
    )
    assert r.status_code == 200
    cfg = client.get("/api/v1/admin/ai-config", headers=owner).json()["config"]
    assert cfg["summary_llm_timeout"] == 600
    assert cfg["summary_reasoning"] is True
    # Reasoning back off (a falsy value) must persist, not silently revert to the default.
    client.put("/api/v1/admin/ai-config", headers=owner, json={"summary_reasoning": False})
    assert (
        client.get("/api/v1/admin/ai-config", headers=owner).json()["config"]["summary_reasoning"]
        is False
    )
    # A non-positive timeout is rejected.
    assert (
        client.put(
            "/api/v1/admin/ai-config", headers=owner, json={"summary_llm_timeout": 0}
        ).status_code
        == 400
    )


# --- mount/unmount ENDPOINTS: enqueue a background job (the load runs in the worker) ---


def test_mount_endpoint_enqueues_and_validates(client, auth_headers, monkeypatch):
    import app.api.v1.endpoints.ai_admin as m

    seen: list = []
    monkeypatch.setattr(
        m,
        "enqueue_model_mount",
        lambda model, kind, compute, actor, num_ctx=None: (
            seen.append((model, kind, compute, num_ctx)) or "mount-job"
        ),
    )
    owner = auth_headers("owner")
    r = client.post(
        "/api/v1/admin/ai/models/mount",
        headers=owner,
        json={
            "provider": "ollama",
            "model": "qwen3:4b",
            "kind": "summary",
            "compute": "gpu",
            "num_ctx": 8192,
        },
    )
    assert r.status_code == 202 and r.json()["job_id"] == "mount-job"
    assert seen == [("qwen3:4b", "summary", "gpu", 8192)]
    # Validation: bad kind, bad compute, non-ollama provider → 400.
    for bad in (
        {"provider": "ollama", "model": "x", "kind": "bogus"},
        {"provider": "ollama", "model": "x", "kind": "summary", "compute": "quantum"},
        {"provider": "sentence_transformers", "model": "x", "kind": "summary"},
    ):
        assert (
            client.post("/api/v1/admin/ai/models/mount", headers=owner, json=bad).status_code == 400
        )


def test_unmount_endpoint_enqueues(client, auth_headers, monkeypatch):
    import app.api.v1.endpoints.ai_admin as m

    monkeypatch.setattr(m, "enqueue_model_unmount", lambda model, kind, actor: "unmount-job")
    owner = auth_headers("owner")
    r = client.post(
        "/api/v1/admin/ai/models/unmount",
        headers=owner,
        json={"provider": "ollama", "model": "qwen3:4b", "kind": "summary"},
    )
    assert r.status_code == 202 and r.json()["job_id"] == "unmount-job"


def test_loaded_endpoint_owner_only(client, auth_headers, monkeypatch):
    import app.api.v1.endpoints.ai_admin as m

    monkeypatch.setattr(m, "list_loaded", lambda *, ollama_url: [])
    assert client.get("/api/v1/admin/ai/loaded", headers=auth_headers("editor")).status_code == 403
    r = client.get("/api/v1/admin/ai/loaded", headers=auth_headers("owner"))
    assert r.status_code == 200
    assert "loaded" in r.json() and "vram_budget_gb" in r.json()


# --- mount/unmount JOBS: the worker loads/unloads, then flips the active config ---


def _use_test_db(monkeypatch, db):
    """Point the jobs' own ``SessionLocal`` at the test session so their config writes are visible
    (the ``db`` fixture is a fresh per-test DB, so committing/reusing it is safe)."""
    import contextlib

    @contextlib.contextmanager
    def _sl():
        yield db  # reuse the test session; the fixture owns closing it

    monkeypatch.setattr("app.db.session.SessionLocal", _sl)


def _owner_row(db):
    from app.core.security import Role
    from app.models.user import User

    return db.query(User).filter(User.role == Role.OWNER).first()


def test_mount_job_selects_and_frees_previous(client, auth_headers, db, monkeypatch):
    from app.services.ai_config import get_ai_config
    from app.workers import jobs

    _use_test_db(monkeypatch, db)
    calls: list = []
    # The job imports mount_model/unmount_model from model_management at call time, so patch there.
    monkeypatch.setattr(
        "app.services.model_management.mount_model",
        lambda model, **k: calls.append(("mount", model)) or {},
    )
    monkeypatch.setattr(
        "app.services.model_management.unmount_model",
        lambda model, **k: calls.append(("unmount", model)) or {},
    )
    auth_headers("owner")  # ensure an owner row exists
    owner = _owner_row(db)

    jobs.mount_model_job("qwen3:4b", "summary", "auto", str(owner.id))
    assert get_ai_config(db).summary_model == "qwen3:4b"
    assert ("mount", "qwen3:4b") in calls

    jobs.mount_model_job("llama3.2:3b", "summary", "auto", str(owner.id))
    assert get_ai_config(db).summary_model == "llama3.2:3b"
    assert ("unmount", "qwen3:4b") in calls  # one-per-kind freed the previous


def test_unmount_job_reverts_active_to_baseline(client, auth_headers, db, monkeypatch):
    from app.services.ai_config import get_ai_config
    from app.workers import jobs

    _use_test_db(monkeypatch, db)
    monkeypatch.setattr("app.services.model_management.mount_model", lambda model, **k: {})
    monkeypatch.setattr("app.services.model_management.unmount_model", lambda model, **k: {})
    auth_headers("owner")
    owner = _owner_row(db)

    jobs.mount_model_job("qwen3:4b", "summary", "auto", str(owner.id))
    assert get_ai_config(db).summary_provider == "local_llm"
    jobs.unmount_model_job("qwen3:4b", "summary", str(owner.id))
    assert get_ai_config(db).summary_provider == "extractive"  # dropped to baseline


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


def test_keyword_topic_status_counts_missing(client, auth_headers, db):
    """Issue 12: coverage status reports total papers and how many lack keywords/topics."""
    from app.models.work import Work

    db.add_all(
        [
            Work(canonical_title="has kw", keywords=["a"], topics=[]),
            Work(canonical_title="no kw", keywords=[], topics=["t"]),
            Work(canonical_title="empty", keywords=[], topics=[]),
        ]
    )
    db.commit()
    r = client.get("/api/v1/admin/ai/keyword-topic-status", headers=auth_headers("owner"))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["keywords_missing"] == 2  # "no kw" + "empty"
    assert body["topics_missing"] == 2  # "has kw" + "empty"


def test_batch_keywords_missing_only_enqueues_the_gap(client, auth_headers, db, monkeypatch):
    """Issue 12: scope='missing' queues only papers lacking keywords; 'all' queues every paper."""
    from app.models.work import Work
    from app.workers import queue as queue_mod

    with_kw = Work(canonical_title="with", keywords=["x"])
    without_kw = Work(canonical_title="without", keywords=[])
    db.add_all([with_kw, without_kw])
    db.commit()
    enqueued: list = []
    monkeypatch.setattr(queue_mod, "enqueue_keywords", lambda wid: enqueued.append(str(wid)) or "j")

    r = client.post(
        "/api/v1/admin/ai/keywords/batch", headers=auth_headers("owner"), json={"scope": "missing"}
    )
    assert r.status_code == 202
    body = r.json()
    assert body["eligible"] == 1 and body["queued"] == 1
    assert enqueued == [str(without_kw.id)]


def test_batch_keywords_all_scope_and_owner_only(client, auth_headers, db, monkeypatch):
    from app.models.work import Work
    from app.workers import queue as queue_mod

    db.add_all([Work(canonical_title="a", keywords=["x"]), Work(canonical_title="b", keywords=[])])
    db.commit()
    monkeypatch.setattr(queue_mod, "enqueue_keywords", lambda wid: "j")
    r = client.post(
        "/api/v1/admin/ai/keywords/batch", headers=auth_headers("owner"), json={"scope": "all"}
    )
    assert r.status_code == 202
    assert r.json()["eligible"] == 2  # every current paper
    # Non-admin is rejected.
    assert (
        client.post(
            "/api/v1/admin/ai/keywords/batch", headers=auth_headers("editor"), json={"scope": "all"}
        ).status_code
        == 403
    )
