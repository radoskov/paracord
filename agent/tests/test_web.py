"""Smoke tests for the local web GUI (SPEC §32.7).

Full HTTP coverage would need starlette's TestClient, which pulls in mainline ``httpx`` (the agent
ships ``httpx2`` only), so we exercise the app construction, the token gate, and the runtime-file
lifecycle directly instead.
"""

import asyncio

from paperracks_agent import web_server
from paperracks_agent.web import create_app
from starlette.applications import Starlette
from starlette.requests import Request


def _request(path: str, *, query: bytes = b"", cookies: dict | None = None) -> Request:
    headers = []
    if cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers,
    }
    return Request(scope)


def _route(app: Starlette, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no route for {path}")


def test_create_app_registers_expected_routes() -> None:
    app = create_app("secret")
    assert isinstance(app, Starlette)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/", "/api/status", "/api/sync", "/api/items", "/api/requests"} <= paths


def test_index_requires_token() -> None:
    app = create_app("s3cr3t")
    index = _route(app, "/")

    forbidden = asyncio.run(index(_request("/")))
    assert forbidden.status_code == 403

    ok = asyncio.run(index(_request("/", query=b"token=s3cr3t")))
    assert ok.status_code == 200

    via_cookie = asyncio.run(index(_request("/", cookies={"pa_token": "s3cr3t"})))
    assert via_cookie.status_code == 200


def test_status_api_rejects_bad_token() -> None:
    app = create_app("right")
    status_api = _route(app, "/api/status")
    denied = asyncio.run(status_api(_request("/api/status", query=b"token=wrong")))
    assert denied.status_code == 401


def test_web_runtime_lifecycle(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    assert web_server._read_runtime() is None

    runtime = web_server.runtime_path()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('{"pid": 999999, "host": "127.0.0.1", "port": 8765, "token": "x"}')

    # A stale pid is reported as stopped and the file is cleaned up.
    web_server.web_status(object())
    assert not runtime.exists()
