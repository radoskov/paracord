"""Phase D: list_works SAFE sort allowlist + per-user preferences round-trip."""

import pytest


def _create(client, headers, title: str) -> dict:
    return client.post("/api/v1/works", headers=headers, json={"canonical_title": title}).json()


def _titles(client, headers, **params) -> list[str]:
    resp = client.get("/api/v1/works", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [w["canonical_title"] for w in resp.json()["items"]]


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
    assert len(resp.json()["items"]) == 2


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
    ids = [w["id"] for w in asc["items"]]
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
    # The atomic write actually created the per-user file next to the configured path (S11).
    assert list((prefs_tmp_path.parent / "preferences.d").glob("*.yaml"))


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


def test_preferences_read_falls_back_to_legacy_shared_file(
    client, auth_headers, prefs_tmp_path
) -> None:
    """S11 lazy migration: a user who never wrote since the per-user split still reads their slice
    from the old single shared file; their first write lands in the per-user file and wins."""
    import yaml
    from app.services import preferences as prefs

    h = auth_headers("reader")
    me = client.get("/api/v1/auth/me", headers=h).json()
    legacy = prefs._preferences_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        yaml.safe_dump(
            {"version": 1, "users": {me["id"]: {"library_columns": {"order": ["title"]}}}}
        ),
        encoding="utf-8",
    )
    got = client.get("/api/v1/preferences", headers=h).json()
    assert got["library_columns"]["order"] == ["title"]

    # A write goes to the per-user file and shadows the legacy slice from then on.
    client.put("/api/v1/preferences", headers=h, json={"library_columns": {"order": ["year"]}})
    assert (legacy.parent / "preferences.d" / f"{me['id']}.yaml").exists()
    got = client.get("/api/v1/preferences", headers=h).json()
    assert got["library_columns"]["order"] == ["year"]


def test_preferences_writes_are_one_file_per_user(client, auth_headers, prefs_tmp_path) -> None:
    """S11: each user's save touches only their own file (no shared read-modify-write)."""
    from app.services import preferences as prefs

    alice = auth_headers("owner")
    bob = auth_headers("reader")
    client.put("/api/v1/preferences", headers=alice, json={"library_columns": {"a": 1}})
    client.put("/api/v1/preferences", headers=bob, json={"library_columns": {"b": 2}})
    user_dir = prefs._preferences_path().parent / "preferences.d"
    assert len(list(user_dir.glob("*.yaml"))) == 2
    assert client.get("/api/v1/preferences", headers=alice).json()["library_columns"]["a"] == 1
    assert client.get("/api/v1/preferences", headers=bob).json()["library_columns"]["b"] == 2
