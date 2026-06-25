# PaRacORD Backend

FastAPI backend for PaRacORD.

## Responsibilities

- Authentication and authorization.
- Audit logging.
- Work/version/file/shelf/rack/tag data model.
- Agent registration and manifests.
- Teleport upload receiver.
- Import pipeline orchestration.
- GROBID extraction job management.
- Metadata enrichment and provenance.
- Citation graph and citation context APIs.
- Export APIs.
- Local AI and topic modeling job orchestration.

## First backend milestone

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/v1/health
```

Server-console account commands run from the repository root:

```bash
python scripts/bootstrap_admin.py
python scripts/reset_admin_password.py
```

They require database access and intentionally do not expose web-based credential recovery.
