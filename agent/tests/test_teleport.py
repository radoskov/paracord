"""Agent index + teleport resolution tests (M5 security boundary)."""

from __future__ import annotations

from pathlib import Path

import pytest
from paperracks_agent.index import AgentIndex
from paperracks_agent.teleport import open_file_for_teleport


def test_index_scans_and_builds_manifest_payload(tmp_path: Path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4\nA")
    index = AgentIndex([root]).scan()

    payload = index.manifest_payload()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["sha256"] and item["local_file_id"] == item["sha256"]
    assert item["display_path"] == "a.pdf"  # name only, never an absolute path
    assert "/" not in item["display_path"]


def test_teleport_resolves_known_local_file_id(tmp_path: Path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    pdf = root / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nA")
    index = AgentIndex([root]).scan()
    local_file_id = index.items()[0].local_file_id

    with open_file_for_teleport(index, local_file_id) as handle:
        assert handle.read(4) == b"%PDF"


def test_teleport_rejects_unknown_local_file_id(tmp_path: Path) -> None:
    index = AgentIndex([tmp_path]).scan()
    # The server cannot coax the agent into opening a file it never indexed.
    with pytest.raises(KeyError):
        open_file_for_teleport(index, "deadbeef")
