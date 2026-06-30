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


def test_providers_detection_and_reindex_status(client, auth_headers):
    owner = auth_headers("owner")
    providers = client.get("/api/v1/admin/ai/providers", headers=owner)
    assert providers.status_code == 200
    body = providers.json()
    assert body["embedding"]["hash_bow"]["available"] is True
    # Ollama is unreachable in CI → reported unavailable with a how-to-enable note.
    assert body["embedding"]["ollama"]["available"] is False
    assert body["embedding"]["ollama"]["note"]

    status = client.get("/api/v1/admin/ai/reindex/status", headers=owner)
    assert status.status_code == 200
    assert status.json()["model_name"] == "hash-bow-v1"
    assert "indexed" in status.json()


def test_models_list_when_ollama_down(client, auth_headers):
    # No Ollama in CI → an empty model list, not an error.
    r = client.get("/api/v1/admin/ai/models", headers=auth_headers("owner"))
    assert r.status_code == 200
    assert r.json() == {"models": []}
