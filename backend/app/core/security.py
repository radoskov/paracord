"""Authentication and authorization helpers.

Implementation notes:
- Do not add a guest role.
- Password reset must be handled by server-console script, not by an unauthenticated web endpoint.
"""

from enum import StrEnum
from typing import Iterable

import bcrypt

# bcrypt's algorithm only considers the first 72 bytes of the password.
_BCRYPT_MAX_BYTES = 72


class Role(StrEnum):
    """Allowed authenticated roles."""

    OWNER = "owner"
    EDITOR = "editor"
    READER = "reader"


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
