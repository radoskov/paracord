"""CLI parity tests for the Batch-A subcommands (reconcile / prune-unwatched / forget)."""

from __future__ import annotations

import sys
from pathlib import Path

from paperracks_agent import cli
from paperracks_agent.config import AgentConfig, ManagedFolder, save_config
from paperracks_agent.state import AgentState


class _FakeClient:
    """Minimal stand-in for the server client (reconcile only needs get_my_files)."""

    def __init__(self, *args, my_files=None, **kwargs) -> None:
        self.server_url = "http://test"
        self._my_files = my_files or []

    async def get_my_files(self) -> list[dict]:
        return self._my_files


def _watched_config_and_state(tmp_path: Path):
    folder = tmp_path / "papers"
    folder.mkdir()
    inside = folder / "a.pdf"
    inside.write_bytes(b"%PDF-1.4")
    config_path = tmp_path / "agent.yaml"
    save_config(AgentConfig(folders=[ManagedFolder(path=folder)]), config_path)
    state_path = tmp_path / "state.sqlite3"
    return folder, inside, config_path, state_path


def test_cli_forget_bulk(tmp_path, capsys) -> None:
    state_path = tmp_path / "state.sqlite3"
    state = AgentState(state_path)
    for i in (1, 2):
        state.upsert(local_file_id=f"h{i}", real_path=f"/p/{i}.pdf", sha256=f"h{i}", size_bytes=1)
    state.close()
    sys.argv = ["paracord-agent", "forget", "h1", "h2", "--state", str(state_path)]
    cli.main()
    assert "Forgot 2" in capsys.readouterr().out
    assert AgentState(state_path).all_files() == []


def test_cli_prune_unwatched(tmp_path, capsys) -> None:
    _folder, _inside, config_path, state_path = _watched_config_and_state(tmp_path)
    state = AgentState(state_path)
    loose = tmp_path / "loose.pdf"
    loose.write_bytes(b"%PDF-1.4")
    state.upsert(local_file_id="out", real_path=str(loose), sha256="e", size_bytes=1)
    state.close()

    sys.argv = [
        "paracord-agent",
        "prune-unwatched",
        "--dry-run",
        "--config",
        str(config_path),
        "--state",
        str(state_path),
    ]
    cli.main()
    assert "1 unwatched" in capsys.readouterr().out
    assert AgentState(state_path).all_files()  # dry-run changed nothing

    sys.argv = [
        "paracord-agent",
        "prune-unwatched",
        "--config",
        str(config_path),
        "--state",
        str(state_path),
    ]
    cli.main()
    assert "Pruned 1" in capsys.readouterr().out
    assert AgentState(state_path).all_files() == []


def test_cli_reconcile_dry_run(tmp_path, capsys, monkeypatch) -> None:
    folder, inside, config_path, state_path = _watched_config_and_state(tmp_path)
    state = AgentState(state_path)
    state.upsert(local_file_id="known", real_path=str(inside), sha256="d", size_bytes=1)
    state.set_processing_state("known", "teleported")  # server-known
    state.close()
    monkeypatch.setattr(cli, "PaRacORDServerClient", lambda *a, **k: _FakeClient(my_files=[]))

    sys.argv = [
        "paracord-agent",
        "reconcile",
        "--config",
        str(config_path),
        "--state",
        str(state_path),
    ]
    cli.main()
    out = capsys.readouterr().out
    assert "1 to un-index" in out and "Re-run with --apply" in out
    assert AgentState(state_path).all_files()  # dry-run applied nothing


def test_cli_reconcile_delete_needs_confirm(tmp_path, capsys, monkeypatch) -> None:
    """--delete-on-disk without --confirm-delete is refused (not armed)."""
    folder, inside, config_path, state_path = _watched_config_and_state(tmp_path)
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    state = AgentState(state_path)
    state.upsert(local_file_id="known", real_path=str(inside), sha256="d", size_bytes=1)
    state.set_processing_state("known", "teleported")
    state.close()
    monkeypatch.setattr(cli, "PaRacORDServerClient", lambda *a, **k: _FakeClient(my_files=[]))

    sys.argv = [
        "paracord-agent",
        "reconcile",
        "--apply",
        "--delete-on-disk",
        "--config",
        str(config_path),
        "--state",
        str(state_path),
    ]
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 2
    assert inside.exists()  # nothing deleted
    assert AgentState(state_path).all_files()  # nothing un-indexed on a refused delete run
