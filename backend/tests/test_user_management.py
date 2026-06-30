"""Role-authorization dependency and user-management service tests."""

import uuid
from pathlib import Path

import pytest
from app.api.deps import require_admin, require_owner, require_roles
from app.core.security import Role, hash_password, verify_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.user import User
from app.services import users as user_service
from app.services.users import PermissionError403
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, AuditEvent.__table__])
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _add_user(db_session, username: str, role: str, *, is_bootstrap: bool = False) -> User:
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role=role,
        is_bootstrap=is_bootstrap,
    )
    db_session.add(user)
    db_session.commit()
    return user


# --- authorization dependencies ---------------------------------------------


def test_require_owner_allows_owner_and_rejects_others(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    admin = _add_user(db_session, "admin", "admin")
    reader = _add_user(db_session, "reader", "reader")

    assert require_owner(user=owner) is owner

    # An admin is NOT an owner — owner-exclusive gate rejects them.
    with pytest.raises(HTTPException) as exc_info:
        require_owner(user=admin)
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        require_owner(user=reader)
    assert exc_info.value.status_code == 403


def test_require_admin_allows_owner_and_admin_rejects_others(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    admin = _add_user(db_session, "admin", "admin")
    editor = _add_user(db_session, "editor", "editor")

    assert require_admin(user=owner) is owner
    assert require_admin(user=admin) is admin

    with pytest.raises(HTTPException) as exc_info:
        require_admin(user=editor)
    assert exc_info.value.status_code == 403


def test_require_roles_checks_membership(db_session) -> None:
    editor = _add_user(db_session, "editor", "editor")
    reader = _add_user(db_session, "reader", "reader")
    editor_or_owner = require_roles(Role.OWNER, Role.EDITOR)

    assert editor_or_owner(user=editor) is editor
    with pytest.raises(HTTPException) as exc_info:
        editor_or_owner(user=reader)
    assert exc_info.value.status_code == 403


# --- user-management service ------------------------------------------------


def test_create_user_persists_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)

    user = user_service.create_user(
        db_session, username="alice", password="pw", role=Role.EDITOR, actor=owner
    )
    db_session.commit()

    assert user.role == "editor"
    assert verify_password("pw", user.password_hash)
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.created")
    ).all()
    assert any(e.entity_id == str(user.id) for e in events)


def test_create_user_rejects_duplicate_and_bad_role(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    user_service.create_user(
        db_session, username="alice", password="pw", role=Role.READER, actor=owner
    )
    db_session.commit()

    with pytest.raises(ValueError, match="already exists"):
        user_service.create_user(
            db_session, username="alice", password="pw", role=Role.READER, actor=owner
        )
    with pytest.raises(ValueError, match="Unknown role"):
        user_service.create_user(
            db_session, username="bob", password="pw", role="superuser", actor=owner
        )


def test_owner_role_can_never_be_created(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    with pytest.raises(ValueError, match="owner role cannot be assigned"):
        user_service.create_user(
            db_session, username="second", password="pw", role=Role.OWNER, actor=owner
        )


def test_only_owner_can_create_admin(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    admin = _add_user(db_session, "admin", "admin")

    # Owner can create an admin.
    created = user_service.create_user(
        db_session, username="admin2", password="pw", role=Role.ADMIN, actor=owner
    )
    db_session.commit()
    assert created.role == "admin"

    # A (non-owner) admin cannot create another admin.
    with pytest.raises(PermissionError403, match="owner can create administrator"):
        user_service.create_user(
            db_session, username="admin3", password="pw", role=Role.ADMIN, actor=admin
        )


def test_set_user_role_changes_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    alice = _add_user(db_session, "alice", "reader")

    user_service.set_user_role(db_session, user_id=alice.id, role=Role.EDITOR, actor=owner)
    db_session.commit()

    assert db_session.get(User, alice.id).role == "editor"
    changed = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.role_changed")
    ).all()
    assert changed and changed[0].details["to"] == "editor"


def test_owner_cannot_be_role_changed_disabled_or_deleted(db_session) -> None:
    # An admin (or any other actor) can never touch the owner. The bootstrap owner and any
    # stray owner-role account are both protected by the owner guard.
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    other_owner = _add_user(db_session, "second", "owner")
    admin = _add_user(db_session, "admin", "admin")

    for target in (owner, other_owner):
        with pytest.raises(PermissionError403, match="owner account cannot be modified"):
            user_service.set_user_role(db_session, user_id=target.id, role=Role.READER, actor=admin)
        with pytest.raises(PermissionError403, match="owner account cannot be modified"):
            user_service.disable_user(db_session, user_id=target.id, actor=admin)
        with pytest.raises(PermissionError403, match="owner account cannot be modified"):
            user_service.reset_user_password(
                db_session,
                user_id=target.id,
                new_password="long-enough-pw",  # pragma: allowlist secret
                actor=admin,
            )


def test_no_self_disable_or_self_delete(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    admin = _add_user(db_session, "admin", "admin")

    # No account — owner included — may disable or delete itself (prevents self-lockout).
    with pytest.raises(ValueError, match="cannot disable your own account"):
        user_service.disable_user(db_session, user_id=owner.id, actor=owner)
    with pytest.raises(ValueError, match="cannot disable your own account"):
        user_service.disable_user(db_session, user_id=admin.id, actor=admin)
    with pytest.raises(ValueError, match="cannot delete your own account"):
        user_service.delete_user(db_session, user_id=admin.id, actor=admin)


def test_admin_cannot_manage_other_admin(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    admin_a = _add_user(db_session, "admin-a", "admin")
    admin_b = _add_user(db_session, "admin-b", "admin")

    # Admin A cannot disable / role-change / reset / delete admin B.
    with pytest.raises(PermissionError403, match="owner can manage administrator"):
        user_service.disable_user(db_session, user_id=admin_b.id, actor=admin_a)
    with pytest.raises(PermissionError403, match="owner can manage administrator"):
        user_service.set_user_role(db_session, user_id=admin_b.id, role=Role.READER, actor=admin_a)
    with pytest.raises(PermissionError403, match="owner can manage administrator"):
        user_service.reset_user_password(
            db_session,
            user_id=admin_b.id,
            new_password="long-enough-pw",  # pragma: allowlist secret
            actor=admin_a,
        )

    # But the owner can manage admin B.
    user_service.set_user_role(db_session, user_id=admin_b.id, role=Role.READER, actor=owner)
    db_session.commit()
    assert db_session.get(User, admin_b.id).role == "reader"
    # admin_a is untouched.
    assert db_session.get(User, admin_a.id).role == "admin"


def test_admin_cannot_promote_to_admin(db_session) -> None:
    admin = _add_user(db_session, "admin", "admin")
    reader = _add_user(db_session, "reader", "reader")

    with pytest.raises(PermissionError403, match="owner can grant the administrator"):
        user_service.set_user_role(db_session, user_id=reader.id, role=Role.ADMIN, actor=admin)


def test_admin_can_manage_readers_and_editors(db_session) -> None:
    admin = _add_user(db_session, "admin", "admin")
    reader = _add_user(db_session, "reader", "reader")

    user_service.set_user_role(db_session, user_id=reader.id, role=Role.EDITOR, actor=admin)
    db_session.commit()
    assert db_session.get(User, reader.id).role == "editor"

    user_service.disable_user(db_session, user_id=reader.id, actor=admin)
    db_session.commit()
    assert db_session.get(User, reader.id).disabled_at is not None


def test_disable_user_sets_timestamp_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    alice = _add_user(db_session, "alice", "reader")

    user_service.disable_user(db_session, user_id=alice.id, actor=owner)
    db_session.commit()

    assert db_session.get(User, alice.id).disabled_at is not None
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.disabled")
    ).all()


def test_enable_user_clears_timestamp_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    alice = _add_user(db_session, "alice", "reader")

    user_service.disable_user(db_session, user_id=alice.id, actor=owner)
    db_session.commit()
    user_service.enable_user(db_session, user_id=alice.id, actor=owner)
    db_session.commit()

    assert db_session.get(User, alice.id).disabled_at is None
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.enabled")
    ).all()


def test_set_role_unknown_user_raises_lookup(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner", is_bootstrap=True)
    with pytest.raises(LookupError):
        user_service.set_user_role(db_session, user_id=uuid.uuid4(), role=Role.READER, actor=owner)


# --- migration intent: downgrade non-bootstrap owners to admin (batch 2 #20) --------------------
#
# The 0024 migration runs raw SQL. These tests exercise that exact selection + downgrade logic
# against the session so the data-migration intent is covered without a live Postgres.

from datetime import UTC, datetime, timedelta  # noqa: E402

from sqlalchemy import text  # noqa: E402

_MIGRATION_SELECT = text(
    "SELECT id FROM users WHERE role = 'owner' "
    "ORDER BY is_bootstrap DESC, created_at ASC, id ASC LIMIT 1"
)


def _norm(value: object) -> str:
    """Normalise an id (UUID or SQLite hex string) for comparison."""
    return str(value).replace("-", "")


def _run_downgrade(db_session) -> object:
    """Replicate the 0024 data migration: pick the surviving owner, flag it, downgrade the rest."""
    surviving = db_session.execute(_MIGRATION_SELECT).scalar()
    if surviving is not None:
        db_session.execute(
            text("UPDATE users SET is_bootstrap = 1 WHERE id = :id"), {"id": surviving}
        )
        db_session.execute(
            text("UPDATE users SET role = 'admin' WHERE role = 'owner' AND id <> :id"),
            {"id": surviving},
        )
        db_session.commit()
    return surviving


def test_migration_keeps_flagged_bootstrap_owner_and_downgrades_others(db_session) -> None:
    # The flagged bootstrap owner survives regardless of creation order.
    early = _add_user(db_session, "early", "owner")
    boot = _add_user(db_session, "boot", "owner", is_bootstrap=True)
    late = _add_user(db_session, "late", "owner")
    editor = _add_user(db_session, "ed", "editor")

    surviving = _run_downgrade(db_session)
    assert _norm(surviving) == _norm(boot.id)

    db_session.expire_all()
    assert db_session.get(User, boot.id).role == "owner"
    assert db_session.get(User, boot.id).is_bootstrap is True
    assert db_session.get(User, early.id).role == "admin"
    assert db_session.get(User, late.id).role == "admin"
    # Non-owners are untouched.
    assert db_session.get(User, editor.id).role == "editor"


def test_migration_picks_earliest_owner_when_none_flagged(db_session) -> None:
    now = datetime.now(UTC)
    first = _add_user(db_session, "first", "owner")
    first.created_at = now - timedelta(days=2)
    second = _add_user(db_session, "second", "owner")
    second.created_at = now - timedelta(days=1)
    db_session.commit()

    surviving = _run_downgrade(db_session)
    assert _norm(surviving) == _norm(first.id)

    db_session.expire_all()
    assert db_session.get(User, first.id).role == "owner"
    assert db_session.get(User, first.id).is_bootstrap is True
    assert db_session.get(User, second.id).role == "admin"
