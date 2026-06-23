"""Folder scanner and future watcher implementation."""

from collections.abc import Iterator
from pathlib import Path

from paperracks_agent.security import is_path_within_roots


def iter_pdf_files(roots: list[Path]) -> Iterator[Path]:
    """Yield PDF files from configured roots."""
    canonical_roots = [root.expanduser().resolve(strict=False) for root in roots]
    for root in canonical_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.pdf"):
            if path.is_file() and is_path_within_roots(path, canonical_roots):
                yield path
