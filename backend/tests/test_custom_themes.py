"""Runtime custom-theme store + endpoints (Theming P4).

Covers: upload a valid theme -> it appears in GET /themes and resolves via GET /themes/{slug};
malformed YAML -> 400; a missing required token role -> 400; a categorical palette that fails the
readability check -> ACCEPTED with warnings (not rejected); non-admin write -> 403; delete works and
emits an audit event; a per-user theme preference accepts a custom slug.
"""

import textwrap

from app.models.audit import AuditEvent
from sqlalchemy import select

# A structurally-complete theme built with palette refs; its categorical palette is the validated
# mocha-warm data palette so it passes the readability check cleanly.
VALID_THEME = textwrap.dedent(
    """
    id: ocean-dusk
    name: "Ocean Dusk"
    mode: dark
    temperature: cool
    palette:
      base: "#1e1e2e"
      text: "#cdd6f4"
      subtext1: "#bac2de"
      muted: "#9399b2"
      blue: "#89b4fa"
    tokens:
      surface: {base: "#211e2a", raised: "#2a2536", overlay: "#322c40", sunken: "#191622", hover: "#302a3d"}
      ink: {strong: palette.text, normal: palette.subtext1, muted: palette.muted, inverse: "#1e1e2e"}
      border: {normal: "#3a3547", strong: "#4a4458", focus: palette.blue}
      accent: {primary: "#89b4fa", primary-strong: "#6a9bf0", secondary: "#bac2de", link: "#89b4fa", note: "#cba6f7", note-bg: "#2a2340", note-border: "#453a66"}
      status:
        success: "#a6e3a1"
        success-bg: "#22311f"
        success-border: "#3d5c3a"
        warning: "#f9e2af"
        warning-bg: "#332b17"
        warning-border: "#5c4f2a"
        danger: "#f38ba8"
        danger-bg: "#3a2029"
        danger-border: "#5c2f3a"
        info: "#89dceb"
        info-bg: "#17303a"
        info-border: "#2a5560"
      radius: {sm: "6px", md: "8px"}
      font: {family: "Inter, sans-serif"}
    graph:
      surface: "#211e2a"
      categorical:
        - "#cf7020"
        - "#4a7fd0"
        - "#e04a68"
        - "#1a9a9a"
        - "#a88a20"
        - "#a55fe0"
        - "#d85fa8"
        - "#2e9a52"
    """
).strip()


def _upload(client, headers, yaml_text):
    return client.post("/api/v1/admin/themes", headers=headers, json={"yaml": yaml_text})


def test_upload_valid_theme_lists_and_resolves(client, auth_headers):
    admin = auth_headers("admin")
    reader = auth_headers("reader")

    r = _upload(client, admin, VALID_THEME)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "ocean-dusk"
    assert body["mode"] == "dark"
    # A clean palette produces no readability warnings.
    assert body["warnings"] == []

    # Any authenticated user (even a reader) may list it for their picker.
    listing = client.get("/api/v1/themes", headers=reader)
    assert listing.status_code == 200
    ids = [t["id"] for t in listing.json()]
    assert "ocean-dusk" in ids
    item = next(t for t in listing.json() if t["id"] == "ocean-dusk")
    assert item["swatch"]["surface"] == "#211e2a"
    assert item["swatch"]["primary"] == "#89b4fa"
    assert len(item["swatch"]["accents"]) == 4

    # It resolves to the full Theme object (palette refs resolved, graph defaults filled).
    resolved = client.get("/api/v1/themes/ocean-dusk", headers=reader)
    assert resolved.status_code == 200
    obj = resolved.json()
    assert obj["tokens"]["ink"]["strong"] == "#cdd6f4"  # palette.text resolved
    assert obj["tokens"]["border"]["focus"] == "#89b4fa"  # palette.blue resolved
    assert obj["graph"]["categorical"][0] == "#cf7020"
    # Omitted presentational graph keys are defaulted from the tokens.
    assert obj["graph"]["text"] == "#bac2de"
    assert obj["graph"]["diverging"]["low"] == "#cf7020"


def test_upload_replaces_by_slug(client, auth_headers):
    admin = auth_headers("admin")
    assert _upload(client, admin, VALID_THEME).status_code == 201
    renamed = VALID_THEME.replace('name: "Ocean Dusk"', 'name: "Ocean Dawn"')
    r = _upload(client, admin, renamed)
    assert r.status_code == 201
    assert r.json()["name"] == "Ocean Dawn"
    # Still a single row (replace, not duplicate).
    listing = client.get("/api/v1/themes", headers=admin).json()
    assert [t["id"] for t in listing].count("ocean-dusk") == 1


def test_malformed_yaml_rejected_400(client, auth_headers):
    admin = auth_headers("admin")
    r = _upload(client, admin, "id: [unterminated\n  bad: :::")
    assert r.status_code == 400


def test_missing_required_token_role_rejected_400(client, auth_headers):
    admin = auth_headers("admin")
    # Drop accent.primary — a required role token.
    broken = VALID_THEME.replace(
        'accent: {primary: "#89b4fa", primary-strong: "#6a9bf0",',
        'accent: {primary-strong: "#6a9bf0",',
    )
    r = _upload(client, admin, broken)
    assert r.status_code == 400
    assert "accent.primary" in r.json()["detail"]


def test_invalid_mode_rejected_400(client, auth_headers):
    admin = auth_headers("admin")
    r = _upload(client, admin, VALID_THEME.replace("mode: dark", "mode: twilight"))
    assert r.status_code == 400


def test_bad_palette_accepted_with_warning(client, auth_headers):
    admin = auth_headers("admin")
    # Replace the whole graph block's palette with near-identical greys: fails chroma floor + CVD,
    # but the theme must still be ACCEPTED (warn, don't reject).
    grey_graph = textwrap.dedent(
        """
        graph:
          surface: "#211e2a"
          categorical:
            - "#808080"
            - "#828282"
            - "#858585"
        """
    ).strip()
    bad = VALID_THEME.split("graph:")[0] + grey_graph
    bad = bad.replace("id: ocean-dusk", "id: grey-soup")
    r = _upload(client, admin, bad)
    assert r.status_code == 201, r.text
    assert r.json()["warnings"], "expected readability warnings for a grey palette"
    # And it is still stored/listed despite the warnings.
    ids = [t["id"] for t in client.get("/api/v1/themes", headers=admin).json()]
    assert "grey-soup" in ids


def test_slug_colliding_with_bundled_theme_rejected_400(client, auth_headers):
    admin = auth_headers("admin")
    r = _upload(client, admin, VALID_THEME.replace("id: ocean-dusk", "id: mocha-warm"))
    assert r.status_code == 400


def test_non_admin_cannot_write(client, auth_headers):
    reader = auth_headers("reader")
    editor = auth_headers("editor")
    assert _upload(client, reader, VALID_THEME).status_code == 403
    assert _upload(client, editor, VALID_THEME).status_code == 403


def test_delete_removes_and_audits(client, auth_headers, db):
    admin = auth_headers("admin")
    assert _upload(client, admin, VALID_THEME).status_code == 201

    r = client.delete("/api/v1/admin/themes/ocean-dusk", headers=admin)
    assert r.status_code == 204
    assert client.get("/api/v1/themes/ocean-dusk", headers=admin).status_code == 404

    events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "theme.deleted")).all()
    assert any(e.entity_id == "ocean-dusk" for e in events)
    # The upload was audited too.
    assert db.scalars(select(AuditEvent).where(AuditEvent.event_type == "theme.uploaded")).all()


def test_delete_missing_theme_404(client, auth_headers):
    admin = auth_headers("admin")
    assert client.delete("/api/v1/admin/themes/nope", headers=admin).status_code == 404


def test_profile_accepts_custom_theme_slug(client, auth_headers):
    admin = auth_headers("admin")
    assert _upload(client, admin, VALID_THEME).status_code == 201
    # A user may now persist the custom slug as their theme preference.
    r = client.patch("/api/v1/auth/me", headers=admin, json={"theme": "ocean-dusk"})
    assert r.status_code == 200
    assert r.json()["theme"] == "ocean-dusk"
