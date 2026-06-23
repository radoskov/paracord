"""Filesystem security helpers for the local agent."""

from pathlib import Path


def canonicalize_allowed_root(root: Path) -> Path:
    """Return a canonical allowed root path."""
    return root.expanduser().resolve(strict=False)


def is_path_within_roots(path: Path, roots: list[Path]) -> bool:
    """Return whether a path is inside one of the configured roots."""
    resolved = path.expanduser().resolve(strict=False)
    for root in roots:
        root_resolved = canonicalize_allowed_root(root)
        try:
            resolved.relative_to(root_resolved)
            return True
        except ValueError:
            continue
    return False
