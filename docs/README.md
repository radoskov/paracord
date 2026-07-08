# Documentation

This folder contains implementation notes, runbooks, diagrams, agent handoffs, and LaTeX source for the PaRacORD manual.

## Contents

```text
latex/                  LaTeX implementation manual source
runbooks/               Operational guides
architecture/           Architecture notes and diagrams
agent_handoffs/         Required task handoff notes from coding agents
compile_docs.sh         Compiles the LaTeX manual
```

## Planning & status docs

- [`../SPECIFICATION.md`](../SPECIFICATION.md) — the agreed feature set (destination doc; mostly built).
- [`WORKPLAN.md`](WORKPLAN.md) — the single forward-looking backlog (pending work + unresolved discussions).
- [`WORKPLAN_ARCHIVE.md`](WORKPLAN_ARCHIVE.md) — completed workplan history.
- [`AUDIT.md`](AUDIT.md) — the merged register of known technical issues (open + resolved).
- [`../PROGRESS.md`](../PROGRESS.md) — running completion log with commit hashes.
- [`../ROADMAP.md`](../ROADMAP.md) — milestone-ordered mirror of `SPECIFICATION.md` §20.

(The per-round workplans, feature-design briefs, and pre-merge audit sources were consolidated into
the above on 2026-07-08 and archived to the gitignored `documentation_archive.zip`.)

## Runbooks

Start here:

```bash
make init                 # first-time environment setup
make up                   # start development stack
make migrate              # apply migrations
make bootstrap-admin      # create first owner
make ready                # run local autofix + pre-commit + Docker checks before pushing
```

Consolidated runbooks (the former nine were merged 2026-07-08):

- `development.md` — contributor workflow + Docker Compose service/command reference (was
  `development_setup.md` + `dev_containers.md`).
- `features.md` — per-feature how-tos: theming, AI providers, local agent, teleport (was
  `theming.md` + `ai_providers.md` + `local_agent.md` + `teleport.md`).
- `operations.md` — backup/restore + credential recovery (was `backup_restore.md` +
  `credential_recovery.md`).
- `secrets_management.md` — rules for credentials, `.env`, tokens, and secret scanning (kept
  standalone; referenced by CI and the secret scanner).
