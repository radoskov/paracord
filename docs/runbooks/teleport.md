# Teleport Runbook

Teleport means copying a PDF from a workstation/agent or server allowed root into the server managed-library store.

## Required checks

1. User is authenticated and authorized.
2. File ID is known to the server.
3. Agent confirms the file is available and inside an allowed root.
4. Server receives file in chunks.
5. Server computes SHA-256.
6. Server compares computed hash to manifest hash.
7. Server writes the file to content-addressed storage.
8. Server creates or updates File, Location, and ImportBatch records.
9. Audit event is written.

Teleport must not delete the original file unless a future explicit feature is added.
