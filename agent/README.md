# PaRacORD Local Agent

The local agent runs on the machine that owns local PDFs. It scans configured roots, computes file manifests, and communicates with the server using opaque file IDs. It can upload selected PDFs to the server's managed library store through teleport.

## Security boundary

The agent must not expose arbitrary filesystem paths. The server can request only known `local_file_id` values from the agent.

## Planned commands

```bash
paracord-agent register
paracord-agent scan
paracord-agent sync
paracord-agent teleport <local-file-id>
paracord-agent serve
```
