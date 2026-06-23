"""Agent filesystem security tests."""

from pathlib import Path

from paperracks_agent.security import is_path_within_roots


def test_path_inside_root() -> None:
    assert is_path_within_roots(Path("/tmp/example/a.pdf"), [Path("/tmp/example")])


def test_path_outside_root() -> None:
    assert not is_path_within_roots(Path("/tmp/other/a.pdf"), [Path("/tmp/example")])
