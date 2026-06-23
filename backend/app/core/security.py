"""Authentication and authorization helpers.

Implementation notes:
- Do not add a guest role.
- Password reset must be handled by server-console script, not by an unauthenticated web endpoint.
"""

from enum import StrEnum
from typing import Iterable

from passlib.context import CryptContext


password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
    """Hash a password for storage."""
    if not password:
        raise ValueError("Password must not be empty")
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a stored hash."""
    if not password or not password_hash:
        return False
    return password_context.verify(password, password_hash)
