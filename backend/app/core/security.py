"""Authentication and authorization helpers.

Implementation notes:
- Do not add a guest role.
- Password reset must be handled by server-console script, not by an unauthenticated web endpoint.
"""

from collections.abc import Iterable
from enum import StrEnum

import bcrypt

# bcrypt's algorithm only considers the first 72 bytes of the password.
_BCRYPT_MAX_BYTES = 72


class Role(StrEnum):
    """Allowed authenticated roles, highest privilege first.

    Privilege ladder (linear): ``owner`` > ``admin`` > ``librarian`` > ``editor`` >
    ``contributor`` > ``reader``.

    - ``reader``: read accessible content only.
    - ``contributor``: reader + create/edit/delete **own** papers (``Work.created_by_user_id`` ==
      self); no rack/shelf structural changes.
    - ``editor``: contributor + create/edit/delete **any accessible** paper; still no rack/shelf
      structural changes.
    - ``librarian``: editor + create/edit/delete racks & shelves and organize papers within them
      (subject to the rack/shelf grant matrix — "not even a librarian without a grant" for
      visible/private targets).
    - ``admin``: full administration (users/groups/grants/defaults/agents/AI settings/audit log)
      and bypasses all content ACLs, EXCEPT creating, disabling, deleting or role-changing another
      ``admin`` or the ``owner``.
    - ``owner``: single, immutable bootstrap account (``make bootstrap-admin``). It can never be
      disabled, deleted, role-changed, or disable itself, and is the only account that may manage
      ``admin`` accounts. There is exactly one owner. Bypasses all content ACLs.
    """

    OWNER = "owner"
    ADMIN = "admin"
    LIBRARIAN = "librarian"
    EDITOR = "editor"
    CONTRIBUTOR = "contributor"
    READER = "reader"


# Linear privilege ladder used for ">= role" comparisons (deps + the access-control layer).
# Higher number = more privilege. An unknown/legacy role value ranks below ``reader`` (0).
_ROLE_RANK: dict[str, int] = {
    Role.READER: 0,
    Role.CONTRIBUTOR: 1,
    Role.EDITOR: 2,
    Role.LIBRARIAN: 3,
    Role.ADMIN: 4,
    Role.OWNER: 5,
}


def role_rank(role: str) -> int:
    """Return the ladder rank of a role string (unknown roles rank below ``reader``)."""
    return _ROLE_RANK.get(str(role), -1)


def role_at_least(role: str, minimum: Role) -> bool:
    """True if ``role`` is at least as privileged as ``minimum`` on the linear ladder."""
    return role_rank(role) >= role_rank(minimum)


def assert_no_guest_roles(roles: Iterable[str]) -> None:
    """Raise if a forbidden guest/anonymous role is present."""
    forbidden = {"guest", "anonymous", "anon"}
    found = forbidden.intersection({role.lower() for role in roles})
    if found:
        raise ValueError(f"Forbidden unauthenticated roles configured: {sorted(found)}")


def hash_password(password: str) -> str:
    """Hash a password for storage using bcrypt.

    Uses the maintained ``bcrypt`` library directly (passlib is unmaintained and
    incompatible with modern bcrypt releases).
    """
    if not password:
        raise ValueError("Password must not be empty")
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError("Password must not exceed 72 bytes")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a stored bcrypt hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
