# Local Agent Runbook

The local agent scans only configured roots. It sends manifests to the server and can teleport selected PDFs to the managed server library.

## Registration flow

1. Owner creates an agent bootstrap token on the server.
2. Workstation runs `paracord-agent register`.
3. Server returns an agent ID and token.
4. Agent stores token in a user-readable-only token file.
5. Future requests use the scoped token.

## File access flow

- Server may request a known `local_file_id`.
- Agent resolves the ID through its local index.
- Agent verifies the file is still inside an allowed root.
- Agent streams or uploads the file.
- Agent refuses raw path requests.
