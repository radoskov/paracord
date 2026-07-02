"""Secret storage for the agent (SPEC §32.2).

Secrets (the server bearer token, the web access token) are kept in the OS keyring when the
``keyring`` package is importable and usable, otherwise in a ``0600`` JSON file in the config
directory. The same API is used either way.
"""

import contextlib
import json
import os
import stat
from pathlib import Path

_SERVICE = "paracord-agent"


def _keyring():
    try:
        import keyring  # type: ignore

        # Touch the backend; a missing/headless backend raises here, so we fall back to a file.
        keyring.get_keyring()
        return keyring
    except Exception:  # noqa: BLE001 - any keyring problem → file fallback
        return None


def _secrets_file() -> Path:
    env = os.environ.get("PARACORD_AGENT_HOME")
    base = Path(env).expanduser() if env else Path("~/.config/paracord").expanduser()
    return base / "secrets.json"


def _read_file() -> dict:
    path = _secrets_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(data: dict) -> None:
    path = _secrets_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Create 0600 up-front and replace atomically, so the secrets are never world-readable.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data))
    os.replace(tmp, path)


def set_secret(name: str, value: str) -> None:
    """Store a secret under ``name``."""
    kr = _keyring()
    if kr is not None:
        kr.set_password(_SERVICE, name, value)
        return
    data = _read_file()
    data[name] = value
    _write_file(data)


def get_secret(name: str) -> str | None:
    """Return the secret stored under ``name``, or None."""
    kr = _keyring()
    if kr is not None:
        value = kr.get_password(_SERVICE, name)
        if value is not None:
            return value
    return _read_file().get(name)


def delete_secret(name: str) -> None:
    """Remove the secret stored under ``name`` (both backends, best-effort)."""
    kr = _keyring()
    if kr is not None:
        with contextlib.suppress(Exception):  # missing entry is fine
            kr.delete_password(_SERVICE, name)
    data = _read_file()
    if name in data:
        del data[name]
        _write_file(data)


def resolve_token(explicit: str | None) -> str | None:
    """Resolve the agent bearer token: explicit arg, then $PARACORD_AGENT_TOKEN, then storage."""
    if explicit:
        return explicit
    env = os.environ.get("PARACORD_AGENT_TOKEN")
    if env:
        return env
    return get_secret("agent_token")
