"""Local-only web GUI for the agent (SPEC §32.7).

A minimal self-served Starlette app: one HTML page plus a small JSON API that wraps the same
config/state/agent_ops the CLI uses. Bound to 127.0.0.1 and gated by an access token printed by
``paracord-agent web up`` — there is no off-host surface.
"""

import contextlib
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from paperracks_agent import agent_ops
from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.config import ManagedFile, ManagedFolder, load_config, save_config
from paperracks_agent.secrets import resolve_token, set_secret
from paperracks_agent.state import AgentState

_COOKIE = "pa_token"


def create_app(
    access_token: str, *, config_path: Path | None = None, state_path: Path | None = None
) -> Starlette:
    """Build the agent's local web app, gated by ``access_token``."""

    def authed(request: Request) -> bool:
        provided = (
            request.query_params.get("token")
            or request.cookies.get(_COOKIE)
            or request.headers.get("x-access-token")
        )
        return bool(provided) and provided == access_token

    def _config():
        return load_config(config_path)

    def _client(config):
        return PaRacORDServerClient(config.server_url, token=resolve_token(None))

    def _state():
        return AgentState(state_path)

    async def index(request: Request) -> HTMLResponse:
        if not authed(request):
            return HTMLResponse(
                "Forbidden — open the URL printed by `web up` (with ?token=).", status_code=403
            )
        response = HTMLResponse(_PAGE)
        if request.query_params.get("token"):
            response.set_cookie(_COOKIE, access_token, httponly=True, samesite="strict")
        return response

    def guard(request: Request):
        return (
            None if authed(request) else JSONResponse({"detail": "unauthorized"}, status_code=401)
        )

    async def api_status(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        config = _config()
        state = _state()
        out = {
            "server_url": config.server_url,
            "agent_id": config.agent_id,
            "name": config.name,
            "default_action": config.default_action,
            "default_teleport_policy": config.default_teleport_policy,
            "folders": [f.model_dump(mode="json") for f in config.folders],
            "files": [f.model_dump(mode="json") for f in config.files],
            "indexed": len(state.all_files()),
            "connected": False,
            "me": None,
        }
        try:
            out["me"] = await _client(config).get_me()
            out["connected"] = True
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
        return JSONResponse(out)

    async def api_connect(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        config = _config()
        config.server_url = body["url"]
        save_config(config, config_path)
        return JSONResponse({"server_url": config.server_url})

    async def api_enroll(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        config = _config()
        server = body.get("url") or config.server_url
        name = body.get("name") or config.name
        try:
            result = await PaRacORDServerClient(server).enroll(body["token"], name)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"detail": str(exc)}, status_code=400)
        config.server_url = server
        config.name = name
        config.agent_id = result["agent_id"]
        save_config(config, config_path)
        return JSONResponse(result)

    async def api_set_token(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        set_secret("agent_token", body["token"])
        return JSONResponse({"ok": True})

    async def api_add_item(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        config = _config()
        action = body.get("action") or config.default_action
        policy = body.get("teleport_policy") or config.default_teleport_policy
        if body.get("kind") == "file":
            config.files.append(
                ManagedFile(path=body["path"], action=action, teleport_policy=policy)
            )
        else:
            config.folders.append(
                ManagedFolder(
                    path=body["path"],
                    mode=body.get("mode", "monitored"),
                    action=action,
                    teleport_policy=policy,
                )
            )
        save_config(config, config_path)
        return JSONResponse({"ok": True})

    async def api_remove(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        target = str(Path(body["path"]).expanduser())
        config = _config()
        config.folders = [f for f in config.folders if str(f.path.expanduser()) != target]
        config.files = [f for f in config.files if str(f.path.expanduser()) != target]
        save_config(config, config_path)
        return JSONResponse({"ok": True})

    async def api_sync(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        config = _config()
        summary = await agent_ops.sync(config, _state(), _client(config))
        return JSONResponse(summary)

    async def api_files(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        state = _state()
        local = {r.local_file_id: r for r in state.all_files()}
        server = {}
        with contextlib.suppress(Exception):
            server = {f["local_file_id"]: f for f in await _client(_config()).get_my_files()}
        rows = []
        for lid, rec in local.items():
            sf = server.get(lid, {})
            rows.append(
                {
                    "local_file_id": lid,
                    "virtual_path": rec.virtual_path,
                    "action": rec.import_action,
                    "teleport_policy": rec.teleport_policy,
                    "blocked": rec.teleport_blocked,
                    "present": rec.present,
                    "processing_state": sf.get("processing_state", rec.processing_state),
                }
            )
        return JSONResponse(rows)

    async def api_requests(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        try:
            pending = await _client(_config()).get_pending_teleports()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"detail": str(exc)}, status_code=502)
        return JSONResponse(pending)

    async def api_request_action(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        local_file_id = request.path_params["local_file_id"]
        action = request.path_params["action"]
        config = _config()
        client = _client(config)
        state = _state()
        if action == "approve":
            await agent_ops.approve_request(state, client, local_file_id)
        elif action == "reject":
            body = await request.json() if await request.body() else {}
            await agent_ops.reject_request(
                state, client, local_file_id, forever=bool(body.get("forever"))
            )
        elif action == "unblock":
            await agent_ops.unblock(state, client, local_file_id)
        elif action == "reextract":
            path = state.resolve_path(local_file_id)
            if path and path.exists():
                with path.open("rb") as handle:
                    await client.upload_for_extraction(local_file_id, handle)
        else:
            return JSONResponse({"detail": "unknown action"}, status_code=400)
        return JSONResponse({"ok": True})

    routes = [
        Route("/", index),
        Route("/api/status", api_status),
        Route("/api/connect", api_connect, methods=["POST"]),
        Route("/api/enroll", api_enroll, methods=["POST"]),
        Route("/api/set-token", api_set_token, methods=["POST"]),
        Route("/api/items", api_add_item, methods=["POST"]),
        Route("/api/remove", api_remove, methods=["POST"]),
        Route("/api/sync", api_sync, methods=["POST"]),
        Route("/api/files", api_files),
        Route("/api/requests", api_requests),
        Route("/api/requests/{local_file_id}/{action}", api_request_action, methods=["POST"]),
    ]
    return Starlette(routes=routes)


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>PaRacORD Agent</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#eef1f4;color:#1f2a36}
header{background:#2d3e50;color:#fff;padding:.8rem 1rem;font-weight:700}
main{max-width:60rem;margin:1rem auto;padding:0 1rem;display:grid;gap:1rem}
.card{background:#fff;border:1px solid #d8dee6;border-radius:8px;padding:1rem}
h2{font-size:1rem;margin:0 0 .6rem}
button{background:#2d3e50;color:#fff;border:0;border-radius:6px;padding:.4rem .7rem;cursor:pointer;font:inherit}
button.sec{background:#fff;color:#21303d;border:1px solid #bcc7d2}
input,select{border:1px solid #bcc7d2;border-radius:6px;padding:.4rem;font:inherit}
.row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
table{width:100%;border-collapse:collapse;font-size:.85rem}
td,th{text-align:left;padding:.3rem;border-bottom:1px solid #eef1f4}
.muted{color:#64717f;font-size:.85rem}.ok{color:#14532d}.bad{color:#b3261e}
</style></head><body>
<header>PaRacORD Local Agent</header>
<main>
  <div class="card"><h2>Connection</h2><div id="status" class="muted">…</div>
    <div class="row" style="margin-top:.5rem">
      <input id="url" placeholder="http://server:8000" size="28">
      <button class="sec" onclick="connect()">Set server</button>
    </div>
    <div class="row" style="margin-top:.5rem">
      <input id="enrollTok" placeholder="enrollment token" size="20">
      <input id="agentName" placeholder="agent name" size="14">
      <button class="sec" onclick="enroll()">Enroll</button>
      <input id="agentTok" placeholder="agent token (after approval)" size="22">
      <button class="sec" onclick="setToken()">Save token</button>
    </div>
  </div>

  <div class="card"><h2>Managed folders &amp; files</h2>
    <div class="row">
      <input id="path" placeholder="/home/me/papers" size="26">
      <select id="kind"><option value="folder">folder</option><option value="file">file</option></select>
      <select id="mode"><option value="monitored">monitored</option><option value="once">once</option></select>
      <select id="action"><option>index_only</option><option>index_and_extract</option><option>teleport</option></select>
      <select id="policy"><option value="ask">teleport: ask</option><option value="allow">teleport: allow</option></select>
      <button onclick="addItem()">Add</button>
    </div>
    <div id="items" style="margin-top:.6rem"></div>
  </div>

  <div class="card"><h2>Files &amp; status <button class="sec" onclick="sync()">Sync now</button> <button class="sec" onclick="refresh()">Refresh</button></h2>
    <div id="files"></div>
  </div>

  <div class="card"><h2>Teleport requests</h2><div id="reqs"></div></div>
</main>
<script>
const j=(u,o)=>fetch(u,o).then(r=>r.json());
async function refresh(){
  const s=await j('/api/status');
  document.getElementById('status').innerHTML = s.connected
    ? `<span class="ok">connected</span> to ${s.server_url} — agent <b>${s.me.name}</b> [${s.me.status}]; privileges: ${Object.keys(s.me).filter(k=>k.startsWith('can_')||k.endsWith('visibility')).filter(k=>s.me[k]).join(', ')}`
    : `<span class="bad">not connected</span> (${s.server_url}) ${s.error||''}`;
  document.getElementById('url').value = s.server_url;
  const items = [...s.folders.map(f=>({...f,kind:'folder'})),...s.files.map(f=>({...f,kind:'file'}))];
  document.getElementById('items').innerHTML = items.length? '<table><tr><th>kind</th><th>path</th><th>action</th><th>teleport</th><th></th></tr>'+
    items.map(i=>`<tr><td>${i.kind}${i.mode?'/'+i.mode:''}</td><td>${i.path}</td><td>${i.action}</td><td>${i.teleport_policy}</td><td><button class="sec" onclick="rm(this.dataset.p)" data-p="${i.path}">x</button></td></tr>`).join('')+'</table>'
    : '<p class="muted">No managed items.</p>';
  const files=await j('/api/files');
  document.getElementById('files').innerHTML = files.length? '<table><tr><th>file</th><th>action</th><th>state</th><th></th></tr>'+
    files.map(f=>`<tr><td>${f.virtual_path||f.local_file_id.slice(0,10)}${f.present?'':' <span class="bad">(removed)</span>'}</td><td>${f.action}</td><td>${f.processing_state}${f.blocked?' <span class="bad">blocked</span>':''}</td>`+
      `<td><button class="sec" onclick="fileAct('${f.local_file_id}','reextract')">re-extract</button>${f.blocked?` <button class="sec" onclick="fileAct('${f.local_file_id}','unblock')">unblock</button>`:''}</td></tr>`).join('')+'</table>'
    : '<p class="muted">No files indexed yet.</p>';
  const reqs=await j('/api/requests');
  document.getElementById('reqs').innerHTML = (reqs.length? '<table>'+reqs.map(r=>`<tr><td>${r.display_path||r.local_file_id.slice(0,10)}</td>`+
    `<td><button onclick="reqAct('${r.local_file_id}','approve')">approve</button> `+
    `<button class="sec" onclick="reqAct('${r.local_file_id}','reject')">reject</button> `+
    `<button class="sec" onclick="rejectForever('${r.local_file_id}')">reject forever</button></td></tr>`).join('')+'</table>'
    : '<p class="muted">No pending requests.</p>');
}
async function connect(){await j('/api/connect',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url:document.getElementById('url').value})});refresh();}
async function enroll(){await j('/api/enroll',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token:document.getElementById('enrollTok').value,name:document.getElementById('agentName').value})});refresh();}
async function setToken(){await j('/api/set-token',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token:document.getElementById('agentTok').value})});refresh();}
async function addItem(){await j('/api/items',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path:document.getElementById('path').value,kind:document.getElementById('kind').value,mode:document.getElementById('mode').value,action:document.getElementById('action').value,teleport_policy:document.getElementById('policy').value})});refresh();}
async function rm(p){await j('/api/remove',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path:p})});refresh();}
async function sync(){await j('/api/sync',{method:'POST'});refresh();}
async function reqAct(id,a){await j(`/api/requests/${id}/${a}`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});refresh();}
async function rejectForever(id){await j(`/api/requests/${id}/reject`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({forever:true})});refresh();}
async function fileAct(id,a){await j(`/api/requests/${id}/${a}`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});refresh();}
refresh();
</script></body></html>"""
