"""Local-only web GUI for the agent (SPEC §32.7).

A minimal self-served Starlette app: one HTML page plus a small JSON API that wraps the same
config/state/agent_ops the CLI uses. Bound to 127.0.0.1 and gated by an access token printed by
``paracord-agent web up`` — there is no off-host surface. Because it is loopback-only and
token-gated, it is allowed to browse the agent's *own* local filesystem (the picker) and resolve
opaque ids to real paths; neither capability is ever exposed to the server.
"""

import contextlib
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
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
            "folder_stats": agent_ops.folder_stats(config),
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

    async def api_browse(request: Request):
        """List directories + PDFs under a local path so the GUI can offer a picker.

        Local-only and token-gated: the agent already has filesystem access; this never reaches
        the server. Returns the resolved path, its parent, and child dirs/PDFs.
        """
        if (deny := guard(request)) is not None:
            return deny
        raw = request.query_params.get("path") or "~"
        base = Path(raw).expanduser()
        with contextlib.suppress(Exception):
            base = base.resolve(strict=False)
        if not base.is_dir():
            base = Path("~").expanduser().resolve(strict=False)
        dirs, pdfs = [], []
        try:
            for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry), "is_dir": True})
                elif entry.suffix.lower() == ".pdf" and entry.is_file():
                    pdfs.append({"name": entry.name, "path": str(entry), "is_dir": False})
        except PermissionError:
            return JSONResponse({"detail": f"Permission denied: {base}"}, status_code=403)
        parent = str(base.parent) if base.parent != base else None
        return JSONResponse({"path": str(base), "parent": parent, "entries": dirs + pdfs})

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
        path = str(Path(body["path"]).expanduser())
        kind = body.get("kind")
        if kind not in ("file", "folder"):
            kind = "file" if Path(path).is_file() else "folder"
        already = {str(f.path.expanduser()) for f in (*config.folders, *config.files)}
        if path in already:
            return JSONResponse({"detail": "Already managed."}, status_code=400)
        if kind == "file":
            config.files.append(ManagedFile(path=path, action=action, teleport_policy=policy))
        else:
            config.folders.append(
                ManagedFolder(
                    path=path,
                    mode=body.get("mode", "monitored"),
                    action=action,
                    teleport_policy=policy,
                )
            )
        save_config(config, config_path)
        return JSONResponse({"ok": True, "kind": kind})

    async def api_update_item(request: Request):
        """Edit a managed item in place: action / teleport policy / mode / enabled."""
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        target = str(Path(body["path"]).expanduser())
        config = _config()
        fields = ("action", "teleport_policy", "mode", "enabled")
        matched = False
        for item in (*config.folders, *config.files):
            if str(item.path.expanduser()) != target:
                continue
            matched = True
            for f in fields:
                if f in body and hasattr(item, f):
                    setattr(item, f, body[f])
        if not matched:
            return JSONResponse({"detail": "No such managed item."}, status_code=404)
        save_config(config, config_path)
        return JSONResponse({"ok": True})

    async def api_remove(request: Request):
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        target = str(Path(body["path"]).expanduser())
        config = _config()
        before = len(config.folders) + len(config.files)
        config.folders = [f for f in config.folders if str(f.path.expanduser()) != target]
        config.files = [f for f in config.files if str(f.path.expanduser()) != target]
        save_config(config, config_path)
        return JSONResponse(
            {"ok": True, "removed": before - (len(config.folders) + len(config.files))}
        )

    async def api_forget(request: Request):
        """Drop an indexed file from the local index (the on-disk file is left untouched)."""
        if (deny := guard(request)) is not None:
            return deny
        body = await request.json()
        gone = _state().forget(body["local_file_id"])
        return JSONResponse({"ok": True, "forgotten": gone})

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
                    # Prefer the local "extract_queue_failed" marker over the server's optimistic
                    # "extracting" so a dropped extraction enqueue is visible in the GUI (D7).
                    "processing_state": (
                        rec.processing_state
                        if rec.processing_state == agent_ops.EXTRACT_QUEUE_FAILED
                        else sf.get("processing_state", rec.processing_state)
                    ),
                    "teleport_status": sf.get("teleport_status"),
                    # Server→agent metadata sync (#11): prefer the fresh server value, fall back to
                    # the locally cached copy so titles/authors survive an offline refresh.
                    "extracted_title": sf.get("extracted_title") or rec.extracted_title,
                    "extracted_authors": sf.get("extracted_authors") or rec.extracted_authors,
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
        elif action == "offer_teleport":
            # Agent-initiated teleport (#12): push the file's bytes directly to the server.
            ok = await agent_ops.request_teleport_offer(state, client, local_file_id)
            if not ok:
                return JSONResponse(
                    {"detail": "File not found locally; re-sync first."}, status_code=404
                )
        else:
            return JSONResponse({"detail": "unknown action"}, status_code=400)
        return JSONResponse({"ok": True})

    async def api_view_file(request: Request):
        """Stream a locally-indexed PDF for in-browser reading (#13).

        Loopback + token-gated, and the id → real path resolution is **local-only** (state.resolve_path);
        no server-supplied path is ever accepted. Only files already in the local index can be served.
        """
        if (deny := guard(request)) is not None:
            return deny
        local_file_id = request.path_params["local_file_id"]
        path = _state().resolve_path(local_file_id)
        if path is None or not path.exists() or not path.is_file():
            return JSONResponse({"detail": "Not found locally."}, status_code=404)
        if path.suffix.lower() != ".pdf":
            return JSONResponse({"detail": "Not a PDF."}, status_code=400)

        def _chunks():
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            _chunks(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    async def on_error(request: Request, exc: Exception):
        """Surface any unhandled handler error as JSON so the GUI can toast it (not a silent 500)."""
        return JSONResponse({"detail": str(exc) or exc.__class__.__name__}, status_code=500)

    routes = [
        Route("/", index),
        Route("/api/status", api_status),
        Route("/api/browse", api_browse),
        Route("/api/connect", api_connect, methods=["POST"]),
        Route("/api/enroll", api_enroll, methods=["POST"]),
        Route("/api/set-token", api_set_token, methods=["POST"]),
        Route("/api/items", api_add_item, methods=["POST"]),
        Route("/api/items/update", api_update_item, methods=["POST"]),
        Route("/api/remove", api_remove, methods=["POST"]),
        Route("/api/forget", api_forget, methods=["POST"]),
        Route("/api/sync", api_sync, methods=["POST"]),
        Route("/api/files", api_files),
        Route("/api/files/{local_file_id}/view", api_view_file),
        Route("/api/requests", api_requests),
        Route("/api/requests/{local_file_id}/{action}", api_request_action, methods=["POST"]),
    ]
    return Starlette(routes=routes, exception_handlers={Exception: on_error})


_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>PaRacORD Agent</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
html,body{height:100%}
body{font-family:system-ui,sans-serif;margin:0;background:#eef1f4;color:#1f2a36;
  display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{background:#2d3e50;color:#fff;padding:.7rem 1rem;flex:0 0 auto}
header .title{font-weight:700}
header .conn{font-size:.82rem;margin-top:.2rem;opacity:.95}
nav{flex:0 0 auto;background:#243443;display:flex;gap:.2rem;padding:0 .6rem}
nav button{background:transparent;color:#aebccb;border:0;border-bottom:3px solid transparent;
  padding:.6rem .9rem;cursor:pointer;font:inherit;font-weight:600}
nav button.active{color:#fff;border-bottom-color:#5aa0e0}
nav .count{font-size:.7rem;background:#5aa0e0;color:#fff;border-radius:8px;padding:0 .35rem;margin-left:.3rem}
main{flex:1 1 auto;overflow-y:auto;padding:1rem;max-width:64rem;width:100%;margin:0 auto}
.tab{display:none}.tab.active{display:block}
.card{background:#fff;border:1px solid #d8dee6;border-radius:8px;padding:1rem;margin-bottom:1rem}
h2{font-size:1rem;margin:0 0 .7rem}
button{background:#2d3e50;color:#fff;border:0;border-radius:6px;padding:.4rem .7rem;cursor:pointer;font:inherit}
button.sec{background:#fff;color:#21303d;border:1px solid #bcc7d2}
button.tiny{padding:.2rem .45rem;font-size:.78rem}
button.danger{background:#fff;color:#b3261e;border:1px solid #e3a8a3}
input,select{border:1px solid #bcc7d2;border-radius:6px;padding:.4rem;font:inherit}
.row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
table{width:100%;border-collapse:collapse;font-size:.85rem}
td,th{text-align:left;padding:.35rem;border-bottom:1px solid #eef1f4;vertical-align:middle}
.muted{color:#64717f;font-size:.85rem}.ok{color:#14532d}.bad{color:#b3261e}
.pill{font-size:.72rem;border-radius:10px;padding:.05rem .45rem;background:#eef1f4;color:#41505f}
.pill.mono{font-family:ui-monospace,Menlo,monospace;cursor:pointer}
.pill.mono:hover{background:#dde6ef}
.fullhash{position:absolute;left:-9999px}
.paused{opacity:.55}
.statusLight{display:inline-block;width:.7rem;height:.7rem;border-radius:50%;background:#888;
  margin-right:.4rem;vertical-align:middle;box-shadow:0 0 0 2px rgba(255,255,255,.15)}
.statusLight.green{background:#3fbf6b}.statusLight.yellow{background:#e3b23c}.statusLight.red{background:#e0564b}
.filters{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.7rem}
.filters input{flex:1;min-width:10rem}
th.sortable{cursor:pointer;user-select:none}
.filetable{table-layout:fixed}
.filetable td{overflow-wrap:anywhere;word-break:break-word}
.titlecell{overflow-wrap:anywhere;word-break:break-word}
.pathcell{overflow-wrap:anywhere;word-break:break-word}
.radios{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.7rem;font-size:.85rem}
.radiogroup{display:inline-flex;gap:.4rem;align-items:center;flex-wrap:wrap;
  border:1px solid #e3e8ee;border-radius:6px;padding:.2rem .5rem}
.radiogroup>span{color:#64717f}
.radiogroup label{display:inline-flex;gap:.2rem;align-items:center}
.modal{position:fixed;inset:0;background:rgba(20,28,38,.5);display:none;align-items:center;justify-content:center;z-index:20}
.modal.open{display:flex}
.modal .box{background:#fff;border-radius:10px;width:min(40rem,92vw);max-height:84vh;display:flex;flex-direction:column}
.modal .head{padding:.8rem 1rem;border-bottom:1px solid #e3e8ee;display:flex;gap:.5rem;align-items:center}
.modal .head code{font-size:.8rem;background:#eef1f4;padding:.15rem .4rem;border-radius:4px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal .body{overflow-y:auto;padding:.3rem 0}
.modal .foot{padding:.7rem 1rem;border-top:1px solid #e3e8ee;display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
.entry{display:flex;align-items:center;gap:.5rem;padding:.4rem 1rem;cursor:pointer}
.entry:hover{background:#f3f6fa}
.entry .nm{flex:1}
#toasts{position:fixed;top:.8rem;right:.8rem;display:flex;flex-direction:column;gap:.4rem;z-index:40}
.toast{background:#243443;color:#fff;padding:.5rem .8rem;border-radius:6px;font-size:.85rem;box-shadow:0 2px 8px rgba(0,0,0,.2);max-width:24rem}
.toast.bad{background:#9c1c12}.toast.ok{background:#1f6b3a}
</style></head><body>
<header>
  <div class="title"><span class="statusLight" id="statusLight" title="server status"></span>PaRacORD Local Agent</div>
  <div class="conn" id="conn">…</div>
</header>
<nav>
  <button data-tab="conn" class="active" onclick="show('conn')" title="Server connection and enrollment">Connection</button>
  <button data-tab="folders" onclick="show('folders')" title="Folders and files this agent watches">Folders &amp; files <span class="count" id="cFolders">0</span></button>
  <button data-tab="files" onclick="show('files')" title="Files this agent has indexed">Indexed <span class="count" id="cFiles">0</span></button>
  <button data-tab="reqs" onclick="show('reqs')" title="Teleport requests from the server">Requests <span class="count" id="cReqs">0</span></button>
</nav>
<main>
  <section class="tab active" data-tab="conn">
    <div class="card"><h2>Server connection</h2>
      <div id="status" class="muted">…</div>
      <div class="row" style="margin-top:.6rem">
        <input id="url" placeholder="http://server:8000" size="28">
        <button class="sec" onclick="act(connect,'Server URL saved')" title="Save the server URL this agent connects to">Set server</button>
      </div>
    </div>
    <div class="card"><h2>Enrollment</h2>
      <p class="muted">Enroll, have the owner approve the agent, then paste the agent token they give you.</p>
      <div class="row" style="margin-top:.5rem">
        <input id="enrollTok" placeholder="enrollment token" size="20">
        <input id="agentName" placeholder="agent name" size="14">
        <button class="sec" onclick="act(enroll,'Enrolled — ask the owner to approve')" title="Enroll this agent with the server using the token above">Enroll</button>
      </div>
      <div class="row" style="margin-top:.5rem">
        <input id="agentTok" placeholder="agent token (after approval)" size="26">
        <button class="sec" onclick="act(setToken,'Agent token saved')" title="Save the agent bearer token issued after approval">Save token</button>
      </div>
    </div>
  </section>

  <section class="tab" data-tab="folders">
    <div class="card"><h2>Managed folders &amp; files</h2>
      <div class="row">
        <button onclick="openPicker()" title="Browse this workstation to add a folder or file">+ Add folder or file…</button>
        <span class="muted">or paste a path:</span>
        <input id="path" placeholder="/home/me/papers" size="24">
        <button class="sec" onclick="addByPath()" title="Add the pasted path to the watched list">Add</button>
      </div>
      <div id="items" style="margin-top:.7rem"></div>
    </div>
  </section>

  <section class="tab" data-tab="files">
    <div class="card"><h2>Indexed files
        <button class="sec tiny" onclick="act(sync)" title="Re-scan the watched folders and send the manifest to the server">Sync now</button>
        <button class="sec tiny" onclick="refresh()" title="Reload the indexed-files list">Refresh</button></h2>
      <div class="filters">
        <input id="fSearch" placeholder="search filename, hash, title or authors…" oninput="renderFiles()">
        <label>action <select id="fAction" onchange="renderFiles()"><option value="">all</option>
          <option value="index_only">index_only</option><option value="index_and_extract">index_and_extract</option><option value="teleport">teleport</option></select></label>
        <label>state <select id="fState" onchange="renderFiles()"><option value="">all</option></select></label>
      </div>
      <div class="radios">
        <span class="radiogroup"><span>sort by</span>
          <label><input type="radio" name="fSortField" value="file" onchange="saveSort();renderFiles()"> file</label>
          <label><input type="radio" name="fSortField" value="title" onchange="saveSort();renderFiles()"> title</label>
          <label><input type="radio" name="fSortField" value="action" onchange="saveSort();renderFiles()"> action</label>
          <label><input type="radio" name="fSortField" value="state" onchange="saveSort();renderFiles()"> state</label>
          <label><input type="radio" name="fSortField" value="hash" onchange="saveSort();renderFiles()"> hash</label>
        </span>
        <span class="radiogroup"><span>order</span>
          <label><input type="radio" name="fSortDir" value="asc" onchange="saveSort();renderFiles()"> asc</label>
          <label><input type="radio" name="fSortDir" value="desc" onchange="saveSort();renderFiles()"> desc</label>
        </span>
      </div>
      <div id="files"></div>
    </div>
  </section>

  <section class="tab" data-tab="reqs">
    <div class="card"><h2>Teleport requests <button class="sec tiny" onclick="refresh()" title="Reload the teleport requests list">Refresh</button></h2>
      <div id="reqs"></div>
    </div>
  </section>
</main>

<div class="modal" id="picker">
  <div class="box">
    <div class="head">
      <button class="sec tiny" onclick="browse(pickerParent)" id="upBtn" title="Go up to the parent folder">↑ Up</button>
      <code id="pickerPath"></code>
      <button class="sec tiny" onclick="closePicker()" title="Close without adding">✕</button>
    </div>
    <div class="body" id="pickerList"></div>
    <div class="foot">
      <label>action <select id="pAction"><option>index_only</option><option>index_and_extract</option><option>teleport</option></select></label>
      <label>teleport <select id="pPolicy"><option value="ask">ask</option><option value="allow">allow</option></select></label>
      <span class="row" style="gap:.3rem">mode
        <label><input type="radio" name="pMode" value="monitored" onchange="savePMode()"> monitored</label>
        <label><input type="radio" name="pMode" value="once" onchange="savePMode()"> once</label>
      </span>
      <button onclick="addCurrentFolder()" title="Watch the folder currently shown above">Add this folder</button>
    </div>
  </div>
</div>

<div id="toasts"></div>
<script>
let pickerParent=null;
let allFiles=[];        // last /api/files result, cached for client-side search/sort/filter (#15)
let canTeleport=false;  // from /api/status me.can_teleport — gates the offer-teleport button (#12)
// Capture the access token from the opening URL (the session cookie is httpOnly, so JS can't read
// it); needed to authorize the loopback /view route opened in a new tab (#13).
const pageToken=new URLSearchParams(location.search).get('token')||'';
function show(t){
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));
  document.querySelectorAll('.tab').forEach(s=>s.classList.toggle('active',s.dataset.tab===t));
}
function toast(msg,kind){
  const el=document.createElement('div');el.className='toast '+(kind||'');el.textContent=msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(()=>el.remove(),kind==='bad'?6000:3000);
}
async function api(u,o){
  const r=await fetch(u,o);let d=null;try{d=await r.json()}catch(e){}
  if(!r.ok)throw new Error((d&&d.detail)||(r.status+' '+r.statusText));
  return d;
}
// wrap an action: run it, toast success (or the value it returns) or the error message, then refresh.
async function act(fn,okMsg){try{const r=await fn();
  const m=typeof okMsg==='function'?okMsg(r):(okMsg!==undefined?okMsg:(typeof r==='string'?r:'Done'));
  toast(m,'ok');}catch(e){toast(e.message,'bad');}await refresh();}

function setLight(color,title){const el=document.getElementById('statusLight');
  el.className='statusLight '+color;el.title=title;}
async function refresh(){
  let s;
  try{s=await api('/api/status');}catch(e){document.getElementById('conn').textContent='status error: '+e.message;setLight('red','status error: '+e.message);return;}
  canTeleport=!!(s.connected&&s.me&&s.me.can_teleport);
  // Status light (#17): green = reachable + approved; yellow = reachable but error; red = unreachable.
  if(!s.connected){setLight('red','unreachable'+(s.error?' — '+s.error:''));}
  else if(s.me&&s.me.status==='approved'){setLight('green','reachable + approved');}
  else{setLight('yellow','reachable but '+((s.me&&s.me.status)||'no agent identity'));}
  document.getElementById('conn').innerHTML = s.connected
    ? `<span class="ok">●</span> connected to ${esc(s.server_url)} — agent <b>${esc(s.me.name)}</b> [${esc(s.me.status)}]; can: ${esc(Object.keys(s.me).filter(k=>k.startsWith('can_')&&s.me[k]).map(k=>k.slice(4)).join(', ')||'—')}`
    : `<span class="bad">●</span> not connected (${esc(s.server_url)}) ${s.error?'— '+esc(s.error):''}`;
  const url=document.getElementById('url');if(document.activeElement!==url)url.value=s.server_url;
  // managed items
  const items=[...s.folders.map(f=>({...f,kind:'folder'})),...s.files.map(f=>({...f,kind:'file'}))];
  document.getElementById('cFolders').textContent=items.length;
  document.getElementById('items').innerHTML = items.length? '<table><tr><th>path</th><th>kind</th><th>action</th><th>teleport</th><th>found</th><th></th></tr>'+
    items.map(i=>{
      const st=s.folder_stats[i.path];
      const found=i.kind!=='folder'?'<span class="muted">file</span>'
        :(!st||!st.exists?'<span class="bad">missing</span>':`${st.pdfs} PDFs · ${st.subfolders} dirs`);
      const ep=enc(i.path);
      const sel=(name,opts,val)=>`<select onchange="updateItem('${ep}','${name}',this.value)">`+opts.map(o=>`<option${o[0]===val?' selected':''} value="${o[0]}">${o[1]}</option>`).join('')+'</select>';
      const modeSel=i.kind==='folder'?sel('mode',[['monitored','monitored'],['once','once']],i.mode):'<span class="muted">file</span>';
      return `<tr class="${i.enabled?'':'paused'}"><td><span title="${esc(i.path)}">${esc(i.path)}</span></td>`+
        `<td>${modeSel}</td>`+
        `<td>${sel('action',[['index_only','index_only'],['index_and_extract','index_and_extract'],['teleport','teleport']],i.action)}</td>`+
        `<td>${sel('teleport_policy',[['ask','ask'],['allow','allow']],i.teleport_policy)}</td>`+
        `<td class="muted">${found}</td>`+
        `<td class="row"><button class="sec tiny" onclick="togglePause('${ep}',${i.enabled})" title="${i.enabled?'Stop watching this item (keep it in the list)':'Resume watching this item'}">${i.enabled?'Pause':'Resume'}</button>`+
        `<button class="danger tiny" onclick="removeItem('${ep}')" title="Stop watching and remove this item from the list">Remove</button></td></tr>`;
    }).join('')+'</table>'
    : '<p class="muted">No managed items. Click “Add folder or file…”.</p>';
  // indexed files (#15: cache + client-side search/sort/filter via renderFiles)
  try{allFiles=await api('/api/files');}catch(e){toast('files: '+e.message,'bad');allFiles=[];}
  renderFiles();
  // requests
  let reqs=[];try{reqs=await api('/api/requests');}catch(e){/* visibility/connectivity */}
  document.getElementById('cReqs').textContent=reqs.length||0;
  document.getElementById('reqs').innerHTML = (reqs.length? '<table>'+reqs.map(r=>`<tr><td>${esc(r.display_path||r.local_file_id.slice(0,10))}</td>`+
    `<td class="row"><button class="tiny" onclick="reqAct('${r.local_file_id}','approve','Approved — pushing file')" title="Approve and upload this file to the server">approve</button>`+
    `<button class="sec tiny" onclick="reqAct('${r.local_file_id}','reject','Rejected')" title="Reject this request (it may be requested again later)">reject</button>`+
    `<button class="danger tiny" onclick="rejectForever('${r.local_file_id}')" title="Reject and never offer this file again">reject forever</button></td></tr>`).join('')+'</table>'
    : '<p class="muted">No pending requests.</p>');
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');}
// Encode a value for safe use as an onclick JS-string argument (apostrophe included). Decoded in the handler.
function enc(s){return encodeURIComponent(String(s)).replace(/'/g,'%27');}

// --- indexed files: client-side search / sort / filter (#15) ---
function renderFiles(){
  const files=allFiles||[];
  document.getElementById('cFiles').textContent=files.length;
  // Keep the state filter's options in sync with the states actually present.
  const fState=document.getElementById('fState');
  const states=[...new Set(files.map(f=>f.processing_state).filter(Boolean))].sort();
  const cur=fState.value;
  fState.innerHTML='<option value="">all</option>'+states.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');
  fState.value=states.includes(cur)?cur:'';
  const q=document.getElementById('fSearch').value.trim().toLowerCase();
  const fAction=document.getElementById('fAction').value;
  const fSt=fState.value;
  const sortR=document.querySelector('input[name=fSortField]:checked');
  const sort=sortR?sortR.value:'file';
  const dirR=document.querySelector('input[name=fSortDir]:checked');
  const dir=dirR?dirR.value:'asc';
  let rows=files.filter(f=>{
    if(fAction&&f.action!==fAction)return false;
    if(fSt&&f.processing_state!==fSt)return false;
    if(!q)return true;
    const hay=[f.virtual_path,f.local_file_id,f.extracted_title,f.extracted_authors].map(x=>(x||'').toLowerCase());
    return hay.some(h=>h.includes(q));
  });
  const key=f=>({file:(f.virtual_path||f.local_file_id),title:(f.extracted_title||''),
    action:f.action,state:f.processing_state,hash:f.local_file_id})[sort]||'';
  rows.sort((a,b)=>{const cmp=String(key(a)).toLowerCase().localeCompare(String(key(b)).toLowerCase());return dir==='desc'?-cmp:cmp;});
  const el=document.getElementById('files');
  if(!files.length){el.innerHTML='<p class="muted">No files indexed yet — add a folder, then “Sync now”.</p>';return;}
  if(!rows.length){el.innerHTML='<p class="muted">No files match the current filters.</p>';return;}
  el.innerHTML='<table class="filetable">'+
    '<col style="width:26%"><col style="width:16%"><col style="width:26%">'+
    '<col style="width:10%"><col style="width:12%"><col style="width:10%">'+
    '<tr><th>file</th><th>id (hash)</th><th>title</th><th>action</th><th>state</th><th></th></tr>'+
    rows.map(f=>{
      const title=f.extracted_title?`<span class="titlecell" title="${esc(f.extracted_title)}${f.extracted_authors?'\n'+esc(f.extracted_authors):''}">${esc(f.extracted_title)}</span>`:'<span class="muted">—</span>';
      const teleported=(f.processing_state==='teleported')||(f.teleport_status==='complete');
      const offerBtn=(canTeleport&&!teleported&&!f.blocked&&f.present)
        ?`<button class="sec tiny" onclick="fileAct('${f.local_file_id}','offer_teleport','Teleport offered — uploading')" title="Upload this file into the server library">offer teleport</button>`:'';
      const gone=f.present?'':' <span class="bad" title="The original file is no longer present in the indexed folder on this workstation — the agent can no longer see it. The server still keeps the indexed copy and metadata.">(file no longer on this workstation)</span>';
      return `<tr><td class="pathcell">${esc(f.virtual_path||f.local_file_id.slice(0,10))}${gone}</td>`+
        `<td><span class="pill mono" data-hash="${esc(f.local_file_id)}" onclick="copyHash('${f.local_file_id}')" title="click to copy full hash (the cross-reference shown on the server)">#${esc(f.local_file_id.slice(0,12))}…</span>`+
        `<span class="fullhash">${esc(f.local_file_id)}</span></td>`+
        `<td>${title}</td>`+
        `<td><span class="pill">${f.action}</span></td><td>${f.processing_state}${f.blocked?' <span class="bad">blocked</span>':''}</td>`+
        `<td class="row"><button class="sec tiny" onclick="readFile('${f.local_file_id}')"${f.present?'':' disabled'} title="${f.present?'Open this PDF in a new tab':'The original file is no longer on this workstation'}">read</button>`+
        offerBtn+
        `<button class="sec tiny" onclick="fileAct('${f.local_file_id}','reextract','Re-extract requested')" title="Ask the server to extract this file again">re-extract</button>`+
        (f.blocked?`<button class="sec tiny" onclick="fileAct('${f.local_file_id}','unblock','Unblocked')" title="Unblock this file so it can be processed again">unblock</button>`:'')+
        `<button class="danger tiny" onclick="forget('${f.local_file_id}')" title="Forget this file locally (the server keeps its copy)">forget</button></td></tr>`;
    }).join('')+'</table>';
}
// Open a locally-indexed PDF in a new tab (#13). The httpOnly session cookie authorizes the
// same-origin navigation; the captured page token is appended as a fallback when no cookie is set.
function readFile(id){const q=pageToken?`?token=${encodeURIComponent(pageToken)}`:'';
  window.open(`/api/files/${id}/view${q}`,'_blank');}
// Copy the full content hash to the clipboard (#14).
function copyHash(h){navigator.clipboard.writeText(h).then(()=>toast('Hash copied','ok'),()=>toast('Copy failed','bad'));}

// --- server connection / enrollment ---
function connect(){return api('/api/connect',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url:document.getElementById('url').value})});}
function enroll(){return api('/api/enroll',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token:document.getElementById('enrollTok').value,name:document.getElementById('agentName').value})});}
function setToken(){return api('/api/set-token',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token:document.getElementById('agentTok').value})});}

// --- managed items ---
function addByPath(){const p=document.getElementById('path').value.trim();if(!p){toast('Enter a path','bad');return;}
  act(()=>api('/api/items',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path:p})}).then(()=>{document.getElementById('path').value='';}),'Added');}
function updateItem(path,field,value){path=decodeURIComponent(path);act(()=>api('/api/items/update',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path,[field]:value})}),'Updated');}
function togglePause(path,enabled){path=decodeURIComponent(path);act(()=>api('/api/items/update',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path,enabled:!enabled})}),enabled?'Paused':'Resumed');}
function removeItem(path){path=decodeURIComponent(path);if(!confirm('Stop managing this path?\n'+path))return;act(()=>api('/api/remove',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})}),'Removed');}
function forget(id){if(!confirm('Forget this file from the index? (the file on disk is untouched)'))return;act(()=>api('/api/forget',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({local_file_id:id})}),'Forgotten');}

// --- picker ---
function openPicker(){document.getElementById('picker').classList.add('open');browse(null);}
function closePicker(){document.getElementById('picker').classList.remove('open');}
async function browse(path){
  let d;try{d=await api('/api/browse'+(path?('?path='+encodeURIComponent(path)):''));}catch(e){toast(e.message,'bad');return;}
  pickerParent=d.parent;
  document.getElementById('pickerPath').textContent=d.path;
  document.getElementById('upBtn').disabled=!d.parent;
  document.getElementById('pickerList').innerHTML=d.entries.length?d.entries.map(e=>{
    const ep=enc(e.path);
    return `<div class="entry">${e.is_dir
      ? `<span class="nm" onclick="browse(decodeURIComponent('${ep}'))">📁 ${esc(e.name)}</span><button class="sec tiny" onclick="addPath(decodeURIComponent('${ep}'),'folder')" title="Watch this folder">add</button>`
      : `<span class="nm">📄 ${esc(e.name)}</span><button class="sec tiny" onclick="addPath(decodeURIComponent('${ep}'),'file')" title="Watch this file">add</button>`}</div>`;}).join('')
    :'<p class="muted" style="padding:0 1rem">Empty (no subfolders or PDFs).</p>';
}
function pMode(){const r=document.querySelector('input[name=pMode]:checked');return r?r.value:'monitored';}
function savePMode(){try{localStorage.setItem('pMode',pMode());}catch(e){}}
function loadPMode(){let v='monitored';try{v=localStorage.getItem('pMode')||'monitored';}catch(e){}
  const r=document.querySelector(`input[name=pMode][value="${v}"]`)||document.querySelector('input[name=pMode][value="monitored"]');
  if(r)r.checked=true;}
function fSortField(){const r=document.querySelector('input[name=fSortField]:checked');return r?r.value:'file';}
function fSortDir(){const r=document.querySelector('input[name=fSortDir]:checked');return r?r.value:'asc';}
function saveSort(){try{localStorage.setItem('fSortField',fSortField());localStorage.setItem('fSortDir',fSortDir());}catch(e){}}
function loadSort(){let field='file',dir='asc';
  try{field=localStorage.getItem('fSortField')||'file';dir=localStorage.getItem('fSortDir')||'asc';}catch(e){}
  const fr=document.querySelector(`input[name=fSortField][value="${field}"]`)||document.querySelector('input[name=fSortField][value="file"]');
  if(fr)fr.checked=true;
  const dr=document.querySelector(`input[name=fSortDir][value="${dir}"]`)||document.querySelector('input[name=fSortDir][value="asc"]');
  if(dr)dr.checked=true;}
function pickerOpts(){return {action:document.getElementById('pAction').value,teleport_policy:document.getElementById('pPolicy').value,mode:pMode()};}
function addPath(path,kind){
  const o=pickerOpts();
  act(()=>api('/api/items',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path,kind,...o})}),
    r=>`Added ${kind}`).then(()=>{closePicker();show('folders');});
}
function addCurrentFolder(){addPath(document.getElementById('pickerPath').textContent,'folder');}

// --- files / requests ---
function sync(){return api('/api/sync',{method:'POST'}).then(r=>`Indexed ${r.indexed}, applied ${r.actions_applied}, removed ${r.removed}`);}
function fileAct(id,a,msg){act(()=>api(`/api/requests/${id}/${a}`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'}),msg);}
function reqAct(id,a,msg){act(()=>api(`/api/requests/${id}/${a}`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'}),msg);}
function rejectForever(id){if(!confirm('Reject and block all future requests for this file?'))return;
  act(()=>api(`/api/requests/${id}/reject`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({forever:true})}),'Blocked');}

loadPMode();
loadSort();
refresh();
setInterval(refresh,15000);
</script></body></html>"""
