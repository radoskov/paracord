"""GUI-managed server import roots, merged with the read-only server.yaml entries (batch 2 #19).

Covers the service-level merge + validation and the owner-only management API, plus the safety
invariant that the server-folder import accepts a GUI-added root yet still rejects traversal /
outside-root paths.
"""

import uuid
from pathlib import Path

import pytest
from app.core.config import Settings
from app.core.security import hash_password
from app.models.import_root import ImportRoot
from app.models.user import User
from app.services.import_roots import add_import_root, list_merged_roots, remove_import_root
from app.services.storage import (
    _assert_inside_root,
    create_server_folder_source,
    merged_server_roots,
)


@pytest.fixture()
def owner_user(db) -> User:
    user = User(username="owner-roots", password_hash=hash_password("secret"), role="owner")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --- service: merged set --------------------------------------------------------------------


def test_merged_set_returns_yaml_and_db_roots(db, owner_user, tmp_path: Path) -> None:
    yaml_dir = tmp_path / "yaml_root"
    db_dir = tmp_path / "db_root"
    yaml_dir.mkdir()
    db_dir.mkdir()
    settings = Settings(server_allowed_roots=[{"alias": "fixed", "path": str(yaml_dir)}])

    add_import_root(
        db, settings=settings, alias="added", path=str(db_dir), created_by_user_id=owner_user.id
    )
    db.commit()

    merged = merged_server_roots(db, settings)
    assert merged == {"fixed": yaml_dir.resolve(), "added": db_dir.resolve()}


def test_list_marks_yaml_fixed_vs_db_removable(db, owner_user, tmp_path: Path) -> None:
    yaml_dir = tmp_path / "y"
    db_dir = tmp_path / "d"
    yaml_dir.mkdir()
    db_dir.mkdir()
    settings = Settings(server_allowed_roots=[{"alias": "fixed", "path": str(yaml_dir)}])
    add_import_root(
        db, settings=settings, alias="added", path=str(db_dir), created_by_user_id=owner_user.id
    )
    db.commit()

    items = {item["alias"]: item for item in list_merged_roots(db, settings)}
    assert items["fixed"]["source"] == "yaml"
    assert items["fixed"]["removable"] is False
    assert items["fixed"]["id"] is None
    assert items["added"]["source"] == "db"
    assert items["added"]["removable"] is True
    assert items["added"]["id"] is not None


# --- service: add validation ----------------------------------------------------------------


def test_add_root_rejects_nonexistent_path(db, owner_user, tmp_path: Path) -> None:
    settings = Settings(server_allowed_roots=[])
    with pytest.raises(ValueError, match="existing directory"):
        add_import_root(
            db,
            settings=settings,
            alias="ghost",
            path=str(tmp_path / "does-not-exist"),
            created_by_user_id=owner_user.id,
        )


def test_add_root_rejects_file_path(db, owner_user, tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    settings = Settings(server_allowed_roots=[])
    with pytest.raises(ValueError, match="existing directory"):
        add_import_root(
            db,
            settings=settings,
            alias="not-a-dir",
            path=str(f),
            created_by_user_id=owner_user.id,
        )


def test_add_root_rejects_duplicate_alias_against_yaml(db, owner_user, tmp_path: Path) -> None:
    yaml_dir = tmp_path / "y"
    other = tmp_path / "o"
    yaml_dir.mkdir()
    other.mkdir()
    settings = Settings(server_allowed_roots=[{"alias": "fixed", "path": str(yaml_dir)}])
    with pytest.raises(ValueError, match="already in use"):
        add_import_root(
            db,
            settings=settings,
            alias="fixed",
            path=str(other),
            created_by_user_id=owner_user.id,
        )


def test_add_root_rejects_duplicate_alias_against_db(db, owner_user, tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    settings = Settings(server_allowed_roots=[])
    add_import_root(
        db, settings=settings, alias="dup", path=str(a), created_by_user_id=owner_user.id
    )
    db.commit()
    with pytest.raises(ValueError, match="already in use"):
        add_import_root(
            db, settings=settings, alias="dup", path=str(b), created_by_user_id=owner_user.id
        )


def test_remove_db_root_works(db, owner_user, tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    settings = Settings(server_allowed_roots=[])
    root = add_import_root(
        db, settings=settings, alias="x", path=str(d), created_by_user_id=owner_user.id
    )
    db.commit()
    remove_import_root(db, root_id=root.id)
    db.commit()
    assert db.get(ImportRoot, root.id) is None


def test_remove_missing_root_raises(db) -> None:
    with pytest.raises(ValueError, match="not found"):
        remove_import_root(db, root_id=uuid.uuid4())


# --- safety: import accepts a GUI root yet still rejects traversal --------------------------


def test_import_accepts_gui_added_root(db, owner_user, tmp_path: Path) -> None:
    folder = tmp_path / "gui"
    folder.mkdir()
    (folder / "p.pdf").write_bytes(b"%PDF-1.4\n% fixture\n")
    settings = Settings(server_allowed_roots=[])  # no yaml roots at all
    add_import_root(
        db, settings=settings, alias="gui", path=str(folder), created_by_user_id=owner_user.id
    )
    db.commit()

    # The GUI alias is now a valid server-folder source even though server.yaml has no roots.
    source = create_server_folder_source(
        db, settings=settings, name="GUI folder", path_alias="gui", actor=owner_user
    )
    assert source.config["root_path"] == str(folder.resolve())


def test_import_rejects_unknown_alias(db, owner_user, tmp_path: Path) -> None:
    settings = Settings(server_allowed_roots=[])
    with pytest.raises(ValueError, match="Unknown server-folder alias"):
        create_server_folder_source(
            db, settings=settings, name="bad", path_alias="nope", actor=owner_user
        )


def test_path_containment_still_rejects_outside_paths(tmp_path: Path) -> None:
    """A GUI root does not weaken the anti-traversal containment check."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF-1.4\n")
    # A path outside the (GUI-added or yaml) root is rejected exactly as before.
    with pytest.raises(ValueError):
        _assert_inside_root(root, outside / "secret.pdf")
    # An escaping ../ path is likewise rejected.
    with pytest.raises(ValueError):
        _assert_inside_root(root, root / ".." / "outside" / "secret.pdf")


# --- API: owner-only management -------------------------------------------------------------


def test_api_add_list_remove_owner(client, auth_headers, tmp_path: Path) -> None:
    owner = auth_headers("owner")
    folder = tmp_path / "api_root"
    folder.mkdir()

    created = client.post(
        "/api/v1/admin/import-roots",
        headers=owner,
        json={"alias": "apiroot", "path": str(folder)},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "db"
    assert body["removable"] is True
    root_id = body["id"]

    listed = client.get("/api/v1/admin/import-roots", headers=owner)
    assert listed.status_code == 200
    aliases = {item["alias"] for item in listed.json()}
    assert "apiroot" in aliases

    removed = client.delete(f"/api/v1/admin/import-roots/{root_id}", headers=owner)
    assert removed.status_code == 204
    aliases_after = {
        item["alias"] for item in client.get("/api/v1/admin/import-roots", headers=owner).json()
    }
    assert "apiroot" not in aliases_after


def test_api_add_validates_path(client, auth_headers, tmp_path: Path) -> None:
    r = client.post(
        "/api/v1/admin/import-roots",
        headers=auth_headers("owner"),
        json={"alias": "ghost", "path": str(tmp_path / "missing")},
    )
    assert r.status_code == 400


def test_api_management_is_owner_only(client, auth_headers, tmp_path: Path) -> None:
    folder = tmp_path / "f"
    folder.mkdir()
    for role in ("editor", "reader", "admin"):
        headers = auth_headers(role)
        assert client.get("/api/v1/admin/import-roots", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/v1/admin/import-roots",
                headers=headers,
                json={"alias": f"r-{role}", "path": str(folder)},
            ).status_code
            == 403
        )
        assert (
            client.delete(f"/api/v1/admin/import-roots/{uuid.uuid4()}", headers=headers).status_code
            == 403
        )


def test_api_remove_missing_is_404(client, auth_headers) -> None:
    r = client.delete(f"/api/v1/admin/import-roots/{uuid.uuid4()}", headers=auth_headers("owner"))
    assert r.status_code == 404
