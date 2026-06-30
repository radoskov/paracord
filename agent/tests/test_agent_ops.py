"""Agent operations tests (§32 S4) with a fake server client."""

from __future__ import annotations

import asyncio
from pathlib import Path

from paperracks_agent import agent_ops
from paperracks_agent.config import AgentConfig, ManagedFolder
from paperracks_agent.state import AgentState


class FakeClient:
    """Records calls instead of hitting a server."""

    def __init__(self, pending=None, my_files=None) -> None:
        self.server_url = "http://test"
        self.manifests: list[dict] = []
        self.extracted: list[str] = []
        self.teleported: list[str] = []
        self.rejected: list[str] = []
        self.removed: list[str] = []
        self._pending = pending or []
        self._my_files = my_files or []

    async def send_manifest(self, payload: dict) -> None:
        self.manifests.append(payload)

    async def get_my_files(self) -> list[dict]:
        return self._my_files

    async def report_source_removed(self, ids: list[str]) -> dict:
        self.removed.extend(ids)
        return {"marked": len(ids)}

    async def get_pending_teleports(self) -> list[dict]:
        return self._pending

    async def upload_for_extraction(self, local_file_id: str, handle) -> dict:
        self.extracted.append(local_file_id)
        return {"status": "extracting"}

    async def upload_teleport_content(self, local_file_id: str, handle) -> dict:
        self.teleported.append(local_file_id)
        return {"status": "complete"}

    async def reject_teleport(self, local_file_id: str, forever: bool = False) -> dict:
        self.rejected.append(local_file_id)
        return {"status": "rejected"}


def _folder(tmp_path: Path, action: str, policy: str = "ask") -> AgentConfig:
    root = tmp_path / "papers"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4\nA")
    return AgentConfig(folders=[ManagedFolder(path=root, action=action, teleport_policy=policy)])


def test_sync_index_only_sends_manifest_no_bytes(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    summary = asyncio.run(agent_ops.sync(config, state, client))
    assert summary["indexed"] == 1
    assert len(client.manifests) == 1
    assert client.manifests[0]["items"][0]["import_action"] == "index_only"
    assert client.extracted == [] and client.teleported == []


def test_sync_extract_action_uploads_for_extraction(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_and_extract")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))
    assert len(client.extracted) == 1
    assert client.teleported == []


def test_sync_teleport_action_pushes_bytes(tmp_path: Path) -> None:
    config = _folder(tmp_path, "teleport")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))
    assert len(client.teleported) == 1


def test_sync_auto_fulfils_allow_requests(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only", policy="allow")
    state = AgentState(tmp_path / "state.sqlite3")
    # First sync indexes the file so the state knows its id + policy + path.
    asyncio.run(agent_ops.sync(config, AgentState(tmp_path / "state.sqlite3"), FakeClient()))
    state = AgentState(tmp_path / "state.sqlite3")
    local_id = state.all_files()[0].local_file_id
    client = FakeClient(pending=[{"local_file_id": local_id}])
    summary = asyncio.run(agent_ops.sync(config, state, client))
    assert local_id in client.teleported
    assert summary["requests_fulfilled"] == 1


def test_blocked_request_is_rejected(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only", policy="allow")
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    local_id = state.all_files()[0].local_file_id
    state.set_blocked(local_id, True)
    client = FakeClient(pending=[{"local_file_id": local_id}])
    asyncio.run(agent_ops.fulfil_requests(config, state, client))
    assert client.rejected == [local_id]
    assert client.teleported == []


def test_scan_reuses_cached_hash_for_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    """An unchanged file is not re-hashed on rescan (E7 incremental scan)."""
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))  # first scan: hashes + caches mtime

    calls = {"n": 0}
    real_hash = agent_ops.build_manifest_item.__globals__["hash_file"]

    def counting_hash(path, chunk_size=1024 * 1024):
        calls["n"] += 1
        return real_hash(path, chunk_size)

    monkeypatch.setattr("paperracks_agent.manifest.hash_file", counting_hash)
    # Rescan with the same (unchanged) file → cache hit, no hashing.
    agent_ops.scan_managed(config, state)
    assert calls["n"] == 0

    # Modify the file (content + mtime change) → it is re-hashed.
    pdf = tmp_path / "papers" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCHANGED CONTENT")
    import os

    os.utime(pdf, (pdf.stat().st_atime, pdf.stat().st_mtime + 10))
    agent_ops.scan_managed(config, state)
    assert calls["n"] == 1
