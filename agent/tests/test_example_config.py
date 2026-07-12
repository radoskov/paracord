"""Guard against ``config/agent.example.yaml`` drifting from the real ``AgentConfig`` schema.

The example file is documentation. Because pydantic ignores unknown keys by default, a stale key is
*silently dropped* rather than erroring — which is exactly the historical bug this test prevents:
the old example used a schema (`token_file`, `poll_interval_seconds`, `filesystem.allowed_roots`, …)
that the agent never read, so hand-editing it did nothing. These tests fail if the example contains
any key the model does not define (top level or per folder/file), or an invalid enum value, and they
confirm the declared scalar values actually round-trip into a loaded config (no silent drop).

The example is mounted read-only into the agent container at ``/app/config`` (see
``docker-compose.yml``); the finder walks up from this test file to locate it.
"""

from pathlib import Path

import pytest
import yaml

from paperracks_agent.config import (
    ACTIONS,
    POLICIES,
    AgentConfig,
    ManagedFile,
    ManagedFolder,
)

_FOLDER_MODES = ("monitored", "once")


def _find_example() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "agent.example.yaml"
        if candidate.exists():
            return candidate
    pytest.skip("config/agent.example.yaml not reachable from the test environment")


def _load() -> dict:
    data = yaml.safe_load(_find_example().read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), "agent.example.yaml must be a mapping"
    return data


def test_example_keys_are_all_real_agentconfig_fields():
    data = _load()

    unknown = set(data) - set(AgentConfig.model_fields)
    assert not unknown, f"agent.example.yaml has keys AgentConfig would ignore: {sorted(unknown)}"

    folder_fields = set(ManagedFolder.model_fields)
    for i, folder in enumerate(data.get("folders") or []):
        extra = set(folder) - folder_fields
        assert not extra, f"folders[{i}] has unknown keys: {sorted(extra)}"

    file_fields = set(ManagedFile.model_fields)
    for i, f in enumerate(data.get("files") or []):
        extra = set(f) - file_fields
        assert not extra, f"files[{i}] has unknown keys: {sorted(extra)}"


def test_example_constructs_and_scalar_values_round_trip():
    data = _load()
    config = AgentConfig(**data)  # validates types; would raise on a malformed example
    # Every declared scalar must actually appear on the loaded config — proves the key is real
    # (a silently-ignored key would leave the default in place instead).
    for key, value in data.items():
        if key in ("folders", "files"):
            continue
        assert getattr(config, key) == value, f"{key!r} was not applied from the example"


def test_example_enum_values_are_valid():
    data = _load()
    if "default_action" in data:
        assert data["default_action"] in ACTIONS
    if "default_teleport_policy" in data:
        assert data["default_teleport_policy"] in POLICIES
    for folder in data.get("folders") or []:
        assert folder.get("action", "index_only") in ACTIONS
        assert folder.get("teleport_policy", "ask") in POLICIES
        assert folder.get("mode", "monitored") in _FOLDER_MODES
    for f in data.get("files") or []:
        assert f.get("action", "index_only") in ACTIONS
        assert f.get("teleport_policy", "ask") in POLICIES
