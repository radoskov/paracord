# API Surface

All endpoints are under `/api/v1`. This reflects the **implemented** routes as of the M7 audit
(commit ~`0429b14`), generated from the live OpenAPI schema. For the full *intended* surface see
`SPECIFICATION.md` §10; for gaps between the two see `docs/AUDIT.md`.

Auth: `/health` and `/agents/enroll-request` are public; everything else requires a bearer session
(`require_authenticated_user`). `/admin/*` requires owner; write routes require owner/editor
(`require_roles`). `/agents/manifest` and `/agents/teleport` are currently **unauthenticated stubs**
— see AUDIT.md H4.

```text
# Auth
POST   /auth/login
POST   /auth/logout

# Admin (owner only)
GET    /admin/users
POST   /admin/users
PATCH  /admin/users/{user_id}
POST   /admin/users/{user_id}/disable
GET    /admin/audit-events
POST   /admin/agents/enroll-token
GET    /admin/agents
POST   /admin/agents/{agent_id}/approve

# Local agent
POST   /agents/enroll-request           # public; owner-issued enrollment token
POST   /agents/register                 # stub -> NotImplementedError (legacy)
POST   /agents/manifest                 # stub (unauthenticated; see AUDIT H4)
POST   /agents/teleport/{agent_file_id} # stub (unauthenticated; see AUDIT H4)

# Import / sources
GET    /sources
POST   /sources/server-folder           # only ingestion source type implemented
POST   /imports/folder
POST   /imports/bibtex
GET    /imports/{batch_id}

# Library
GET,POST   /works
GET,PATCH  /works/{work_id}
GET    /works/{work_id}/metadata               # provenance/conflict review (no UI yet)
POST   /works/{work_id}/metadata/select        # pick canonical assertion (no UI yet)
POST   /works/{work_id}/enrich                 # (no UI button yet)
GET,POST   /works/{work_id}/annotations
GET    /works/{work_id}/citation-contexts
GET,POST   /works/{work_id}/summaries          # M7 tier 0/1
GET,POST   /shelves
PATCH  /shelves/{shelf_id}
GET,POST   /shelves/{shelf_id}/works
DELETE /shelves/{shelf_id}/works/{work_id}
GET,POST   /racks
PATCH  /racks/{rack_id}
GET,POST   /racks/{rack_id}/shelves
DELETE /racks/{rack_id}/shelves/{shelf_id}
GET,POST   /tags
POST,DELETE /tags/{tag_id}/links

# Files
GET    /files
GET    /files/{file_id}
GET    /files/{file_id}/stream                 # auth'd PDF stream, root-escape guarded
POST   /files/{file_id}/extract                # enqueue GROBID extraction

# Discovery / analysis
POST   /search/semantic                        # lexical hashing embedder (see AUDIT)
GET    /duplicates
POST   /duplicates/scan
PATCH  /duplicates/{candidate_id}              # apply review action
POST   /graphs/citation                        # scoped node/edge graph
GET    /citations/contexts                      # DEAD stub -> {"status":"todo"} (remove)

# Export & local AI
POST   /exports                                 # bibtex/biblatex/ris/csl-json/markdown/html/text
POST   /ai/summaries                            # stub -> {"status":"todo"} (shelf/rack summaries TODO)
POST   /ai/topics                               # TF-IDF + k-means topic model

# Health
GET    /health
```

Endpoints use typed Pydantic request/response schemas and raise `HTTPException` with 400/403/404.
Notable **unimplemented** spec routes: `/auth/me`, `/auth/change-password`,
`/sources/{url,arxiv,doi}`, `/search/advanced`, `/search/suggestions`, `/exports/preview`,
`/graphs/work/{id}/neighborhood`, `/shelves|racks/{id}/citation-summary`, `/admin/backup`,
`/admin/restore/dry-run`. See `docs/AUDIT.md` §2.
