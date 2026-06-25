# Architecture Notes

```text
Browser UI
  -> PaRacORD Server API
     -> PostgreSQL + pgvector
     -> Redis/RQ workers
     -> GROBID service
     -> optional Ollama/local LLM
     -> optional metadata connectors
  -> PaRacORD Local Agent(s)
     -> configured roots only
     -> manifests
     -> teleport uploads
     -> optional PDF streaming
```

## Main boundaries

- Browser never sees host filesystem paths.
- Server never requests arbitrary paths from agents.
- Internal services are bound to localhost or Docker internal networks.
- Authentication is required for all library content.
