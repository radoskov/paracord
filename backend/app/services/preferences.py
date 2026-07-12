"""Per-user UI preferences, persisted as one YAML file **per user** (S11).

This is intentionally file-based (NOT a DB table — no migration): the data is small, purely
cosmetic UI state (which library columns show, in what order, current sort), and the durable source
of truth the frontend reconciles its localStorage against.

Layout: ``<preferences_path>``'s directory gains a ``preferences.d/`` folder holding one
``<user-uuid>.yaml`` per user (shape: ``{version: 1, preferences: {...}}``). One file per user
means two users saving at the same moment (multi-process gunicorn) touch different files — the
lost-update race of the old single shared file is gone by construction. Same-user concurrent
writes remain last-writer-wins on that user's own file, which is the right semantic for "my two
tabs both saved".

The legacy single file (``preferences_path`` itself, shape ``{users: {<uuid>: {...}}}``) is still
**read** as a fallback for users who have not written since the split, so existing deployments
migrate lazily with no data move. Writes always go to the per-user file.

Writes are atomic (tempfile in the same directory + ``os.replace``) so a crash mid-write can never
corrupt a file. A read-only filesystem surfaces as :class:`PreferencesUnavailable` so the API can
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
_USER_DIR_NAME = "preferences.d"


class PreferencesUnavailable(RuntimeError):
    """The preferences store could not be written (e.g. read-only filesystem)."""


def _preferences_path() -> Path:
    """Resolved legacy single-file path (``~`` expanded) — read-only fallback."""
    return Path(get_settings().preferences_path).expanduser()


def _user_file(user_id: uuid.UUID) -> Path:
    """The per-user preferences file (uuid-typed id, so the name is always filesystem-safe)."""
    return _preferences_path().parent / _USER_DIR_NAME / f"{user_id}.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Best-effort YAML load: a missing/corrupt/unreadable file degrades to ``{}``."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_slice(user_id: uuid.UUID) -> dict[str, Any]:
    """This user's blob from the pre-S11 shared file (lazy-migration read path)."""
    users = _load_yaml(_preferences_path()).get("users")
    if not isinstance(users, dict):
        return {}
    blob = users.get(str(user_id))
    return blob if isinstance(blob, dict) else {}


def read_preferences(user_id: uuid.UUID) -> dict[str, Any]:
    """Return the stored preferences blob for ``user_id`` (``{}`` if none).

    The per-user file wins; the legacy shared file is only consulted when the user has never
    written since the per-user split.
    """
    per_user = _load_yaml(_user_file(user_id))
    if per_user:
        blob = per_user.get("preferences")
        return blob if isinstance(blob, dict) else {}
    return _legacy_slice(user_id)


def write_preferences(user_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace ``user_id``'s preferences and persist atomically. Returns the stored blob.

    Raises :class:`PreferencesUnavailable` if the file cannot be written (read-only FS, etc.).
    """
    path = _user_file(user_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: serialize to a temp file in the same directory, fsync, then os.replace.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".preferences-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    {"version": _FILE_VERSION, "preferences": payload},
                    handle,
                    default_flow_style=False,
                    sort_keys=True,
                )
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
