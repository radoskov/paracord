# Contributing

PaRacORD is currently scaffolded for multi-agent implementation. Contributions should follow the work packages in `WORK_SPLIT.md`.

## Development expectations

- Keep changes small and testable.
- Update `PROGRESS.md` after completing a milestone or vertical slice.
- Update docs when APIs, config, security behavior, or user-visible behavior changes.
- Add or update tests for new service logic.
- Keep security-sensitive code explicit and easy to review.

## Formatting

Backend and agent Python code should be formatted with Ruff/Black-compatible defaults. Frontend code should use the package-level formatter once the frontend toolchain is finalized.

## Secrets and credentials

Never commit real credentials, secrets, or personal data. Light config (URLs, IPs, ports) goes through `.env` / `config/*.local.yaml`; serious secrets are read from the environment; user passwords are bcrypt-hashed. The full, enforced policy is in [`docs/runbooks/secrets_management.md`](docs/runbooks/secrets_management.md). Install the local guard once with `bash scripts/install_git_hooks.sh` (or `pre-commit install`) and run `make check-secrets` before pushing.

## Pull request checklist

- [ ] Tests added or consciously deferred with reason.
- [ ] Security implications considered.
- [ ] No real credentials/secrets/personal data committed; `make check-secrets` passes.
- [ ] Config examples updated (placeholders only; secret keys referenced by env-var name).
- [ ] Docs updated.
- [ ] `PROGRESS.md` updated.
- [ ] Agent handoff note added under `docs/agent_handoffs/`.
