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
.pill.mono{font-family:ui-monospace,Menlo,monospace;cursor:help}
.paused{opacity:.55}
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
  <div class="title">PaRacORD Local Agent</div>
  <div class="conn" id="conn">…</div>
</header>
<nav>
  <button data-tab="conn" class="active" onclick="show('conn')">Connection</button>
  <button data-tab="folders" onclick="show('folders')">Folders &amp; files <span class="count" id="cFolders">0</span></button>
  <button data-tab="files" onclick="show('files')">Indexed <span class="count" id="cFiles">0</span></button>
  <button data-tab="reqs" onclick="show('reqs')">Requests <span class="count" id="cReqs">0</span></button>
</nav>
<main>
  <section class="tab active" data-tab="conn">
    <div class="card"><h2>Server connection</h2>
      <div id="status" class="muted">…</div>
      <div class="row" style="margin-top:.6rem">
        <input id="url" placeholder="http://server:8000" size="28">
        <button class="sec" onclick="act(connect,'Server URL saved')">Set server</button>
      </div>
    </div>
    <div class="card"><h2>Enrollment</h2>
      <p class="muted">Enroll, have the owner approve the agent, then paste the agent token they give you.</p>
      <div class="row" style="margin-top:.5rem">
        <input id="enrollTok" placeholder="enrollment token" size="20">
        <input id="agentName" placeholder="agent name" size="14">
        <button class="sec" onclick="act(enroll,'Enrolled — ask the owner to approve')">Enroll</button>
      </div>
      <div class="row" style="margin-top:.5rem">
        <input id="agentTok" placeholder="agent token (after approval)" size="26">
        <button class="sec" onclick="act(setToken,'Agent token saved')">Save token</button>
      </div>
    </div>
  </section>

  <section class="tab" data-tab="folders">
    <div class="card"><h2>Managed folders &amp; files</h2>
      <div class="row">
        <button onclick="openPicker()">+ Add folder or file…</button>
        <span class="muted">or paste a path:</span>
        <input id="path" placeholder="/home/me/papers" size="24">
        <button class="sec" onclick="addByPath()">Add</button>
      </div>
      <div id="items" style="margin-top:.7rem"></div>
    </div>
  </section>

  <section class="tab" data-tab="files">
    <div class="card"><h2>Indexed files
        <button class="sec tiny" onclick="act(sync)">Sync now</button>
        <button class="sec tiny" onclick="refresh()">Refresh</button></h2>
      <div id="files"></div>
    </div>
  </section>

  <section class="tab" data-tab="reqs">
    <div class="card"><h2>Teleport requests <button class="sec tiny" onclick="refresh()">Refresh</button></h2>
      <div id="reqs"></div>
    </div>
  </section>
</main>

<div class="modal" id="picker">
  <div class="box">
    <div class="head">
      <button class="sec tiny" onclick="browse(pickerParent)" id="upBtn">↑ Up</button>
      <code id="pickerPath"></code>
      <button class="sec tiny" onclick="closePicker()">✕</button>
    </div>
    <div class="body" id="pickerList"></div>
    <div class="foot">
      <label>action <select id="pAction"><option>index_only</option><option>index_and_extract</option><option>teleport</option></select></label>
      <label>teleport <select id="pPolicy"><option value="ask">ask</option><option value="allow">allow</option></select></label>
      <label>mode <select id="pMode"><option value="monitored">monitored</option><option value="once">once</option></select></label>
      <button onclick="addCurrentFolder()">Add this folder</button>
    </div>
  </div>
</div>

<div id="toasts"></div>
<script>
let pickerParent=null;
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

async function refresh(){
  let s;
  try{s=await api('/api/status');}catch(e){document.getElementById('conn').textContent='status error: '+e.message;return;}
  document.getElementById('conn').innerHTML = s.connected
    ? `<span class="ok">●</span> connected to ${s.server_url} — agent <b>${s.me.name}</b> [${s.me.status}]; can: ${Object.keys(s.me).filter(k=>k.startsWith('can_')&&s.me[k]).map(k=>k.slice(4)).join(', ')||'—'}`
    : `<span class="bad">●</span> not connected (${s.server_url}) ${s.error?'— '+s.error:''}`;
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
        `<td class="row"><button class="sec tiny" onclick="togglePause('${ep}',${i.enabled})">${i.enabled?'Pause':'Resume'}</button>`+
        `<button class="danger tiny" onclick="removeItem('${ep}')">Remove</button></td></tr>`;
    }).join('')+'</table>'
    : '<p class="muted">No managed items. Click “Add folder or file…”.</p>';
  // indexed files
  let files=[];try{files=await api('/api/files');}catch(e){toast('files: '+e.message,'bad');}
  document.getElementById('cFiles').textContent=files.length;
  document.getElementById('files').innerHTML = files.length? '<table><tr><th>file</th><th>id (hash)</th><th>action</th><th>state</th><th></th></tr>'+
    files.map(f=>`<tr><td>${esc(f.virtual_path||f.local_file_id.slice(0,10))}${f.present?'':' <span class="bad">(removed)</span>'}</td>`+
      `<td><span class="pill mono" title="${esc(f.local_file_id)}\n(this content hash is the cross-reference shown on the server)">#${esc(f.local_file_id.slice(0,12))}…</span></td>`+
      `<td><span class="pill">${f.action}</span></td><td>${f.processing_state}${f.blocked?' <span class="bad">blocked</span>':''}</td>`+
      `<td class="row"><button class="sec tiny" onclick="fileAct('${f.local_file_id}','reextract','Re-extract requested')">re-extract</button>`+
      (f.blocked?`<button class="sec tiny" onclick="fileAct('${f.local_file_id}','unblock','Unblocked')">unblock</button>`:'')+
      `<button class="danger tiny" onclick="forget('${f.local_file_id}')">forget</button></td></tr>`).join('')+'</table>'
    : '<p class="muted">No files indexed yet — add a folder, then “Sync now”.</p>';
  // requests
  let reqs=[];try{reqs=await api('/api/requests');}catch(e){/* visibility/connectivity */}
  document.getElementById('cReqs').textContent=reqs.length||0;
  document.getElementById('reqs').innerHTML = (reqs.length? '<table>'+reqs.map(r=>`<tr><td>${esc(r.display_path||r.local_file_id.slice(0,10))}</td>`+
    `<td class="row"><button class="tiny" onclick="reqAct('${r.local_file_id}','approve','Approved — pushing file')">approve</button>`+
    `<button class="sec tiny" onclick="reqAct('${r.local_file_id}','reject','Rejected')">reject</button>`+
    `<button class="danger tiny" onclick="rejectForever('${r.local_file_id}')">reject forever</button></td></tr>`).join('')+'</table>'
    : '<p class="muted">No pending requests.</p>');
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');}
// Encode a value for safe use as an onclick JS-string argument (apostrophe included). Decoded in the handler.
function enc(s){return encodeURIComponent(String(s)).replace(/'/g,'%27');}

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
      ? `<span class="nm" onclick="browse(decodeURIComponent('${ep}'))">📁 ${esc(e.name)}</span><button class="sec tiny" onclick="addPath(decodeURIComponent('${ep}'),'folder')">add</button>`
      : `<span class="nm">📄 ${esc(e.name)}</span><button class="sec tiny" onclick="addPath(decodeURIComponent('${ep}'),'file')">add</button>`}</div>`;}).join('')
    :'<p class="muted" style="padding:0 1rem">Empty (no subfolders or PDFs).</p>';
}
function pickerOpts(){return {action:document.getElementById('pAction').value,teleport_policy:document.getElementById('pPolicy').value,mode:document.getElementById('pMode').value};}
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

refresh();
setInterval(refresh,15000);
</script></body></html>"""
