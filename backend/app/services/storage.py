"""Managed library storage service."""

from pathlib import Path


def content_addressed_path(root: Path, sha256: str) -> Path:
    """Return managed-library path for a SHA-256 digest."""
    if len(sha256) != 64:
        raise ValueError("Expected a 64-character SHA-256 digest")
    return root / sha256[:2] / sha256[2:4] / f"{sha256}.pdf"
