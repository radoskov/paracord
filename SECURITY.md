# Security Model

PaRacORD is intended for authenticated local-network use. It is not designed to be exposed directly to the public internet without additional hardening.

## Non-negotiable requirements

- No guest or anonymous read-only access.
- No unauthenticated PDF viewing.
- No unauthenticated citation export.
- No browser access to arbitrary filesystem paths.
- No server request that can ask an agent for an arbitrary raw path.
- GROBID, Redis, PostgreSQL, Ollama, and internal worker services should not be directly exposed to the LAN.
- Credential recovery must be possible from the server PC, but not through an unauthenticated remote web endpoint.
- No real credential, secret, or personal data may ever be committed to git. See [Secrets and credential handling](#secrets-and-credential-handling).

## Secrets and credential handling

The full policy lives in [`docs/runbooks/secrets_management.md`](docs/runbooks/secrets_management.md) and is **automatically enforced** (see [Enforcement](#enforcement)). Summary:

- **Light, non-secret config** (URLs, hostnames, IP addresses, ports, flags) is provided through environment variables loaded from a local `.env` file and/or `config/*.local.yaml`. Only `*.example` files with placeholder values are committed.
- **Serious machine secrets** (database passwords, `PARACORD_SECRET_KEY`, agent tokens, API keys) are read from the environment / a secret store and referenced in YAML by env-var name (the `*_env` keys), never inlined. They are never committed in any form.
- **User passwords** are stored only as bcrypt hashes via `hash_password` / `verify_password` in `backend/app/core/security.py` — one-way, never reversibly encoded, never logged. **Bearer tokens** (user sessions, agent access, enrollment) are likewise stored only as SHA-256 hashes, never in plaintext.
- **At rest:** the database holds bibliographic data plus the one-way hashes above; there are currently **no reversibly-encrypted application fields**, so at-rest confidentiality of the corpus relies on the operator's disk/volume encryption (documented in the deployment runbook). `PARACORD_SECRET_KEY` is read from the environment and **reserved** for future field-level encryption (Fernet); when unset, the app runs without field encryption rather than failing.
- **Personal data** (usernames, emails) lives in the database, never in source, fixtures, logs, or examples. The only personal data in the repository is the git author name/email in commit metadata.
- *Clearly fake* placeholders and test values are allowed; mark unavoidable realistic test values with `# pragma: allowlist secret`.

### Enforcement

- `.gitignore` excludes `.env`, `config/*.local.yaml`, databases, and key material (`*.pem`, `*.key`, `secrets/`, …).
- `scripts/check_secrets.py` scans for private keys, provider tokens, and hardcoded `password`/`secret`/`token` values (`make check-secrets`).
- A pre-commit hook runs the scan locally (`bash scripts/install_git_hooks.sh`, or `pre-commit install`).
- `.github/workflows/secret-scan.yml` runs the scan on every push and pull request.

## Roles

```text
owner   full control, credential recovery visibility, user management
editor  library editing, import, metadata changes, annotations, exports
reader  authenticated reading/searching/export where enabled by owner policy
```

There is no `guest` role.

## Credential recovery

Credential recovery is performed by a local operator on the server PC using `scripts/reset_admin_password.py`. The script should:

1. Run only on the server environment with DB access.
2. Require the operator to identify the target account.
3. Prompt for a new password without echo.
4. Hash the password using the same production password hasher.
5. Invalidate existing sessions/tokens for the account.
6. Write an `auth.password_reset_cli` audit event with recovery method `server_console`.

Do not add a web endpoint for unauthenticated password reset unless a future security design is explicitly approved.

## Data egress and privacy

PaRacORD is local-first and built only from open-source, auditable components (GROBID, PostgreSQL, Redis, PDF.js, Ollama, BERTopic, and the supporting tools listed in `SPECIFICATION.md` §5.3). None is a closed binary that could silently scan the host or exfiltrate data.

- No component reads outside its configured roots (server roots, managed store, agent roots). There is no host-wide scanning.
- **OCR is a local subprocess with no egress.** The default `ocr_backend=ocrmypdf` runs the bundled `ocrmypdf`/Tesseract/Ghostscript tools as a bounded local subprocess on a stored PDF, writing a searchable copy to a transient scratch path that is fed to GROBID and then discarded. It makes no network calls and never transmits PDF contents. The opt-in full-ML extractors (Nougat/Marker) run locally too and are built only via `make build-ml-extraction` — never installed at runtime.
- The only outbound traffic is **opt-in metadata enrichment and GROBID consolidation**, and it carries only **bibliographic identifiers** — titles, authors, DOIs, arXiv IDs, and raw reference strings.
- The system never transmits PDF contents, full text, annotations, notes, your shelf/rack/collection structure, filesystem paths, or any bulk export of your library to a third party.
- Every external request is recorded as a `metadata.enrichment_called` audit event, so egress is fully visible to the owner.
- Outbound enrichment requests are **SSRF-hardened**: identifiers are percent-encoded into the URL (never able to alter the target) and redirects that leave the API's own host are refused, so a crafted DOI/arXiv id or a hostile upstream cannot pivot the request to a link-local or metadata endpoint.
- Enrichment is configurable per service and can be disabled entirely; consolidation can be pointed at a self-hosted biblio-glutton instance for zero third-party calls.

See also [Secrets and credential handling](#secrets-and-credential-handling).

## Local agent boundary

The local agent owns filesystem access for its machine. It may expose only configured roots and indexed file IDs. It must reject:

- path traversal attempts,
- symlink escapes by default,
- requests for unknown file IDs,
- requests not signed/authenticated with the server token,
- direct raw-path file reads from the server.

## Audit events

Audit the following at minimum:

- login success,
- login failure,
- logout,
- password change,
- server-console credential recovery,
- agent registration,
- agent token rotation,
- folder scan,
- teleport upload,
- PDF view/open,
- citation export,
- metadata edit,
- work/file deletion,
- duplicate merge,
- local AI summary generation,
- external metadata lookup.
