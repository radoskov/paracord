# PaRacORD Local Agent

The local agent runs on a workstation that owns local PDFs (it can be a different machine from
the server). It indexes configured folders, reports a manifest of **opaque file IDs** (content
hashes) to the server, and — when you request a teleport from the server UI — pushes the bytes to
the server's managed library. The server never learns a filesystem path on your machine, and the
agent only ever acts on IDs it has itself indexed.

There is **no separate agent GUI**: you manage the agent from the *server* UI (Admin → Agents:
approve it, browse its files, click Teleport) plus this CLI/daemon. Folders to index are listed in
the agent config file — edit it to add/remove folders.

## Install

```bash
cd agent
python -m pip install -e .          # provides the `paracord-agent` command
```

## Enroll & run

1. In the server UI, **Admin → Agents → Issue enrollment token**.
2. Enroll, then have the owner **approve** the agent to get its bearer token:
   ```bash
   paracord-agent sync --server http://SERVER:8000 --token <ENROLLMENT_OR_AGENT_TOKEN> ~/papers
   export PARACORD_AGENT_TOKEN=<AGENT_TOKEN>     # after approval
   ```
3. Run the daemon — it keeps the manifest fresh and auto-fulfils teleports requested in the UI:
   ```bash
   paracord-agent serve --config ~/.config/paracord/agent.yaml
   ```

Config file (see `config/agent.example.yaml`): `filesystem.allowed_roots` lists the folders to
index; `agent.server_url`, `agent.poll_interval_seconds`, and `agent.token_file` configure the
rest. CLI flags (`--server`, `--token`, positional roots) override the config.

## Commands

```bash
paracord-agent scan [folders...]      # list indexed PDFs locally (no server)
paracord-agent sync [folders...]      # send the manifest to the server once
paracord-agent teleport [folders...]  # upload any files the server has requested, once
paracord-agent serve                  # run continuously: sync + auto-teleport
```

## Security boundary

The agent never exposes arbitrary filesystem paths. The server addresses files only by
`local_file_id`, and the agent refuses any ID it has not indexed within its configured roots.
