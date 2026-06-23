# Handoff: secrets policy and automated enforcement

## Files changed

- `docs/runbooks/secrets_management.md` (new) — authoritative secrets/credential policy.
- `scripts/check_secrets.py` (new) — dependency-free secret scanner (staged/`--all`/path modes).
- `scripts/install_git_hooks.sh` (new) — installs a plain git pre-commit hook for the scan.
- `.pre-commit-config.yaml` (new) — local scanner + optional gitleaks.
- `.github/workflows/secret-scan.yml` (new) — runs `check_secrets.py --all` on push/PR.
- `.gitignore` — exclude `*.pem`, `*.key`, `*.p12/.pfx`, `*.token`, `secrets/`, and `.env.*` (keep `.env.example`).
- `docker-compose.yml` — Postgres credentials now read from `.env` instead of a hardcoded value.
- `SECURITY.md`, `AGENTS.md`, `HINTS_FOR_AGENTS.md`, `CONTRIBUTING.md`, `README.md`,
  `docs/latex/chapters/02_security.tex` — policy summary + enforcement pointers.
- `CHANGELOG.md`, `PROGRESS.md` — recorded the change.

## Assumptions made

- "Encrypt usernames and passwords" is satisfied for passwords by the existing bcrypt
  **hashing** (one-way, stronger than reversible encryption); other recoverable sensitive
  fields are to be encrypted at rest with a key from the environment. Documented as such.
- The scanner uses heuristics: clearly-fake values (`example`, `change_me`, `dev`, …),
  env indirection (`os.environ`, `*_env`, `${...}`), and `# pragma: allowlist secret` are
  treated as safe; high-confidence provider/key patterns are always reported.
- `docker-compose.yml` now requires a local `.env` (already in the README first-run steps).

## Tests added or skipped

- No unit test added for the scanner (it is tooling, not app code). Verified manually:
  passes clean on the full repo (`make check-secrets`) and correctly flags planted
  OpenAI/AWS keys and hardcoded password / API-key assignments while ignoring placeholders,
  env indirection, and pragma-marked lines.

## Security implications

- Real credentials can no longer be committed without tripping the local hook and CI.
- Only personal data remaining in the repo is the git author name/email in commit metadata
  (required to commit/push), confirmed by audit.

## Next recommended task

- Add `PAPERRACKS_SECRET_KEY` loading to `backend/app/core/config.py` (referenced by
  `server.example.yaml` as `secret_key_env`) and use it to sign sessions.
- Optionally add the secret scan to `make test`/CI alongside lint once CI is established.
