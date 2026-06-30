"""Per-user UI preferences, persisted to a single YAML file.

This is intentionally a flat YAML file (NOT a DB table — no migration): the data is small, purely
cosmetic UI state (which library columns show, in what order, current sort), and the durable source
of truth the frontend reconciles its localStorage against.

File shape::

    version: 1
    users:
      <user-uuid>: {library_columns: {...}, ...}

Writes are atomic (tempfile in the same directory + ``os.replace``) so a crash mid-write can never
corrupt the file. A read-only filesystem surfaces as :class:`PreferencesUnavailable` so the API can
tell the user their change was "saved locally only".
"""

import contextlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings

_FILE_VERSION = 1


class PreferencesUnavailable(RuntimeError):
    """The preferences store could not be written (e.g. read-only filesystem)."""


def _preferences_path() -> Path:
    """Resolved preferences file path (``~`` expanded)."""
    return Path(get_settings().preferences_path).expanduser()


def _load_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _FILE_VERSION, "users": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        # A missing/corrupt/unreadable file degrades to empty rather than 500ing every read.
        return {"version": _FILE_VERSION, "users": {}}
    if not isinstance(data, dict):
        return {"version": _FILE_VERSION, "users": {}}
    users = data.get("users")
    if not isinstance(users, dict):
        data["users"] = {}
    data.setdefault("version", _FILE_VERSION)
    return data


def read_preferences(user_id: uuid.UUID) -> dict[str, Any]:
    """Return the stored preferences blob for ``user_id`` ( ``{}`` if none)."""
    data = _load_file(_preferences_path())
    blob = data.get("users", {}).get(str(user_id))
    return blob if isinstance(blob, dict) else {}


def write_preferences(user_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace ``user_id``'s preferences slice and persist atomically. Returns the stored blob.

    Raises :class:`PreferencesUnavailable` if the file cannot be written (read-only FS, etc.).
    """
    path = _preferences_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _load_file(path)
        # Only this user's slice is replaced; other users' blobs are preserved.
        data.setdefault("users", {})[str(user_id)] = payload
        # Atomic write: serialize to a temp file in the same directory, fsync, then os.replace.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".preferences-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            # Don't leave a stray temp file behind on failure.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as exc:
        raise PreferencesUnavailable(str(exc)) from exc
    return payload
