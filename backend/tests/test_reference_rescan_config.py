"""F3a — the reference-rescan-on-startup toggle (AppConfig).

Covers the model/service/endpoint plumbing for the owner toggle that (on startup) enqueues a full
library-wide reference→work rematch. The 6-line startup glue in ``main.lifespan`` mirrors the
already-tested D7 owed-extraction sweep and is exercised at integration time.
"""

from app.services.app_config import (
    effective_reference_rescan_on_startup,
    update_reference_rescan_on_startup,
)


def test_rescan_on_startup_defaults_off_and_persists(db) -> None:
    assert effective_reference_rescan_on_startup(db) is False  # absent row → OFF
    update_reference_rescan_on_startup(db, value=True)
    assert effective_reference_rescan_on_startup(db) is True
    update_reference_rescan_on_startup(db, value=False)
    assert effective_reference_rescan_on_startup(db) is False


def test_admin_app_config_roundtrips_rescan_toggle(client, auth_headers) -> None:
    headers = auth_headers("owner")

    got = client.get("/api/v1/admin/app-config", headers=headers).json()
    assert got["reference_rescan_on_startup"] is False

    resp = client.patch(
        "/api/v1/admin/app-config",
        headers=headers,
        json={"reference_rescan_on_startup": True},
    )
    assert resp.status_code == 200
    assert resp.json()["reference_rescan_on_startup"] is True

    # Persists on a fresh GET, and the toggle didn't disturb the other config fields.
    got2 = client.get("/api/v1/admin/app-config", headers=headers).json()
    assert got2["reference_rescan_on_startup"] is True
    assert "use_fuzzy_match_as_confirmed" in got2
    assert "max_papers_per_page" in got2


def test_app_config_rescan_toggle_requires_admin(client, auth_headers) -> None:
    resp = client.patch(
        "/api/v1/admin/app-config",
        headers=auth_headers("editor"),
        json={"reference_rescan_on_startup": True},
    )
    assert resp.status_code == 403
