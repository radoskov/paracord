"""Dynamic embedding-model registry (#21): slug/parse units + admin listing.

Runtime pgvector-column provisioning is Postgres-only DDL and is exercised against a live Postgres
in the manual/integration path; here we cover the dialect-agnostic logic and the admin API surface.
"""

from app.services import embedding_registry as er


def test_slugify_is_sql_safe_and_bounded():
    assert er.slugify("ollama:nomic-embed-text:latest") == "ollama_nomic_embed_text_latest"
    assert er.slugify("st:sentence-transformers/all-MiniLM-L6-v2").startswith("st_sentence")
    assert er.slugify("") == "model"
    # only [a-z0-9_], and short enough that 'vec_' + slug fits Postgres' 63-char identifier limit.
    slug = er.slugify("X" * 200)
    assert len(slug) <= 56
    assert all(c.islower() or c.isdigit() or c == "_" for c in slug)


def test_parse_model_name_splits_provider_and_raw():
    assert er._parse_model_name("ollama:nomic-embed-text:latest") == (
        "ollama",
        "nomic-embed-text:latest",
    )
    assert er._parse_model_name("st:all-MiniLM-L6-v2") == (
        "sentence_transformers",
        "all-MiniLM-L6-v2",
    )
    assert er._parse_model_name("hash-bow-v1") == ("hash_bow", "hash-bow-v1")


def test_embedding_models_endpoint_lists_registered(client, auth_headers):
    r = client.get("/api/v1/admin/ai/embedding-models", headers=auth_headers("owner"))
    assert r.status_code == 200
    body = r.json()
    assert "models" in body
    assert "max_models" in body
    assert isinstance(body["multimode_available"], bool)


def test_embedding_models_endpoint_is_admin_only(client, auth_headers):
    assert (
        client.get("/api/v1/admin/ai/embedding-models", headers=auth_headers("editor")).status_code
        == 403
    )
