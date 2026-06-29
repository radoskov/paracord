"""Local file index for the agent.

The index is the agent's source of truth: it maps opaque ``local_file_id`` values (content
hashes) to on-disk paths within the configured roots. The server only ever refers to files by
``local_file_id`` — it never sends a path — and the agent will only act on ids it has itself
indexed, so a malicious/compromised server cannot coax the agent into reading arbitrary files.
"""

from pathlib import Path

from paperracks_agent.manifest import ManifestItem, build_manifest_item
from paperracks_agent.security import is_path_within_roots
from paperracks_agent.watcher import iter_pdf_files


class AgentIndex:
    """An in-memory index of indexed PDFs keyed by ``local_file_id``."""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = [root.expanduser().resolve(strict=False) for root in roots]
        self._items: dict[str, ManifestItem] = {}

    def scan(self) -> "AgentIndex":
        """(Re)build the index from the configured roots."""
        self._items = {}
        for path in iter_pdf_files(self._roots):
            item = build_manifest_item(path)
            self._items[item.local_file_id] = item
        return self

    def items(self) -> list[ManifestItem]:
        return list(self._items.values())

    def resolve_path(self, local_file_id: str) -> Path:
        """Return the path for an indexed id, or raise ``KeyError``.

        Rejects ids the agent has not indexed and re-checks the resolved path is still inside a
        configured root (defense in depth against symlink/TOCTOU drift).
        """
        item = self._items.get(local_file_id)
        if item is None:
            raise KeyError(local_file_id)
        if not is_path_within_roots(item.path, self._roots):
            raise KeyError(local_file_id)
        return item.path

    def manifest_payload(self) -> dict:
        """Build the server manifest body (opaque identity only — no server-usable paths)."""
        return {
            "items": [
                {
                    "local_file_id": item.local_file_id,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                    "display_path": item.display_path,
                    "mime_type": item.mime_type,
                }
                for item in self._items.values()
            ]
        }
