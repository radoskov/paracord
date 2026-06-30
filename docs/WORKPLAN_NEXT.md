# PaRacORD — Work Plan, next phase (2026-06-30)

Continues `docs/WORKPLAN.md` (Stages 1–7 done). It re-prioritizes the remaining roadmap around a
maintainer directive:

> **The heavier semantic-engine providers must be controllable from the server web interface — not
> opt-in in a config file.** An owner should be able to *choose* the embedding / summary / topic
> backend, *download* the models, and tune behavior **from the GUI**, with the lexical baselines as
> the always-available default.

Today those providers (`sentence_transformers`, `ollama`, `bertopic`, `local_llm`) are selected via
static `Settings` (env / YAML) and degrade to the baselines when absent. The work below moves that
selection to a **DB-backed, owner-editable runtime configuration** with **in-GUI model management**,
then finishes the deferred roadmap tail.

Governing principle is unchanged: ship steady, user-visible progress; keep the dependency-free
lexical/TF-IDF/extractive engines as the default and the test path; never add a hard dependency to
the base install.

---

## Stage 8 — Runtime, GUI-managed AI providers & model management  *(headline)*

Make AI behavior a first-class, owner-managed runtime concern.

**8A. DB-backed AI configuration.** New `ai_config` (single-row, owner-owned) table + Alembic
migration holding the effective settings: `embedding_provider` / `embedding_model`,
`summary_provider` (`extractive` | `local_llm`) / `summary_model`, `topic_backend` /
`topic_embedding_model`, plus `ollama_url` override. A service `get_ai_config(db)` overlays the DB
row on the static `Settings` defaults (DB wins; `Settings` is the bootstrap fallback). `embeddings.
get_embedding_provider`, `summarization`, and `topic_modeling` read the **effective** config from
here instead of `get_settings()` directly.
  *DoD:* changing a provider in the DB changes behavior with no process restart and no env edit; with
  an empty `ai_config` the app behaves exactly as today (hash_bow / extractive / tfidf).

**8B. Owner AI-config API.** `GET /admin/ai-config` (effective config + per-field source: default vs
DB) and `PUT /admin/ai-config` (owner only, validated against known providers/models, audited
`ai.config_changed`). Changing the **embedding model** enqueues a reindex (vectors are stored per
`model_name`, so the active model must be (re)built — see 8F).
  *DoD:* round-trips through the API; invalid provider/model rejected; a model change schedules
  reindex.

**8C. Provider capability detection + model management API.**
  - `GET /admin/ai/providers` — for each provider report **availability** (is the Python lib
    importable? is the Ollama daemon reachable at the configured URL?) and the **models present
    locally**, so the GUI only offers what can actually run and explains how to enable the rest.
  - `POST /admin/ai/models/pull` — download/pull a model as a tracked background RQ job:
    - **Ollama:** call the daemon's `/api/pull` (streamed progress → job status); needs no Python
      dependency, so it is **fully GUI-drivable** once the Ollama profile is up.
    - **sentence-transformers:** download the named HF model into a shared **model-cache volume**
      (the weights, not the OS package — see 8E).
  - `GET /admin/ai/models` + `DELETE /admin/ai/models/{id}` — list local models (name, size, provider)
    and remove them to reclaim disk.
  - Pull/download status surfaced through the existing Jobs surface (label `model-pull`) plus a
    dedicated `GET /admin/ai/models/pull/{job_id}` for live progress.
  *DoD:* an owner can pull an Ollama model from the GUI and watch progress to completion; a model
  that finished downloading appears in `GET /admin/ai/models` and becomes selectable in 8B.

**8D. Admin → "AI & Models" panel (frontend).** A new Admin sub-page:
  - provider pickers for **embedding / summary / topic**, each populated from 8C capability detection
    (unavailable providers shown disabled with a one-line "how to enable" hint — e.g. *"start the
    Ollama profile"* / *"rebuild with the `ai` image extra"*);
  - model dropdowns populated from locally-present models, with a **"Pull model…"** control (name
    input + progress bar driven by the pull job);
  - toggles for behavior (LLM summaries on/off, topic backend);
  - a **"Reindex embeddings"** button + a live *"N/M works indexed for `<model>`"* readout;
  - **Save** writes via 8B and shows what changed + whether a reindex was queued.
  *DoD:* a non-technical owner can switch to a real embedding model, pull it, reindex, and get
  better semantic search — entirely from the web UI, no shell.

**8E. Dependency & profile strategy (explicit, documented).** Runtime `pip install` is intentionally
**not** performed (images stay immutable/reproducible). Instead:
  - **Ollama** (embeddings + `local_llm` summaries) needs no Python dep — only a reachable daemon;
    the existing `--profile ai` brings it up and the GUI configures its URL + pulls models. This is
    the recommended, fully-GUI path.
  - **sentence-transformers / BERTopic** Python packages ship in an **opt-in image layer** (an `ai`
    build target / compose profile); 8C detects whether they're importable and the GUI guides
    enabling them. Their **model weights** are downloaded at runtime into a cache volume via 8C.
  - Document all of this in a new `docs/runbooks/ai_providers.md`.
  *DoD:* the base image gains no heavy dependency; enabling a heavier provider is a documented
  profile/image action; *model weights* are always GUI-downloadable.

**8F. Reindex orchestration.** A reindex service/job builds embeddings for the **active** model and
reports progress (`indexed/total` for that `model_name`); search keeps returning results from the
previous model until the new index is ready (no blank window). Triggered by 8B model changes and the
8D button. Builds on Stage 6's `embed_work_job` / `ensure_work_embeddings`.
  *DoD:* switching models never loses search; progress is visible; old-model vectors can be GC'd.

---

## Stage 9 — Deferred roadmap tail  *(after Stage 8; mostly independent)*

- **H7 — pgvector.** Now first-class (real fixed-dim embeddings arrive via Stage 8). Add a `vector`
  column + ANN index + `CREATE EXTENSION vector`, used **only** when the active provider yields a
  fixed dimension; keep the JSON-array storage + Python cosine as the fallback for `hash_bow` and on
  SQLite. Migration + parity.
- **CSL citeproc styles.** Render real CSL styles via the already-present `citeproc-py` (style picker
  in `ExportDialog`; CSL-JSON interchange already ships). Add the **graph-scope** export.
- **Postgres integration suite.** Now that the C3/C4 FKs exist, add a Postgres-backed test asserting
  FK-cascade (delete a work → references/mentions/links go), `timestamptz` round-tripping, and JSONB
  `@>`/`->` query behavior — the cases SQLite can't exercise.
- **Optional ML extraction path (M7).** Nougat/Marker as an opt-in extractor for hard/scanned PDFs,
  behind the same Stage-8 provider/availability model (GUI-selectable, model-cached).
- **Full E2E + deploy hardening (M8).** A browser-level happy-path E2E (login → import → extract →
  read → export); LAN/TLS deployment notes; finish the audit `file.viewed` vs `downloaded`
  distinction and surface the read/view events in the Admin audit view.

---

## Sequencing

```
Stage 8  GUI-managed AI providers + model management   ← do first (maintainer priority)
  8A db config → 8B api → 8C model mgmt/detection → 8D GUI → 8E profiles/docs → 8F reindex
Stage 9  pgvector · CSL styles · PG integration suite · ML extraction · E2E   (after 8; parallelizable)
```

Stage 8 is the priority and is self-contained on top of the Stage-6 provider seams (the providers
already exist; this makes them **chooseable and downloadable from the GUI**). Stage 9 items are the
genuine long-tail and can be picked up in any order once Stage 8 lands.
