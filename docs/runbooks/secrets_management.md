# Secrets and Credential Handling Policy

This is the **authoritative policy** for how PaRacORD handles configuration, secrets,
and credentials. It applies to every human contributor and every coding agent. The rule
of thumb is simple:

> **Nothing that can be used to authenticate, authorize, or impersonate may ever be
> committed to git.** Only non-secret configuration and *clearly fake* placeholders are
> allowed in the repository.

The only personal data intentionally present in this repository is the **git author
name and email** in commit metadata, which is required to commit and push. Nothing else
personal or secret should ever land in version control.

Enforcement is automated — see [Enforcement](#enforcement). Do not rely on review alone.

---

## 1. Classification

Classify every value before deciding where it lives.

### Tier 0 — Non-secret configuration ("light" items)
Examples: URLs, hostnames, IP addresses, ports, feature flags, timeouts, model names,
log levels, allowed roots.

- **Where:** environment variables, loaded from a local `.env` file (gitignored) and/or a
  local YAML overlay (`config/*.local.yaml`, gitignored).
- **In the repo:** committed only as **example files** with placeholder values
  (`.env.example`, `config/*.example.yaml`). Never commit the real `.env` or `*.local.yaml`.
- These are *not secrets*, but routing them through `.env` keeps environments reproducible
  and keeps Tier 1/2 values out of source entirely.

### Tier 1 — Service secrets ("serious" machine credentials)
Examples: database passwords, `PARACORD_SECRET_KEY`, agent bootstrap/scoped tokens,
Redis auth, third-party API keys, signing keys.

- **Where:** injected at runtime from the environment (`.env` locally; a real secret
  store — systemd `EnvironmentFile` with `0600` perms, Docker/Compose secrets, or a
  vault — in production). The code reads them via `os.environ` / pydantic-settings
  (`backend/app/core/config.py`).
- **Never** hardcode a real value in source, YAML, `docker-compose.yml`, Dockerfiles,
  CI files, tests, fixtures, logs, or error messages.
- Reference secrets indirectly. The server YAML already does this correctly with
  `secret_key_env: PARACORD_SECRET_KEY` and `*_url_env` keys — point at the env var
  name, do not inline the value.
- Rotate on exposure. If a real secret is ever committed, treat it as compromised:
  rotate it **and** purge it from history (see [If a secret leaks](#if-a-secret-leaks)).

### Tier 2 — User account credentials ("serious", highest sensitivity)
Examples: user passwords, anything that authenticates a human.

- **Passwords are hashed, never stored reversibly.** Use the project hasher
  (`backend/app/core/security.py` → `hash_password` / `verify_password`, bcrypt). Do not
  invent new password handling, do not log passwords, do not return them in API
  responses, do not write them to audit `details`.
  - *Note on terminology:* a password must **never** be stored in a form it can be
    decrypted back from. Hashing (one-way, salted, bcrypt) is the correct mechanism and
    is stronger than encryption for this purpose. "Encrypt the password" is satisfied —
    and exceeded — by hashing it.
- **Other sensitive stored fields that must remain recoverable** (e.g. a stored
  third-party credential the app must replay on the user's behalf) must be **encrypted at
  rest** with a key supplied via Tier 1 (`PARACORD_SECRET_KEY` or a dedicated data key),
  never with a key committed to the repo.
- Usernames and emails are personal data: keep them in the database, never in source,
  fixtures, logs shipped to git, or example files (use `user@example.com`-style fakes).

---

## 2. Decision table

| You have a…                         | Tier | Goes in                                  | Committed form                     |
|-------------------------------------|------|------------------------------------------|------------------------------------|
| Service URL / host / IP / port      | 0    | `.env` / `config/*.local.yaml`           | placeholder in `*.example.*`       |
| Feature flag / model name / timeout | 0    | `.env` / `config/*.local.yaml`           | safe default or placeholder        |
| DB password / API key / secret key  | 1    | env / secret store (`*_env` indirection) | **never** — only the env-var name  |
| Agent bootstrap / scoped token      | 1    | env / secret store; token file `0600`    | **never**                          |
| User password                       | 2    | DB, **bcrypt-hashed**                     | **never**                          |
| Recoverable stored credential       | 2    | DB, **encrypted at rest**                | **never**                          |
| Dummy value for a test/example      | n/a  | inline, **clearly fake**                 | allowed (see §3)                   |

---

## 3. Placeholders, dummies, and test values

Fake values are allowed and encouraged in examples and tests, but they must be
**unmistakably fake** so the scanner and human reviewers can tell them apart from real
secrets. Use one of:

- `change_me`, `changeme`, `example`, `placeholder`, `dummy`, `fake`, `sample`
- `your_value_here`, `<your-token>`, `${ENV_VAR}`, `xxxxxxxx`
- the documented dev value `paperracks_dev_password` (local development only)

If a test genuinely needs a realistic-looking value that the scanner would flag, append
the inline marker on that line:

```python
api_key = "sk-test-1234567890abcdef"  # pragma: allowlist secret
```

Use the marker sparingly and only for values that are provably not real.

---

## 4. Rules for coding agents (and humans)

1. **Never write a real credential into any file that git tracks.** This includes source,
   YAML, `docker-compose.yml`, Dockerfiles, CI, tests, fixtures, notebooks, and docs.
2. **Tier 0 light config goes through `.env` / `*.local.yaml`.** Add a placeholder to the
   matching `*.example.*` file in the same change — see the "Definition of done" in
   `AGENTS.md`.
3. **Tier 1/2 secrets are read from the environment**, referenced by env-var name in YAML
   (`*_env` keys), never inlined.
4. **User passwords use `hash_password` / `verify_password` only.** No plaintext, no
   reversible encoding, no logging.
5. **Recoverable sensitive data is encrypted at rest** with a key from the environment.
6. **Never log or echo secrets**, and never put them in audit-event `details`, exception
   messages, or API responses.
7. **New config keys ship with an example + a docs note**, and secret keys ship as an
   `*_env` indirection, not a value.
8. **Run the secret scan before committing** (the pre-commit hook does this for you).
9. **If unsure whether something is a secret, treat it as one** and ask in the PR.

---

## 5. Enforcement

These guardrails run automatically so the policy cannot be quietly broken:

- **`.gitignore`** excludes `.env`, `config/*.local.yaml`, `*.sqlite3`, `*.db`,
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, and `secrets/` so real secret files cannot be
  staged by accident.
- **`scripts/check_secrets.py`** scans for private keys, cloud/provider tokens, and
  generic `password=`/`secret=`/`token=` assignments with non-placeholder values. It
  treats clearly-fake values as safe and honors `# pragma: allowlist secret`.
  - Scan staged files:  `python scripts/check_secrets.py`
  - Scan everything:    `python scripts/check_secrets.py --all`  (also `make check-secrets`)
- **Pre-commit hook** — install once and the scan runs on every commit:
  - Plain git hook (no extra deps):  `bash scripts/install_git_hooks.sh`
  - Or via the `pre-commit` framework using `.pre-commit-config.yaml`:  `pre-commit install`
- **CI** — `.github/workflows/secret-scan.yml` runs `check_secrets.py --all` on every
  push and pull request, so a leak is blocked even if a local hook was skipped.

## If a secret leaks

1. **Rotate the credential immediately** — assume it is compromised the moment it is
   committed, even if the commit is not yet pushed.
2. **Purge it from history** (single unpushed commit: `git commit --amend`; deeper:
   `git filter-repo` or BFG), then force-update any remote.
3. Note the incident in `CHANGELOG.md` under Security and in your agent handoff.
