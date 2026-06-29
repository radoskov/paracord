"""Additional local-agent filesystem security tests."""

from __future__ import annotations

from pathlib import Path

from paperracks_agent.security import is_path_within_roots


def test_prefix_collision_is_not_treated_as_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "papers"
    sibling = tmp_path / "papers-secret"
    root.mkdir()
    sibling.mkdir()
    candidate = sibling / "paper.pdf"
    candidate.write_bytes(b"%PDF-1.4\n")

    assert not is_path_within_roots(candidate, [root])


def test_symlink_escape_is_rejected_when_target_exists(tmp_path: Path) -> None:
    root = tmp_path / "papers"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4\n")
    link = root / "linked-secret.pdf"
    link.symlink_to(secret)

    assert not is_path_within_roots(link, [root])


def test_nested_normalized_path_inside_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "papers"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    candidate = nested / ".." / "b" / "paper.pdf"
    candidate.write_bytes(b"%PDF-1.4\n")

    assert is_path_within_roots(candidate, [root])
