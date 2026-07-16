# PaRacORD — Comprehensive Software Documentation

> **PaRacORD** — **Pa**per **Rac**ks for **O**rganization, **R**etrieval, and **D**iscovery — is a
> local-first, self-hostable scientific-paper library and literature-graph system for Linux.

This documentation set is the **engineering reference** for the application as it exists in the
current codebase. It is written from the source of truth (the code under `backend/`, `agent/`,
`frontend/`), not from the older planning documents. Where the older docs
(`FILE_TREE.md`, parts of `SPECIFICATION.md`; `docs/architecture/*` was archived into the
gitignored `documentation_archive.zip` on 2026-07-13) have drifted from reality, this
set supersedes them — see [§11 Future work & revision notes](11_future_and_revision_notes.md) for a
list of stale documents.

_Generated 2026-07-12 against `main` @ `4115278`; re-verified 2026-07-16 against `main` @ `1ee92f0`.
When you change the code, update the matching section here._

---

## How to read this

| Doc | What it covers | Read it when you want to… |
|-----|----------------|---------------------------|
| [01 — Architecture](01_architecture.md) | Runtime topology, containers, request/data-flow overview, tech stack, build/run/test tooling | Understand the moving parts and how to run the system |
| [02 — Data model](02_data_model.md) | Every table/ORM model, relationships, ER + class diagrams, schema-design decisions | Add a column, write a migration, understand an entity |
| [03 — Backend services](03_backend_services.md) | All 71 service modules grouped into 9 clusters, key functions, algorithms, collaboration diagram | Change business logic or an algorithm |
| [04 — API surface](04_api_surface.md) | Every HTTP route, the auth dependency chain, request lifecycle, cross-cutting patterns | Add/modify an endpoint or a client call |
| [05 — Pipelines & workers](05_pipelines_workers.md) | The async ingestion pipeline (upload → GROBID → parse → dedup → embed), the RQ queue, OCR, recovery | Work on extraction, embeddings, or background jobs |
| [06 — Local agent](06_agent_protocol.md) | The workstation agent, manifest/teleport protocol, agent↔server API, security boundary | Work on the agent or remote-machine import |
| [07 — Frontend](07_frontend.md) | Svelte app shell, routing, component hierarchy, API client, theming, reader/graph/viz | Build UI or wire a new backend feature to the UI |
| [08 — Security](08_security.md) | AuthN/AuthZ, access-control model, file/SSRF/XXE boundaries, the safety test battery, secrets | Touch auth, access control, file access, or external egress |
| [09 — Efficiency](09_efficiency.md) | Computational-cost analysis, ranked hotspots, what is already optimized | Optimize performance or reason about scale |
| [10 — User workflows](10_user_workflows.md) | End-to-end user manual: first run, import, organize, search, dedup, graph, export, AI, reading | Learn or document how the product is used |
| [11 — Future & revision notes](11_future_and_revision_notes.md) | Consolidated revision register (tech/algorithmic/UX/security/robustness/stability) + expansion ideas | Plan hardening, refactors, or new features |

---

## One-paragraph orientation

PaRacORD is a **FastAPI + PostgreSQL(+pgvector) + Redis/RQ** backend, a **Svelte 5 + Vite**
single-page frontend, and an optional **Python workstation agent**. PDFs are ingested (uploaded,
imported from a server folder, pulled by identifier, or teleported from an agent), extracted with
**GROBID** into structured TEI, parsed into metadata / references / in-text citation mentions, then
enriched from arXiv/Crossref/OpenAlex/Semantic-Scholar, deduplicated, chunked, and embedded for
semantic search. Works are organized into **shelves** (which live in **racks**) and tagged; a
**Linux-style group/grant access-control model** governs who can see and modify each collection.
Analytical layers build **scoped citation graphs**, **citation/venue/author summaries**, **topic
models**, and **bibliographic exports** in ~11 formats. Everything requires authentication; there is
deliberately **no anonymous access**, and the system is designed to run either single-user on one
machine or for a handful of trusted users on a LAN.

## The single most important naming convention

The user-facing entity is called a **"paper"** in all UI text. In code and the database it is a
**`Work`** (`works` table, `/api/v1/works`, `WorkRead`, `entity_type="work"`). This split is
intentional and load-bearing — **do not rename code identifiers to "paper"**. See
[AGENTS.md](../../AGENTS.md) and [07 — Frontend §"paper" vs "work"](07_frontend.md).

## Key source locations

```text
backend/app/
  api/v1/endpoints/   27 HTTP endpoint modules      → 04_api_surface.md
  api/deps.py         auth/DI dependency chain       → 04, 08
  services/           71 service modules (business logic) → 03
  models/             26 SQLAlchemy models            → 02
  workers/            RQ jobs, queue, supervisor, recovery → 05
  core/               config.py, security.py          → 01, 08
  utils/              normalization, table_presence    → 03
backend/alembic/versions/ 10 migrations (0067 squashed baseline + 0068-0076) → 02
agent/paperracks_agent/  14 modules (local agent)     → 06
frontend/src/         Svelte app, ~120 files           → 07
```
