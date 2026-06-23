# PaperRacks

PaperRacks is a local-first, self-hostable scientific-paper library and literature-graph system for Linux. It is designed around a central server, one or more local workstation agents, GROBID-based extraction, shelves/racks/tags organization, citation-context graphs, citation export, local summaries, and topic modeling.

This repository is an implementation scaffold. It contains the intended project structure, core interfaces, placeholder services, documentation sources, and agent-oriented work packages. It is not yet a production application.

## Core goals

- Keep PDFs in their original folders by default.
- Allow selected PDFs to be teleported to a managed server-side library store.
- Support an external server on the local network plus an on-PC local agent.
- Prevent arbitrary filesystem browsing from the browser or server.
- Extract metadata, full text, references, citation mentions, citation contexts, and PDF coordinates.
- Organize works into shelves and shelves into racks, with many-to-many membership.
- Provide local citation graphs and citation summaries scoped to the full library, rack, shelf, or search result.
- Export citations for papers, shelves, racks, and selections in BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, and free-text bibliography formats.
- Provide local AI summaries, user/external summaries, keyword suggestions, topic modeling, and semantic search.
- Require authenticated access. There is intentionally no guest/read-only anonymous mode.
- Provide server-local credential recovery through a command-line tool, not through an unauthenticated web endpoint.

## Repository layout

```text
backend/                 FastAPI backend scaffold
agent/                   Local workstation agent scaffold
frontend/                Web UI scaffold
config/                  Example server and agent configuration
docs/                    Markdown docs, LaTeX manual sources, runbooks, diagrams
scripts/                 Operational helper scripts
SPECIFICATION.md         Full implementation specification
CHANGELOG.md             Project changelog
PROGRESS.md              Current build status and next steps
AGENTS.md                Coding-agent coordination guide
WORK_SPLIT.md            Suggested work packages for parallel agents
HINTS_FOR_AGENTS.md      Practical implementation hints and constraints
```

## Suggested first run for developers

```bash
cp config/server.example.yaml config/server.local.yaml
cp config/agent.example.yaml config/agent.local.yaml
cp .env.example .env
make dev-up
make backend-dev
```

The current scaffold will not implement all endpoints yet. The goal of the first milestone is to make `GET /api/v1/health` work, initialize the database, create an admin user, register a local agent, scan a folder, and import one PDF into the processing queue.

## Important security assumption

The server and web browser must never receive arbitrary local filesystem access. Files are accessed only through configured roots, managed library objects, or local-agent file IDs. The agent refuses raw path requests and exposes only files that it has indexed from its configured roots.

## Documentation

The implementation manual source lives in `docs/latex/`. To compile it:

```bash
cd docs
./compile_docs.sh
```

The script uses `latexmk` when available and falls back to repeated `pdflatex` runs.
