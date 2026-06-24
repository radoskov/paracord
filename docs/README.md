# Documentation

This folder contains implementation notes, runbooks, diagrams, agent handoffs, and LaTeX source for the PaperRacks manual.

## Contents

```text
latex/                  LaTeX implementation manual source
runbooks/               Operational guides
architecture/           Architecture notes and diagrams
agent_handoffs/         Required task handoff notes from coding agents
compile_docs.sh         Compiles the LaTeX manual
```

## Runbooks

Start here:

```bash
make init                 # first-time environment setup
make up                   # start development stack
make migrate              # apply migrations
make bootstrap-admin      # create first owner
make ready                # run local autofix + pre-commit + Docker checks before pushing
```

Main runbooks:

- `development_setup.md` — normal contributor workflow.
- `dev_containers.md` — Docker Compose service and command reference.
- `credential_recovery.md` — server-local owner/admin recovery.
- `local_agent.md` — workstation agent behavior and safety rules.
- `teleport.md` — copying PDFs into managed server storage.
- `secrets_management.md` — rules for credentials, `.env`, tokens, and secret scanning.
