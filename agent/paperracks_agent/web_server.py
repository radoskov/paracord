"""Runner + lifecycle for the agent's local web GUI (SPEC §32.7).

``web up`` generates an access token, spawns this module as a detached process bound to
127.0.0.1, records the pid/port/token in a runtime file, and prints the URL. ``web down`` reads
that file and stops the process; ``web status`` reports whether it is running. The GUI is
local-only: it never listens off-host and every request is gated by the printed token.
"""

import contextlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
from pathlib import Path

from paperracks_agent.config import load_config


def runtime_path() -> Path:
    """Where the running web GUI records its pid/port/token."""
    env = os.environ.get("PARACORD_AGENT_HOME")
    base = Path(env).expanduser() if env else Path("~/.local/share/paracord-agent").expanduser()
    return base / "web.json"


def _read_runtime() -> dict | None:
    path = runtime_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def serve(
    *, host: str, port: int, token: str, config_path: str | None, state_path: str | None
) -> None:
    """Run the GUI (blocking). Invoked in the detached child."""
    import uvicorn

    from paperracks_agent.web import create_app

    app = create_app(
        token,
        config_path=Path(config_path) if config_path else None,
        state_path=Path(state_path) if state_path else None,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


def web_up(args) -> None:
    """Start the GUI as a detached background process and print its URL + token."""
    existing = _read_runtime()
    if existing and _alive(existing.get("pid", -1)):
        print(
            f"Web GUI already running: http://{existing['host']}:{existing['port']}/?token={existing['token']}"
        )
        return

    config = load_config(getattr(args, "config", None))
    host = "127.0.0.1"
    port = getattr(args, "port", None) or config.web_port
    token = secrets.token_urlsafe(24)

    runtime = runtime_path()
    runtime.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        runtime.parent.chmod(0o700)
    log_file = runtime.parent / "web.log"

    env = dict(os.environ)
    env.update(
        {
            "PARACORD_WEB_HOST": host,
            "PARACORD_WEB_PORT": str(port),
            "PARACORD_WEB_TOKEN": token,
            "PARACORD_WEB_CONFIG": str(args.config) if getattr(args, "config", None) else "",
            "PARACORD_WEB_STATE": str(args.state) if getattr(args, "state", None) else "",
        }
    )
    with log_file.open("ab") as log:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "paperracks_agent.web_server"],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    runtime.write_text(
        json.dumps({"pid": proc.pid, "host": host, "port": port, "token": token}),
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        runtime.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    print(f"Web GUI started (pid {proc.pid}).")
    print(f"  Open: http://{host}:{port}/?token={token}")
    print("  Stop: paracord-agent web down")


def web_down(args) -> None:
    """Stop the running GUI."""
    runtime = _read_runtime()
    if not runtime:
        print("Web GUI is not running.")
        return
    pid = runtime.get("pid", -1)
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"Could not stop pid {pid}: {exc}")
            return
    runtime_path().unlink(missing_ok=True)
    print(f"Web GUI stopped (pid {pid}).")


def web_status(args) -> None:
    """Report whether the GUI is running and where."""
    runtime = _read_runtime()
    if not runtime:
        print("Web GUI: stopped")
        return
    pid = runtime.get("pid", -1)
    if _alive(pid):
        print(f"Web GUI: running (pid {pid}) → http://{runtime['host']}:{runtime['port']}/")
    else:
        print("Web GUI: stopped (stale runtime file)")
        runtime_path().unlink(missing_ok=True)


def _main_serve() -> None:
    serve(
        host=os.environ.get("PARACORD_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("PARACORD_WEB_PORT", "8765")),
        token=os.environ["PARACORD_WEB_TOKEN"],
        config_path=os.environ.get("PARACORD_WEB_CONFIG") or None,
        state_path=os.environ.get("PARACORD_WEB_STATE") or None,
    )


if __name__ == "__main__":
    _main_serve()
