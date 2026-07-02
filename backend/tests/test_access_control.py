"""Phase H access-control tests: role ladder, SEE/MODIFY matrix, list filtering, group lifecycle.

These exercise the permission layer (``app.services.access``) directly and over HTTP, plus the
user/group lifecycle wiring. They use the in-memory SQLite app fixtures from conftest.
"""

import uuid

import pytest
from app.core.security import Role, role_at_least
from app.models.group import DefaultGrant, Group, GroupGrant, GroupMembership
from app.models.organization import Rack, Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.auth import create_user_session
from sqlalchemy import select


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------
def _shelf(db, *, name="s", access_level="open"):
    shelf = Shelf(name=name, access_level=access_level)
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    return shelf


def _rack(db, *, name="r", access_level="open"):
    rack = Rack(name=name, access_level=access_level)
    db.add(rack)
    db.commit()
    db.refresh(rack)
    return rack


def _work(db, *, title="w", created_by=None):
    work = Work(canonical_title=title, created_by_user_id=created_by)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _add_to_shelf(db, shelf, work):
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()


def _grant(db, user, target_type, target_id):
    """Give a user a grant to a target via a fresh group + membership + grant."""
    group = Group(name=f"g-{uuid.uuid4().hex[:8]}", is_personal=False)
    db.add(group)
    db.flush()
    db.add(GroupMembership(group_id=group.id, user_id=user.id))
    db.add(GroupGrant(group_id=group.id, target_type=target_type, target_id=target_id))
    db.commit()


# --------------------------------------------------------------------------------------------------
# Role ladder
# --------------------------------------------------------------------------------------------------
def test_role_ladder_order():
    order = [Role.READER, Role.CONTRIBUTOR, Role.EDITOR, Role.LIBRARIAN, Role.ADMIN, Role.OWNER]
    for lower, higher in zip(order, order[1:], strict=False):
        assert role_at_least(higher, lower)
        assert not role_at_least(lower, higher)


def test_contributor_passes_reader_floor_admin_passes_all():
    assert role_at_least(Role.CONTRIBUTOR, Role.READER)
    assert role_at_least(Role.ADMIN, Role.CONTRIBUTOR)
    assert role_at_least(Role.OWNER, Role.LIBRARIAN)
    assert not role_at_least(Role.EDITOR, Role.LIBRARIAN)


# --------------------------------------------------------------------------------------------------
# Rack/shelf SEE + MODIFY matrix
# --------------------------------------------------------------------------------------------------
def test_shelf_see_matrix(db, make_user):
    reader = make_user("see-reader", role="reader")
    admin = make_user("see-admin", role="admin")
    open_s = _shelf(db, name="open", access_level="open")
    visible_s = _shelf(db, name="visible", access_level="visible")
    private_s = _shelf(db, name="private", access_level="private")

    assert access.can_see_shelf(db, reader, open_s)
    assert access.can_see_shelf(db, reader, visible_s)
    assert not access.can_see_shelf(db, reader, private_s)
    # admin bypasses everything
    assert access.can_see_shelf(db, admin, private_s)
    # a grant unlocks the private shelf
    _grant(db, reader, "shelf", private_s.id)
    assert access.can_see_shelf(db, reader, private_s)


def test_shelf_modify_matrix_needs_librarian_and_grant(db, make_user):
    editor = make_user("mod-editor", role="editor")
    librarian = make_user("mod-librarian", role="librarian")
    owner = make_user("mod-owner", role="owner")
    open_s = _shelf(db, name="open", access_level="open")
    visible_s = _shelf(db, name="visible", access_level="visible")

    # editor can never modify structure
    assert not access.can_modify_shelf(db, editor, open_s)
    # librarian modifies open by role alone
    assert access.can_modify_shelf(db, librarian, open_s)
    # but visible needs a grant — "not even a librarian without a grant"
    assert not access.can_modify_shelf(db, librarian, visible_s)
    _grant(db, librarian, "shelf", visible_s.id)
    assert access.can_modify_shelf(db, librarian, visible_s)
    # owner bypasses
    assert access.can_modify_shelf(db, owner, visible_s)


def test_visible_needs_grant_to_modify_rack(db, make_user):
    librarian = make_user("rack-librarian", role="librarian")
    visible_r = _rack(db, name="vr", access_level="visible")
    assert access.can_see_rack(db, librarian, visible_r)  # visible -> see-all
    assert not access.can_modify_rack(db, librarian, visible_r)  # but modify needs a grant
    _grant(db, librarian, "rack", visible_r.id)
    assert access.can_modify_rack(db, librarian, visible_r)


# --------------------------------------------------------------------------------------------------
# Paper SEE + MODIFY (most-permissive shelf, loose = open)
# --------------------------------------------------------------------------------------------------
def test_loose_paper_is_open(db, make_user):
    reader = make_user("loose-reader", role="reader")
    contributor = make_user("loose-contrib", role="contributor")
    loose = _work(db, title="loose")  # in no shelf
    assert access.can_see_work(db, reader, loose)
    # contributor can modify a loose paper only if they own it
    assert not access.can_modify_work(db, contributor, loose)
    owned = _work(db, title="owned", created_by=contributor.id)
    assert access.can_modify_work(db, contributor, owned)


def test_most_permissive_shelf_decides_see(db, make_user):
    reader = make_user("mp-reader", role="reader")
    private_s = _shelf(db, name="p", access_level="private")
    open_s = _shelf(db, name="o", access_level="open")
    work = _work(db, title="shared")
    _add_to_shelf(db, private_s, work)
    # only on a private shelf -> hidden
    assert not access.can_see_work(db, reader, work)
    # also place on an open shelf -> the most-permissive shelf makes it visible
    _add_to_shelf(db, open_s, work)
    assert access.can_see_work(db, reader, work)


def test_contributor_own_only_editor_any_seen(db, make_user):
    contributor = make_user("co", role="contributor")
    editor = make_user("ed", role="editor")
    open_s = _shelf(db, name="opensh", access_level="open")
    others = _work(db, title="others")  # created_by NULL
    _add_to_shelf(db, open_s, others)
    # contributor cannot modify a paper they don't own
    assert not access.can_modify_work(db, contributor, others)
    # editor can modify any see-able paper
    assert access.can_modify_work(db, editor, others)


def test_modify_visible_paper_needs_grant(db, make_user):
    editor = make_user("ved", role="editor")
    visible_s = _shelf(db, name="vs", access_level="visible")
    work = _work(db, title="vwork")
    _add_to_shelf(db, visible_s, work)
    # editor can SEE it (visible) but cannot MODIFY without a grant on the governing shelf
    assert access.can_see_work(db, editor, work)
    assert not access.can_modify_work(db, editor, work)
    _grant(db, editor, "shelf", visible_s.id)
    assert access.can_modify_work(db, editor, work)


def test_admin_owner_bypass_paper(db, make_user):
    admin = make_user("ad", role="admin")
    owner = make_user("ow", role="owner")
    private_s = _shelf(db, name="ps", access_level="private")
    work = _work(db, title="secret")
    _add_to_shelf(db, private_s, work)
    assert access.can_see_work(db, admin, work)
    assert access.can_modify_work(db, owner, work)


# --------------------------------------------------------------------------------------------------
# List filtering query builders
# --------------------------------------------------------------------------------------------------
def test_visible_works_query_filters_hidden(db, make_user):
    reader = make_user("vwq", role="reader")
    private_s = _shelf(db, name="vwq-priv", access_level="private")
    hidden = _work(db, title="hidden")
    _add_to_shelf(db, private_s, hidden)
    loose = _work(db, title="loose-vwq")
    ids = {w.id for w in db.scalars(access.visible_works_query(db, reader)).all()}
    assert loose.id in ids
    assert hidden.id not in ids
    # admin sees everything (unfiltered)
    admin = make_user("vwq-admin", role="admin")
    assert access.visible_work_ids(db, admin) is None


def test_visible_shelves_and_racks_query(db, make_user):
    reader = make_user("vsq", role="reader")
    open_s = _shelf(db, name="vsq-open", access_level="open")
    private_s = _shelf(db, name="vsq-priv", access_level="private")
    shelf_ids = {s.id for s in db.scalars(access.visible_shelves_query(db, reader)).all()}
    assert open_s.id in shelf_ids
    assert private_s.id not in shelf_ids
    _grant(db, reader, "shelf", private_s.id)
    shelf_ids = {s.id for s in db.scalars(access.visible_shelves_query(db, reader)).all()}
    assert private_s.id in shelf_ids


# --------------------------------------------------------------------------------------------------
# HTTP: role-ladder enforcement + list filtering never leaks hidden works
# --------------------------------------------------------------------------------------------------
def _headers(db, user):
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def test_http_reader_cannot_create_work_contributor_can(client, db, make_user):
    reader = make_user("h-reader", role="reader")
    contributor = make_user("h-contrib", role="contributor")
    r = client.post("/api/v1/works", headers=_headers(db, reader), json={"canonical_title": "x"})
    assert r.status_code == 403
    r = client.post(
        "/api/v1/works", headers=_headers(db, contributor), json={"canonical_title": "y"}
    )
    assert r.status_code == 201
    assert r.json()["created_by_user_id"] == str(contributor.id)


def test_http_editor_cannot_create_shelf_librarian_can(client, db, make_user):
    editor = make_user("h-editor", role="editor")
    librarian = make_user("h-librarian", role="librarian")
    assert (
        client.post("/api/v1/shelves", headers=_headers(db, editor), json={"name": "e"}).status_code
        == 403
    )
    r = client.post("/api/v1/shelves", headers=_headers(db, librarian), json={"name": "l"})
    assert r.status_code == 201


def test_http_contributor_own_only_edit(client, db, make_user):
    c1 = make_user("h-c1", role="contributor")
    c2 = make_user("h-c2", role="contributor")
    # c1 creates a paper
    r = client.post("/api/v1/works", headers=_headers(db, c1), json={"canonical_title": "mine"})
    work_id = r.json()["id"]
    # c2 cannot edit c1's paper
    r = client.patch(
        f"/api/v1/works/{work_id}", headers=_headers(db, c2), json={"canonical_title": "hacked"}
    )
    assert r.status_code == 403
    # c1 can
    r = client.patch(
        f"/api/v1/works/{work_id}", headers=_headers(db, c1), json={"canonical_title": "ok"}
    )
    assert r.status_code == 200


def test_http_list_works_filters_hidden(client, db, make_user):
    reader = make_user("h-list-reader", role="reader")
    private_s = _shelf(db, name="h-priv", access_level="private")
    hidden = _work(db, title="hidden-http")
    _add_to_shelf(db, private_s, hidden)
    _work(db, title="loose-http")
    titles = {
        w["canonical_title"]
        for w in client.get("/api/v1/works", headers=_headers(db, reader)).json()["items"]
    }
    assert "loose-http" in titles
    assert "hidden-http" not in titles


def test_http_get_hidden_work_404(client, db, make_user):
    reader = make_user("h-get-reader", role="reader")
    private_s = _shelf(db, name="h-get-priv", access_level="private")
    hidden = _work(db, title="hidden-get")
    _add_to_shelf(db, private_s, hidden)
    r = client.get(f"/api/v1/works/{hidden.id}", headers=_headers(db, reader))
    assert r.status_code == 404


@pytest.mark.parametrize("scope_path", ["/api/v1/graphs/citation", "/api/v1/exports"])
def test_http_graph_and_export_never_leak_hidden(client, db, make_user, scope_path):
    reader = make_user(f"h-leak-{uuid.uuid4().hex[:6]}", role="reader")
    private_s = _shelf(db, name=f"leak-{uuid.uuid4().hex[:6]}", access_level="private")
    hidden = _work(db, title="leak-secret")
    _add_to_shelf(db, private_s, hidden)
    if scope_path.endswith("citation"):
        body = {"scope": {"type": "library"}}
        r = client.post(scope_path, headers=_headers(db, reader), json=body)
        assert r.status_code == 200
        labels = {n["label"] for n in r.json()["nodes"]}
        assert "leak-secret" not in labels
    else:
        body = {"scope_type": "library", "format": "bibtex"}
        r = client.post(scope_path, headers=_headers(db, reader), json=body)
        assert r.status_code == 200
        assert "leak-secret" not in r.json()["content"]


def test_http_graph_selected_papers_scope_clamps_unseen_ids(client, db, make_user):
    """A reader passing an unseen work id to the explicit-set scope gets it clamped away (Phase B6).

    The frontend passes arbitrary ids for search_result/selected_papers; the visible-set clamp is the
    real enforcement, so an attacker's ids only intersect what they may already see.
    """
    reader = make_user(f"b6-clamp-{uuid.uuid4().hex[:6]}", role="reader")
    visible = _work(db, title="b6-visible")  # loose (open) -> visible
    private_s = _shelf(db, name=f"b6-priv-{uuid.uuid4().hex[:6]}", access_level="private")
    hidden = _work(db, title="b6-hidden-secret")
    _add_to_shelf(db, private_s, hidden)
    body = {
        "scope": {
            "type": "selected_papers",
            "work_ids": [str(visible.id), str(hidden.id)],
        }
    }
    r = client.post("/api/v1/graphs/citation", headers=_headers(db, reader), json=body)
    assert r.status_code == 200
    ids = {n["work_id"] for n in r.json()["nodes"]}
    labels = {n["label"] for n in r.json()["nodes"]}
    assert str(hidden.id) not in ids
    assert "b6-hidden-secret" not in labels
    assert str(visible.id) in ids


def test_http_semantic_search_filters_hidden(client, db, make_user):
    reader = make_user("h-sem-reader", role="reader")
    private_s = _shelf(db, name="sem-priv", access_level="private")
    hidden = _work(db, title="semantic secret topic")
    _add_to_shelf(db, private_s, hidden)
    r = client.post(
        "/api/v1/search/semantic",
        headers=_headers(db, reader),
        json={"q": "semantic secret topic", "mode": "lexical"},
    )
    assert r.status_code == 200
    ids = {item["work_id"] for item in r.json()["items"]}
    assert str(hidden.id) not in ids


# --------------------------------------------------------------------------------------------------
# Personal-group lifecycle
# --------------------------------------------------------------------------------------------------
def test_create_user_makes_personal_group(client, db, auth_headers):
    owner = auth_headers("owner")
    r = client.post(
        "/api/v1/admin/users",
        headers=owner,
        json={"username": "freshuser", "password": "test-pass-1234", "role": "reader"},
    )
    assert r.status_code == 201
    group = db.scalar(select(Group).where(Group.name == "freshuser"))
    assert group is not None
    assert group.is_personal


def test_delete_user_removes_personal_group(client, db, auth_headers):
    from app.services import users as users_service

    owner_headers = auth_headers("owner")
    owner = db.scalar(select(User).where(User.role == "owner"))
    # create
    r = client.post(
        "/api/v1/admin/users",
        headers=owner_headers,
        json={"username": "tempuser", "password": "test-pass-1234", "role": "reader"},
    )
    user_id = uuid.UUID(r.json()["id"])
    assert db.scalar(select(Group).where(Group.personal_user_id == user_id)) is not None
    # disable then delete via service (mirrors the admin flow)
    actor = db.get(User, owner.id)
    users_service.disable_user(db, user_id=user_id, actor=actor)
    db.commit()
    users_service.delete_user(db, user_id=user_id, actor=actor)
    db.commit()
    assert db.scalar(select(Group).where(Group.personal_user_id == user_id)) is None


def test_default_grants_applied_to_new_personal_group(client, db, auth_headers):
    from app.services import users as users_service

    owner_headers = auth_headers("owner")
    assert owner_headers  # ensure an owner exists
    owner = db.scalar(select(User).where(User.role == "owner"))
    shelf = _shelf(db, name="default-target", access_level="private")
    db.add(DefaultGrant(target_type="shelf", target_id=shelf.id))
    db.commit()
    actor = db.get(User, owner.id)
    new_user = users_service.create_user(
        db, username="withdefaults", password="test-pass-1234", role="reader", actor=actor
    )
    db.commit()
    group = db.scalar(select(Group).where(Group.personal_user_id == new_user.id))
    grants = list(db.scalars(select(GroupGrant).where(GroupGrant.group_id == group.id)))
    assert any(g.target_id == shelf.id for g in grants)


# --------------------------------------------------------------------------------------------------
# Username immutability regression (no role/groups change must reopen it)
# --------------------------------------------------------------------------------------------------
def test_username_immutable(db, make_user):
    from app.services.users import update_profile

    user = make_user("immutable-name", role="reader")
    with pytest.raises(ValueError):
        update_profile(db, user=user, changes={"username": "newname"}, actor_user_id=user.id)
    assert user.username == "immutable-name"


# --------------------------------------------------------------------------------------------------
# Admin group API
# --------------------------------------------------------------------------------------------------
def test_admin_group_crud_and_grant(client, db, auth_headers):
    admin = auth_headers("admin")
    # create a shared group
    r = client.post("/api/v1/admin/groups", headers=admin, json={"name": "team-a"})
    assert r.status_code == 201
    group_id = r.json()["id"]
    # grant it a shelf
    shelf = _shelf(db, name="team-shelf", access_level="private")
    r = client.post(
        f"/api/v1/admin/groups/{group_id}/grants",
        headers=admin,
        json={"target_type": "shelf", "target_id": str(shelf.id)},
    )
    assert r.status_code == 201
    # cannot delete a personal group via the API
    personal = db.scalar(select(Group).where(Group.is_personal.is_(True)))
    if personal is not None:
        r = client.delete(f"/api/v1/admin/groups/{personal.id}", headers=admin)
        assert r.status_code == 400


@pytest.mark.skipif(
    not __import__("app.core.config", fromlist=["get_settings"])
    .get_settings()
    .database_url.startswith("postgresql"),
    reason="backfill assertion requires a Postgres DATABASE_URL (migrations run on PG)",
)
def test_backfill_gave_every_existing_user_a_personal_group():
    """On Postgres, assert the 0029 backfill semantics: every user has a personal group.

    Builds a throwaway DB seeded with users BEFORE the access-control migrations, runs the full
    migration chain, then asserts every pre-existing user got a personal group named == username.
    """
    import os
    import uuid as _uuid
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from app.core.config import get_settings
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    # Resolve relative to this file so it works regardless of cwd
    # (backend/tests/ -> parents[2] is the repo root holding backend/alembic.ini).
    alembic_ini = str(Path(__file__).resolve().parents[2] / "backend/alembic.ini")

    server_url = get_settings().database_url
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    conn = admin.connect()
    db_name = f"backfill_{_uuid.uuid4().hex[:12]}"
    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    conn.close()
    test_url = server_url.rsplit("/", 1)[0] + "/" + db_name
    previous = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = test_url
        get_settings.cache_clear()
        cfg = Config(alembic_ini)
        # Migrate up to just before the access-control schema, seed users, then finish the chain.
        command.upgrade(cfg, "0027_web_find_settings")
        eng = create_engine(test_url, poolclass=NullPool)
        with eng.begin() as c:
            for i in range(3):
                c.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, role, is_bootstrap, "
                        "created_at) VALUES (:id, :u, 'x', 'reader', false, now())"
                    ),
                    {"id": _uuid.uuid4(), "u": f"preexisting{i}"},
                )
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            users = c.execute(text("SELECT username FROM users")).scalars().all()
            for username in users:
                grp = c.execute(
                    text(
                        "SELECT g.name FROM groups g JOIN group_memberships m "
                        "ON m.group_id = g.id JOIN users u ON u.id = m.user_id "
                        "WHERE u.username = :u AND g.is_personal = true"
                    ),
                    {"u": username},
                ).scalar()
                assert grp is not None, f"user {username} has no personal group after backfill"
        eng.dispose()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        drop = admin.connect()
        drop.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        drop.close()
        admin.dispose()


def test_admin_access_settings_roundtrip(client, auth_headers):
    admin = auth_headers("admin")
    r = client.get("/api/v1/admin/access-settings", headers=admin)
    assert r.status_code == 200
    assert r.json()["default_access_level"] == "open"
    r = client.put(
        "/api/v1/admin/access-settings", headers=admin, json={"default_access_level": "visible"}
    )
    assert r.status_code == 200
    assert r.json()["default_access_level"] == "visible"


# --------------------------------------------------------------------------------------------------
# Phase N — paper shelf-membership read (GET /works/{id}/shelves) + can_modify + rack nesting
# --------------------------------------------------------------------------------------------------
def _add_shelf_to_rack(db, rack, shelf):
    from app.models.organization import RackShelf

    db.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db.commit()


def test_list_work_shelves_only_see_able(client, db, make_user):
    reader = make_user("n-see-reader", role="reader")
    admin = make_user("n-see-admin", role="admin")
    open_s = _shelf(db, name="n-open", access_level="open")
    private_s = _shelf(db, name="n-private", access_level="private")
    work = _work(db, title="n-shared")
    _add_to_shelf(db, open_s, work)
    _add_to_shelf(db, private_s, work)
    # reader sees only the open shelf (the private one is hidden, even though the paper is on it)
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, reader))
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert names == {"n-open"}
    # admin bypasses and sees both
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, admin))
    assert {s["name"] for s in r.json()} == {"n-open", "n-private"}


def test_list_work_shelves_can_modify_open(client, db, make_user):
    librarian = make_user("n-mod-lib", role="librarian")
    editor = make_user("n-mod-ed", role="editor")
    reader = make_user("n-mod-reader", role="reader")
    open_s = _shelf(db, name="n-mod-open", access_level="open")
    work = _work(db, title="n-mod-work")
    _add_to_shelf(db, open_s, work)
    # open shelf: librarian may modify by role; editor/reader may not
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, librarian))
    assert r.json()[0]["can_modify"] is True
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, editor))
    assert r.json()[0]["can_modify"] is False
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, reader))
    assert r.json()[0]["can_modify"] is False


def test_list_work_shelves_can_modify_visible_needs_grant(client, db, make_user):
    librarian = make_user("n-vis-lib", role="librarian")
    admin = make_user("n-vis-admin", role="admin")
    visible_s = _shelf(db, name="n-vis", access_level="visible")
    work = _work(db, title="n-vis-work")
    _add_to_shelf(db, visible_s, work)
    # visible shelf: librarian without a grant cannot modify
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, librarian))
    assert r.json()[0]["can_modify"] is False
    # admin always can
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, admin))
    assert r.json()[0]["can_modify"] is True
    # a grant unlocks modify for the librarian
    _grant(db, librarian, "shelf", visible_s.id)
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, librarian))
    assert r.json()[0]["can_modify"] is True


def test_list_work_shelves_rack_nesting_see_filtered(client, db, make_user):
    librarian = make_user("n-rack-lib", role="librarian")
    admin = make_user("n-rack-admin", role="admin")
    shelf = _shelf(db, name="n-rack-shelf", access_level="open")
    open_rack = _rack(db, name="n-open-rack", access_level="open")
    private_rack = _rack(db, name="n-private-rack", access_level="private")
    work = _work(db, title="n-rack-work")
    _add_to_shelf(db, shelf, work)
    _add_shelf_to_rack(db, open_rack, shelf)
    _add_shelf_to_rack(db, private_rack, shelf)
    # librarian sees the shelf, but the private rack (no grant) is omitted from racks
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, librarian))
    racks = {x["name"] for x in r.json()[0]["racks"]}
    assert racks == {"n-open-rack"}
    # admin bypass sees both racks
    r = client.get(f"/api/v1/works/{work.id}/shelves", headers=_headers(db, admin))
    assert {x["name"] for x in r.json()[0]["racks"]} == {"n-open-rack", "n-private-rack"}


def test_list_work_shelves_404_hidden_and_missing(client, db, make_user):
    reader = make_user("n-404-reader", role="reader")
    private_s = _shelf(db, name="n-404-priv", access_level="private")
    hidden = _work(db, title="n-404-hidden")
    _add_to_shelf(db, private_s, hidden)
    # the work is on a private shelf only -> reader can't see it -> 404
    r = client.get(f"/api/v1/works/{hidden.id}/shelves", headers=_headers(db, reader))
    assert r.status_code == 404
    # a missing work id -> 404
    r = client.get(f"/api/v1/works/{uuid.uuid4()}/shelves", headers=_headers(db, reader))
    assert r.status_code == 404


def test_add_remove_work_to_shelf_acl(client, db, make_user):
    editor = make_user("n-acl-ed", role="editor")
    librarian = make_user("n-acl-lib", role="librarian")
    open_s = _shelf(db, name="n-acl-open", access_level="open")
    private_s = _shelf(db, name="n-acl-priv", access_level="private")
    work = _work(db, title="n-acl-work")
    _add_to_shelf(db, open_s, work)  # keep it see-able for everyone
    # editor (below librarian floor) is rejected by the require_librarian dep
    r = client.post(
        f"/api/v1/shelves/{open_s.id}/works",
        headers=_headers(db, editor),
        json={"work_id": str(work.id)},
    )
    assert r.status_code == 403
    # librarian may add on an open shelf
    r = client.post(
        f"/api/v1/shelves/{open_s.id}/works",
        headers=_headers(db, librarian),
        json={"work_id": str(work.id)},
    )
    assert r.status_code == 204
    # librarian WITHOUT a grant cannot add on a private shelf
    r = client.post(
        f"/api/v1/shelves/{private_s.id}/works",
        headers=_headers(db, librarian),
        json={"work_id": str(work.id)},
    )
    assert r.status_code == 403
    # with a grant, the add succeeds and the remove is ACL-gated the same way
    _grant(db, librarian, "shelf", private_s.id)
    r = client.post(
        f"/api/v1/shelves/{private_s.id}/works",
        headers=_headers(db, librarian),
        json={"work_id": str(work.id)},
    )
    assert r.status_code == 204
    r = client.delete(
        f"/api/v1/shelves/{private_s.id}/works/{work.id}", headers=_headers(db, librarian)
    )
    assert r.status_code == 204


def test_list_shelves_carries_can_modify(client, db, make_user):
    librarian = make_user("n-ls-lib", role="librarian")
    _shelf(db, name="n-ls-open", access_level="open")
    _shelf(db, name="n-ls-vis", access_level="visible")
    r = client.get("/api/v1/shelves", headers=_headers(db, librarian))
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()}
    # open -> librarian modifiable by role; visible -> needs a grant (not modifiable yet)
    assert by_name["n-ls-open"]["can_modify"] is True
    assert by_name["n-ls-vis"]["can_modify"] is False


# --- #1 default-shelf access propagation + #6 topic-graph visibility clamp ---


def test_default_shelf_access_level_propagates_to_new_papers(db, make_user):
    """A private default shelf makes auto-placed papers private (no more 'loose = open')."""
    from app.services.access_settings import set_default_access_level
    from app.services.default_shelf import get_default_shelf_id, place_on_default_if_loose

    set_default_access_level(db, access_level="private")
    work = _work(db, title="inbox-private")
    place_on_default_if_loose(db, work.id)
    db.commit()

    reader = make_user("dsp-reader", role="reader")
    admin = make_user("dsp-admin", role="admin")
    assert not access.can_see_work(db, reader, work)  # private default shelf hides it
    assert access.can_see_work(db, admin, work)
    # a grant on the default shelf unlocks it
    _grant(db, reader, "shelf", get_default_shelf_id(db))
    assert access.can_see_work(db, reader, work)


def test_topic_graph_clamps_to_visible_papers(db):
    """The topic graph must never surface papers outside the caller's visible set (#6)."""
    from app.services.topic_graph import build_topic_graph

    works = [_work(db, title=f"tg-{i}") for i in range(3)]
    db.commit()
    visible = {works[0].id, works[1].id}
    graph = build_topic_graph(db, scope_type="library", visible_ids=visible)
    node_ids = {uuid.UUID(n.id) for n in graph.nodes}
    assert node_ids == visible  # the third paper is excluded


def test_can_modify_shelf_precomputed_matches_per_query(db, make_user):
    """The list-optimized precomputed variant must agree with can_modify_shelf (audit #3b)."""
    librarian = make_user("cmp-lib", role="librarian")
    open_s = _shelf(db, name="cmp-open", access_level="open")
    private_s = _shelf(db, name="cmp-priv", access_level="private")
    granted = access.granted_target_ids(db, librarian, "shelf")
    for shelf in (open_s, private_s):
        assert access.can_modify_shelf_precomputed(
            librarian, shelf, granted_shelf_ids=granted
        ) == access.can_modify_shelf(db, librarian, shelf)
    # after a grant on the private shelf, both agree it's now modifiable
    _grant(db, librarian, "shelf", private_s.id)
    granted = access.granted_target_ids(db, librarian, "shelf")
    assert access.can_modify_shelf_precomputed(librarian, private_s, granted_shelf_ids=granted)
    assert access.can_modify_shelf(db, librarian, private_s)
