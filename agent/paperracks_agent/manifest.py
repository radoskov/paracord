"""File manifest generation."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class ManifestItem:
    """One indexed file known to the local agent."""

    local_file_id: str
    path: Path
    sha256: str
    size_bytes: int
    display_path: str = ""
    mime_type: str = "application/pdf"


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 for a file."""
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest_item(path: Path) -> ManifestItem:
    """Build a manifest item for one PDF.

    ``local_file_id`` is the content hash, so it is stable across rescans and exposes no
    filesystem path to the server. ``display_path`` is the file name only — a human label,
    never a server-usable path.
    """
    file_hash = hash_file(path)
    return ManifestItem(
        local_file_id=file_hash,
        path=path,
        sha256=file_hash,
        size_bytes=path.stat().st_size,
        display_path=path.name,
        mime_type="application/pdf",
    )
