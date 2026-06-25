# PaRacORD Frontend

Vite + Svelte web UI for the M1 library workflow.

## Docker workflow

Use the Compose service instead of installing Node dependencies on the host:

```bash
make frontend-dev
make frontend-build
```

The `frontend` service mounts source from `./frontend` and keeps dependencies in the
`paperracks_frontend_node_modules` Docker volume.

## Current screens

- Login.
- Library table and reading queue.
- Server-folder source import controls.
- Manual work creation.
- Shelf and rack creation plus membership views.
- Tag creation and tag assignment.
- File list with quick preview text.

## Still planned

- PDF.js reader integration.
- Citation graph and citation context panels.
- Duplicate/version review queue.
- Export dialog implementation.
- Audit-log admin view.
- AI summaries and topic map.
