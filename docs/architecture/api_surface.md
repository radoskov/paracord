# API Surface Draft

All endpoints are under `/api/v1`.

```text
/auth/login
/auth/logout
/agents/register
/agents/manifest
/agents/teleport/{agent_file_id}
/imports
/files/{file_id}/pdf
/works
/shelves
/racks
/citations/contexts
/graph
/exports
/ai/summaries
/ai/topics
/health
```

The final API should use typed request/response schemas and explicit error codes.
