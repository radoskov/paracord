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


def _request(
    path: str,
    *,
    query: bytes = b"",
    cookies: dict | None = None,
    method: str = "GET",
    body: bytes | None = None,
) -> Request:
    headers = []
    if cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers,
    }
    if body is None:
        return Request(scope)

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive=receive)


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


def test_browse_lists_dirs_and_pdfs(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "notes.txt").write_text("ignore me")
    app = create_app("tok")
    browse = _route(app, "/api/browse")
    res = asyncio.run(
        browse(_request("/api/browse", query=b"token=tok&path=" + str(tmp_path).encode()))
    )
    assert res.status_code == 200
    import json

    payload = json.loads(res.body)
    names = {e["name"] for e in payload["entries"]}
    assert names == {"sub", "paper.pdf"}  # .txt excluded
    assert payload["path"] == str(tmp_path)


def test_update_and_pause_managed_item(tmp_path) -> None:
    import json

    from paperracks_agent.config import AgentConfig, ManagedFolder, load_config, save_config

    config_path = tmp_path / "agent.yaml"
    save_config(AgentConfig(folders=[ManagedFolder(path=tmp_path / "papers")]), config_path)
    app = create_app("tok", config_path=config_path)
    update = _route(app, "/api/items/update")
    body = json.dumps(
        {"path": str(tmp_path / "papers"), "action": "teleport", "enabled": False}
    ).encode()
    res = asyncio.run(
        update(_request("/api/items/update", query=b"token=tok", method="POST", body=body))
    )
    assert res.status_code == 200
    updated = load_config(config_path)
    assert updated.folders[0].action == "teleport"
    assert updated.folders[0].enabled is False


def test_forget_removes_indexed_file(tmp_path) -> None:
    import json

    from paperracks_agent.state import AgentState

    state_path = tmp_path / "state.sqlite3"
    state = AgentState(state_path)
    state.upsert(
        local_file_id="abc", real_path="/x/y.pdf", sha256="d", size_bytes=1, virtual_path="y.pdf"
    )
    state.close()
    app = create_app("tok", state_path=state_path)
    forget = _route(app, "/api/forget")
    res = asyncio.run(
        forget(
            _request(
                "/api/forget",
                query=b"token=tok",
                method="POST",
                body=json.dumps({"local_file_id": "abc"}).encode(),
            )
        )
    )
    assert json.loads(res.body)["forgotten"] is True
    assert AgentState(state_path).all_files() == []


def _post(app, path, body, *, path_params=None):
    import json

    endpoint = _route(app, path)
    req = _request(path, query=b"token=tok", method="POST", body=json.dumps(body).encode())
    if path_params:
        req.scope["path_params"] = path_params
    return asyncio.run(endpoint(req))


def test_prune_unwatched_and_bulk_forget(tmp_path) -> None:
    """Prune-unwatched removes only unwatched rows; bulk forget removes the selected set."""
    import json

    from paperracks_agent.config import AgentConfig, ManagedFolder, save_config
    from paperracks_agent.state import AgentState

    watched = tmp_path / "papers"
    watched.mkdir()
    config_path = tmp_path / "agent.yaml"
    save_config(AgentConfig(folders=[ManagedFolder(path=watched)]), config_path)
    state_path = tmp_path / "state.sqlite3"
    state = AgentState(state_path)
    state.upsert(local_file_id="in", real_path=str(watched / "a.pdf"), sha256="d", size_bytes=1)
    (watched / "a.pdf").write_bytes(b"%PDF-1.4")
    loose = tmp_path / "loose.pdf"
    loose.write_bytes(b"%PDF-1.4")
    state.upsert(local_file_id="out", real_path=str(loose), sha256="e", size_bytes=1)
    state.close()

    app = create_app("tok", config_path=config_path, state_path=state_path)
    res = _post(app, "/api/prune-unwatched", {})
    assert json.loads(res.body)["pruned"] == 1
    assert {r.local_file_id for r in AgentState(state_path).all_files()} == {"in"}

    # Bulk forget the remaining watched row.
    res = _post(app, "/api/bulk", {"action": "forget", "local_file_ids": ["in"]})
    assert json.loads(res.body)["affected"] == 1
    assert AgentState(state_path).all_files() == []


def test_unwatch_preview_and_prune_now(tmp_path) -> None:
    """unwatch-preview lists affected files; remove with prune_ids drops exactly those."""
    import json

    from paperracks_agent.config import AgentConfig, ManagedFolder, save_config
    from paperracks_agent.state import AgentState

    folder = tmp_path / "papers"
    folder.mkdir()
    pdf = folder / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    config_path = tmp_path / "agent.yaml"
    save_config(AgentConfig(folders=[ManagedFolder(path=folder)]), config_path)
    state_path = tmp_path / "state.sqlite3"
    state = AgentState(state_path)
    state.upsert(local_file_id="x", real_path=str(pdf), sha256="d", size_bytes=1)
    state.close()

    app = create_app("tok", config_path=config_path, state_path=state_path)
    preview = _route(app, "/api/unwatch-preview")
    res = asyncio.run(
        preview(_request("/api/unwatch-preview", query=b"token=tok&path=" + str(folder).encode()))
    )
    payload = json.loads(res.body)
    assert payload["count"] == 1 and payload["files"][0]["local_file_id"] == "x"

    res = _post(app, "/api/remove", {"path": str(folder), "prune_ids": ["x"]})
    body = json.loads(res.body)
    assert body["pruned"] == 1
    assert AgentState(state_path).all_files() == []


def test_set_auto_prune_persists(tmp_path) -> None:
    """The forward auto-prune toggle round-trips through config (default OFF)."""
    import json

    from paperracks_agent.config import AgentConfig, load_config, save_config

    config_path = tmp_path / "agent.yaml"
    save_config(AgentConfig(), config_path)
    assert load_config(config_path).auto_prune_unwatched is False
    app = create_app("tok", config_path=config_path)
    res = _post(app, "/api/set-auto-prune", {"enabled": True})
    assert json.loads(res.body)["auto_prune_unwatched"] is True
    assert load_config(config_path).auto_prune_unwatched is True


def test_reconcile_arm_delete_endpoint(tmp_path) -> None:
    """The arm-delete endpoint sets the one-shot flag in agent state (dialog 1 backing)."""
    import json

    from paperracks_agent import agent_ops
    from paperracks_agent.state import AgentState

    state_path = tmp_path / "state.sqlite3"
    app = create_app("tok", state_path=state_path)
    res = _post(app, "/api/reconcile/arm-delete", {})
    assert json.loads(res.body)["armed"] is True
    assert agent_ops.is_delete_on_disk_armed(AgentState(state_path)) is True


def test_reconcile_route_registered() -> None:
    app = create_app("secret")
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {
        "/api/reconcile",
        "/api/reconcile/arm-delete",
        "/api/prune-unwatched",
        "/api/bulk",
    } <= paths


def test_view_route_streams_local_pdf(tmp_path) -> None:
    """The local Read route (#13) serves an indexed PDF, resolving its path local-only."""
    from paperracks_agent.state import AgentState

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nbody")
    state_path = tmp_path / "state.sqlite3"
    state = AgentState(state_path)
    state.upsert(local_file_id="vid", real_path=str(pdf), sha256="d", size_bytes=13)
    state.close()

    app = create_app("tok", state_path=state_path)
    view = _route(app, "/api/files/{local_file_id}/view")

    req = _request("/api/files/vid/view", query=b"token=tok")
    req.scope["path_params"] = {"local_file_id": "vid"}
    res = asyncio.run(view(req))
    assert res.status_code == 200
    assert res.media_type == "application/pdf"

    # Unknown id → 404.
    bad = _request("/api/files/none/view", query=b"token=tok")
    bad.scope["path_params"] = {"local_file_id": "none"}
    assert asyncio.run(view(bad)).status_code == 404

    # Bad token → 401 (never reaches the filesystem).
    denied = _request("/api/files/vid/view", query=b"token=wrong")
    denied.scope["path_params"] = {"local_file_id": "vid"}
    assert asyncio.run(view(denied)).status_code == 401


def test_view_route_is_registered() -> None:
    app = create_app("secret")
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/files/{local_file_id}/view" in paths


def test_web_runtime_lifecycle(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    assert web_server._read_runtime() is None

    runtime = web_server.runtime_path()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('{"pid": 999999, "host": "127.0.0.1", "port": 8765, "token": "x"}')

    # A stale pid is reported as stopped and the file is cleaned up.
    web_server.web_status(object())
    assert not runtime.exists()
