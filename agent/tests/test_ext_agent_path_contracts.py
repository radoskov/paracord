"""Additional local-agent path-boundary contract tests."""

from __future__ import annotations

from paperracks_agent.security import is_path_within_roots


def test_agent_accepts_normalized_path_inside_allowed_root(tmp_path) -> None:
    root = tmp_path / "papers"
    topic = root / "topic"
    topic.mkdir(parents=True)
    (topic / "paper.pdf").write_text("%PDF-1.4\n", encoding="utf-8")

    # The same file reached via a path that carries a '..' segment; it must normalize back
    # inside the allowed root and be accepted. (mkdir() would not normalize '..', so the real
    # directory is created above and only the assertion uses the un-normalized form.)
    nested = root / "topic" / ".." / "topic" / "paper.pdf"
    assert is_path_within_roots(nested, [root])


def test_agent_rejects_sibling_prefix_collision(tmp_path) -> None:
    root = tmp_path / "papers"
    sibling = tmp_path / "papers-secret" / "paper.pdf"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("%PDF-1.4\n", encoding="utf-8")

    assert not is_path_within_roots(sibling, [root])


def test_agent_accepts_exact_allowed_root_path(tmp_path) -> None:
    root = tmp_path / "papers"
    root.mkdir()

    assert is_path_within_roots(root, [root])
