# PaRacORD Local Agent

The local agent runs on a workstation that owns local PDFs (it can be a different machine from
the server). It indexes managed folders/files, reports a manifest of **opaque file IDs** (content
hashes) to the server, and applies a per-item *import action*. The server never learns a
filesystem path on your machine — it addresses files only by `local_file_id`, and the agent
resolves an ID back to a real path **locally only**.

There is a single, persistent agent per machine with a tool-managed config
(`~/.config/paracord/agent.yaml`) and a small SQLite state index. You drive it from this CLI or
from a **local-only web GUI** (`paracord-agent web up`); server-side management (approve the
agent, set its privileges, browse its files, request a teleport) lives in the server UI under
Admin → Agents.

## Install

```bash
cd agent
python -m pip install -e .          # provides the `paracord-agent` command
# optional: OS keyring for secret storage (else a 0600 file is used)
python -m pip install -e ".[keyring]"
```

## Enroll & run

1. In the server UI, **Admin → Agents → Issue enrollment token**.
2. Enroll this agent with that token (one-time):
   ```bash
   paracord-agent enroll --server http://SERVER:8000 --token <ENROLLMENT_TOKEN> --name my-workstation
   ```
3. The owner **approves** it in Admin → Agents, sets its privileges, and copies the agent bearer
   token (shown once):
   ```bash
   paracord-agent set-token <AGENT_TOKEN>
   ```
4. Add what to index and start monitoring:
   ```bash
   paracord-agent add-folder ~/papers --action index_only
   paracord-agent start          # monitor + sync on the refresh interval (Ctrl-C to stop)
   ```

## Import actions

Each managed folder/file carries an **action** (default `index_only`) and a **teleport policy**
(default `ask`):

- `index_only` — only the opaque reference + metadata reach the server; the PDF stays local.
- `index_and_extract` — the PDF is uploaded for extraction, then **discarded** server-side; only
  the reference, extracted metadata, and a short preview are kept (it can be re-teleported later).
- `teleport` — the PDF is uploaded and kept in the server's managed library.

Teleport policy controls server-initiated requests: `ask` requires explicit approval (CLI/GUI),
`allow` auto-fulfils them. A `reject --forever` blocks all future requests for a file until
`unblock`.

## Commands

```bash
paracord-agent enroll --token <T> --server <URL> --name <NAME>   # one-time enrollment
paracord-agent set-token <AGENT_TOKEN>                           # store approved bearer token
paracord-agent set-server <URL>                                  # change the server URL
paracord-agent add-folder <PATH> [--once] [--action ...] [--teleport ask|allow]
paracord-agent add-file <PATH> [--action ...] [--teleport ask|allow]
paracord-agent remove <PATH>                                     # stop managing an item
paracord-agent list                                              # show managed items + defaults
paracord-agent status                                            # connection, approval, privileges, counts
paracord-agent sync | refresh                                    # scan + manifest + apply actions once
paracord-agent start [--interval N]                              # run continuously: monitor + sync
paracord-agent request --list | --approve <ID> | --reject [--forever] <ID> | --unblock <ID>
paracord-agent web up | down | status                            # local-only web GUI
```

## Web GUI

`paracord-agent web up` starts a self-served page bound to `127.0.0.1` (default port `8765`,
configurable via the config's `web_port` or `--port`) and prints a one-time access URL containing
a token. Every request is token-gated; the GUI never listens off-host. Stop it with
`paracord-agent web down`.

The page is a tabbed layout (**Connection / Folders &amp; files / Indexed / Requests**) so a long
file list scrolls without stretching the window:

- **Connection** — set/change the server, enroll, and save the agent token after approval.
- **Folders &amp; files** — add items via a **file/folder picker** (browses the agent's own
  filesystem; pasting a path still works), edit each item's action / teleport policy / monitored↔once
  mode in place, **pause/resume** monitoring, and see per-folder stats (PDFs + subfolders found).
- **Indexed** — `Sync now`, per-file processing state, re-extract/unblock, and **forget** (drops the
  index row; the file on disk is untouched).
- **Requests** — approve / reject / reject-forever pending teleport requests.

Every action shows a success/error toast, so a failed call is visible rather than silent.

## Security boundary

The agent never exposes arbitrary filesystem paths. The server addresses files only by
`local_file_id`, and the agent refuses any ID it has not indexed within its managed items. Secrets
(the server bearer token, the web access token) live in the OS keyring when available, otherwise
in a `0600` file. The web GUI is bound to loopback and token-gated.
