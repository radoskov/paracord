"""Safety-marked local-agent path-escape probes."""

from __future__ import annotations

import pytest
from paperracks_agent.security import is_path_within_roots

pytestmark = pytest.mark.safety


def test_agent_rejects_symlink_escape_from_allowed_root(tmp_path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.pdf"
    target.write_text("%PDF-1.4\n", encoding="utf-8")
    symlink = root / "link.pdf"
    symlink.symlink_to(target)

    assert not is_path_within_roots(symlink, [root])


def test_agent_rejects_parent_directory_escape(tmp_path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_text("%PDF-1.4\n", encoding="utf-8")

    escaped = root / ".." / "outside.pdf"

    assert not is_path_within_roots(escaped, [root])
