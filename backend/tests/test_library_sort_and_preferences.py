"""Phase D: list_works SAFE sort allowlist + per-user preferences round-trip."""

import pytest


def _create(client, headers, title: str) -> dict:
    return client.post("/api/v1/works", headers=headers, json={"canonical_title": title}).json()


def _titles(client, headers, **params) -> list[str]:
    resp = client.get("/api/v1/works", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [w["canonical_title"] for w in resp.json()]


# --- #3 sort allowlist ---------------------------------------------------------------------------


def test_sort_title_asc_and_desc(client, auth_headers):
    h = auth_headers("editor")
    for t in ("Banana", "Apple", "Cherry"):
        _create(client, h, t)

    asc = _titles(client, h, sort="title", order="asc")
    assert asc == sorted(asc)
    desc = _titles(client, h, sort="title", order="desc")
    assert desc == sorted(desc, reverse=True)


def test_unknown_sort_falls_back_to_default(client, auth_headers):
    h = auth_headers("editor")
    for t in ("One", "Two"):
        _create(client, h, t)
    # An unknown key must not error and must not be interpolated — it falls back to updated_at desc.
    resp = client.get("/api/v1/works", headers=h, params={"sort": "bogus_column"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_sort_injection_is_safe(client, auth_headers):
    h = auth_headers("editor")
    _create(client, h, "Safe")
    # A SQL-injection-style sort value is treated as an unknown key (allowlist), never executed.
    resp = client.get(
        "/api/v1/works",
        headers=h,
        params={"sort": "canonical_title; DROP TABLE works;--"},
    )
    assert resp.status_code == 200
    # The table still works afterwards.
    assert client.get("/api/v1/works", headers=h).status_code == 200


def test_invalid_order_is_rejected(client, auth_headers):
    h = auth_headers("editor")
    resp = client.get("/api/v1/works", headers=h, params={"sort": "title", "order": "sideways"})
    assert resp.status_code == 422  # order has a strict asc|desc pattern


def test_added_at_sort_uses_created_at(client, auth_headers):
    h = auth_headers("editor")
    a = _create(client, h, "First added")
    b = _create(client, h, "Second added")
    asc = client.get("/api/v1/works", headers=h, params={"sort": "added_at", "order": "asc"}).json()
    ids = [w["id"] for w in asc]
    assert ids.index(a["id"]) < ids.index(b["id"])


# --- #4 preferences round-trip --------------------------------------------------------------------


@pytest.fixture()
def prefs_tmp_path(tmp_path, monkeypatch):
    """Point the preferences store at an isolated tmp file via the setting."""
    target = tmp_path / "nested" / "preferences.yaml"
    monkeypatch.setenv("PARACORD_PREFERENCES_PATH", str(target))
    import app.core.config as config

    # The service reads get_settings().preferences_path on every call; clearing the lru_cache makes
    # it pick up the patched env var. No module reload needed (the endpoint holds live references).
    config.get_settings.cache_clear()
    yield target
    config.get_settings.cache_clear()


def test_preferences_get_default_empty(client, auth_headers, prefs_tmp_path):
    resp = client.get("/api/v1/preferences", headers=auth_headers("reader"))
    assert resp.status_code == 200
    assert resp.json() == {"library_columns": None}


def test_preferences_put_then_get_roundtrip(client, auth_headers, prefs_tmp_path):
    h = auth_headers("reader")
    payload = {
        "library_columns": {
            "order": ["title", "year", "doi"],
            "visible": ["title", "doi"],
            "sort": {"key": "title", "order": "asc"},
        }
    }
    put = client.put("/api/v1/preferences", headers=h, json=payload)
    assert put.status_code == 200, put.text
    assert put.json()["library_columns"]["visible"] == ["title", "doi"]

    got = client.get("/api/v1/preferences", headers=h)
    assert got.json()["library_columns"]["order"] == ["title", "year", "doi"]
    # The atomic write actually created the file at the configured tmp path.
    assert prefs_tmp_path.exists()


def test_preferences_are_per_user_isolated(client, auth_headers, prefs_tmp_path):
    alice = auth_headers("editor", username="alice")
    bob = auth_headers("editor", username="bob")
    client.put(
        "/api/v1/preferences",
        headers=alice,
        json={
            "library_columns": {
                "order": ["title"],
                "visible": ["title"],
                "sort": {"key": "year", "order": "desc"},
            }
        },
    )
    # Bob hasn't saved anything — his blob stays empty, not Alice's.
    assert client.get("/api/v1/preferences", headers=bob).json() == {"library_columns": None}
    # Alice still sees hers.
    assert (
        client.get("/api/v1/preferences", headers=alice).json()["library_columns"]["sort"]["key"]
        == "year"
    )


def test_preferences_require_auth(client, prefs_tmp_path):
    assert client.get("/api/v1/preferences").status_code == 401
