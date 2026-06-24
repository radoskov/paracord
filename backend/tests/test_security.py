"""Backend security helper tests."""

import pytest
from app.core.security import assert_no_guest_roles, hash_password, verify_password


def test_password_hash_round_trip() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong password", password_hash)


def test_empty_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_guest_roles_are_rejected() -> None:
    with pytest.raises(ValueError):
        assert_no_guest_roles(["owner", "guest"])
