# PaperRacks Local Agent

The local agent runs on the machine that owns local PDFs. It scans configured roots, computes file manifests, and communicates with the server using opaque file IDs. It can upload selected PDFs to the server's managed library store through teleport.

## Security boundary

The agent must not expose arbitrary filesystem paths. The server can request only known `local_file_id` values from the agent.

## Planned commands

```bash
paperracks-agent register
paperracks-agent scan
paperracks-agent sync
paperracks-agent teleport <local-file-id>
paperracks-agent serve
```
