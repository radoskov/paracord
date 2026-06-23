# Security Model

PaperRacks is intended for authenticated local-network use. It is not designed to be exposed directly to the public internet without additional hardening.

## Non-negotiable requirements

- No guest or anonymous read-only access.
- No unauthenticated PDF viewing.
- No unauthenticated citation export.
- No browser access to arbitrary filesystem paths.
- No server request that can ask an agent for an arbitrary raw path.
- GROBID, Redis, PostgreSQL, Ollama, and internal worker services should not be directly exposed to the LAN.
- Credential recovery must be possible from the server PC, but not through an unauthenticated remote web endpoint.

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
6. Write an audit event with recovery method `server_console`.

Do not add a web endpoint for unauthenticated password reset unless a future security design is explicitly approved.

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
