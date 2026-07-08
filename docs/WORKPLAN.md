# PaRacORD — Work Plan

**Consolidated 2026-07-08.** This is the single, forward-looking backlog: everything **not yet
built** or **not yet attended to**, extracted from the many per-round workplans and feature-design
docs that preceded it. Completed workplan history now lives in
[`WORKPLAN_ARCHIVE.md`](WORKPLAN_ARCHIVE.md); known technical issues/defects live in
[`AUDIT.md`](AUDIT.md); the agreed feature set (mostly built) is [`../SPECIFICATION.md`](../SPECIFICATION.md);
the running completion log with commit hashes is [`../PROGRESS.md`](../PROGRESS.md).

**How this is organized.** Items are grouped by priority (High → Medium → Low). Each item names its
originating doc(s) and, where a security/robustness issue backs it, cross-references the `AUDIT.md`
ID (the audit is the risk register; this plan is the build list — they point at each other rather
than duplicate). **Unresolved product/architecture discussions** — choices awaiting an owner call —
are collected at the **end of this document**.

**Ground-truth note.** Every item below was verified against the code + `PROGRESS.md` at
consolidation time, not merely copied from the source docs (many of which had stale status text).

---

## High priority

### H1 — First-run onboarding wizard
*Source: UX_FEATURE_IMPROVEMENTS §1/§9.* No onboarding/wizard page exists today. Build a guided
first-run flow (create owner → add a shelf → import/attach a first paper → extract → read) so a
fresh install reaches "a useful shelf" without spelunking the full IA. Gates the doc's own §9
"empty app → useful in 10 minutes" milestone (with H2).

### H2 — Library-health dashboard
*Source: UX_FEATURE_IMPROVEMENTS §3; AUDIT E6.* A single page of **actionable, clickable** counts:
papers without a PDF, failed extraction, missing DOI, metadata conflicts, unresolved references,
no-shelf, stale indexes — each click deep-links to the filtered library view. The underlying
filters already exist piecemeal; this bundles them into one health surface.

### H3 — Ship a real embedding model as the practical default
*Source: NEXT_STEPS #2; B1-B3-ML-DEPTH; overlaps the discussion below.* All infrastructure exists
(pgvector ANN, per-model registry, GUI model pull, chunk embeddings) but the **default** embedding
provider is still `hash_bow` (lexical). Registering a sentence-transformers / Ollama model as the
shipped default is the single biggest semantic-quality unlock. **Needs the owner decision** recorded
under "Unresolved discussions → Default embedding model" before building (image weight / first-run
download tradeoff).

---

## Medium priority

### M1 — Import/dedup feature deltas (spec §8, "D38 smaller deltas")
*Source: NEXT_STEPS #3; AUDIT D38 (partial); AUDIT_EXT §2.2.* Four independent sub-projects, none
built:
- **(a) Backup/restore as REST endpoints** (spec §10.2) — currently CLI-only; the most operationally
  useful of the four.
- **(b) Import breadth** — CSV/TSV import, watched-folder import, and **Zotero** import (colleague
  interop).
- **(c) Duplicate kinds** — preprint↔published detection and same-file-different-path detection
  (spec §8.4).
- **(d) Reference-string fallback parser** — anystyle/refextract for citations GROBID cannot
  structure (spec §8.2).

### M2 — Production hardening & deployment guide  *(security — see AUDIT D3, D2, E1, E5)*
*Source: NEXT_STEPS #6; WORKPLAN_2026-07 D3; AUDIT D3.* For the supported "few users on a LAN" mode:
- **Agent↔server plaintext-HTTP guard** (`allow_insecure_http`): warn/refuse the 180-day bearer +
  enrollment token over cleartext to a non-loopback host unless explicitly opted in. **This is the
  highest genuinely-open audit item — AUDIT D3.**
- **Non-root nginx master** in the prod image (workers already non-root; the master still runs as
  root).
- **TLS enforcement** + a **prod-deployment runbook** (reverse proxy / certs). Fold in the audit
  hardening residuals as defense-in-depth (E1 Redis fail-closed flag **done 2026-07-09** —
  `PARACORD_PRODUCTION_REQUIRE_REDIS`, document it in the runbook; E5 read-only mounts + backup
  verification in CI still open).

### M3 — Unified processing center
*Source: UX_FEATURE_IMPROVEMENTS §2; AUDIT E4 (partial).* JobsPage already shows queue/worker health,
a failed filter, and clear. Extend it to a per-task-type view (OCR / embeddings / topic / summary /
graph-refresh rows) with **retry / cancel per row** and a **human-readable failure reason** per task.

### M4 — Shelf/rack literature-review workspace
*Source: UX_FEATURE_IMPROVEMENTS §5 (partial).* CitationSummaryPage already covers most-cited / bridge
/ missing / chronological at scope level. Add a **per-shelf review page** bundling the scope summary
+ topic map + "read next" + export into one workspace.

### M5 — Citation-count freshness job
*Source: NEXT_STEPS #4.* Citation counts refresh only on per-work Enrich (Track C P1 deliberately
added no scheduler). Add a periodic/opt-in refresh job so counts don't go stale.

### M6 — Merge-tags action
*Source: NEXT_STEPS #5.* Tag rename already 409s on a name collision; the natural complement is a
"merge tags" action (fold tag A into tag B, re-point all applications). Not built.

---

## Low priority

### L1 — Full BERTopic topic backend (B1.2)
*Source: B1-B3-ML-DEPTH.* The embeddings→k-means path is built (`topic_modeling.py`
`backend="embedding"`). Full BERTopic (UMAP + HDBSCAN + c-TF-IDF) is **not** — `bertopic`/`hdbscan`
are uninstalled and selecting `backend="bertopic"` currently falls back to the embedding/k-means
path. Heaviest ML stack; deferred. (See also the "Topic-modeling depth" discussion.)

### L2 — Large-library UI performance
*Source: UX_FEATURE_IMPROVEMENTS §8 (partial).* Graph node caps, default scoping, lazy-loaded libs,
and pagination are done. Remaining: **virtualized rendering for large tables** and fuller shelf/rack
summary caching.

### L3 — Ops polish (D30)
*Source: WORKPLAN_2026-07 D30; AUDIT D30.* Optional `slim` backend image without the OCR toolchain
(so "heavy = opt-in" holds), and runtime `config.js` API-base injection so the prod frontend needn't
rebuild when the server address changes.

### L4 — Scheduled CI E2E with service profiles
*Source: NEXT_STEPS #8.* The GROBID + online-identifier E2E journeys skip in CI (profile-gated). Add
a scheduled CI run with the `extraction`/`ai` profiles up so they actually execute.

### L5 — Theming "future additions"
*Source: THEMING_DESIGN (Future additions).* Candidate enhancements, all explicitly future ideas: a
high-contrast/CVD-max theme; `prefers-reduced-motion` / forced-colors / print stylesheets; density +
typography tokens exposed in the picker; per-view/per-encoding palettes; time-of-day switching; an
in-app theme editor; a bundled-theme gallery.

### L6 — Ollama pull-model live progress
*Source: WORKPLAN_B1_AND_ISSUES #5.* `model_management.pull_model` calls Ollama `/api/pull` with
`stream: False` and queues a job (watched via the Jobs tab); the streamed progress bar the plan
envisioned is not wired. Cosmetic polish.

### L7 — Audit hardening residuals (defense-in-depth)
Tracked in [`AUDIT.md`](AUDIT.md), listed here so they're on the build radar. **Done 2026-07-09**
(easy-audit-items batch): **E1** (Redis fail-closed flag + admin "limits unavailable" status),
**E2** (parser-level PDF validation via a PyMuPDF open-probe before GROBID/OCR), and the
`Agent.revoked_at` dead-column cleanup. **Still open:** **E3** (SQL visibility predicates instead of
Python-materialized `IN` lists), **E5** (read-only container mounts + backup-restore verification in
CI), **D2 residual** (HttpOnly-cookie token migration — *deferred by design*, only worth it beyond
LAN), **S2 residual** (agent loopback-GUI token niceties: one-time launch token, rotation on
restart).

---

# Unresolved discussions (awaiting an owner decision)

These are **choices**, not scheduled work — moved here from `DISCUSSIONS.md` and the open questions
in the feature-design docs. Nothing below should be built until decided.

### Default embedding model *(gates H3)*
Register a real embedding model as the shipped default vs keep the lexical `hash_bow` default with
heavier providers activate-when-present. Sub-questions: ship sentence-transformers pre-installed in a
default AI image (bigger image / first-run download) vs the current opt-in model; which dimensions to
provision (MiniLM-384 + nomic-768 are live; a 1536-dim slot is a documented future migration).
*Tradeoff: semantic quality out-of-the-box vs image weight and offline-friendliness.*

### Topic-modeling depth (B1.2 BERTopic) *(gates L1)*
The owner requested an **explainer first** — where TF-IDF/embedding stand-ins are used per feature,
and the VRAM / image-size / download impact of a real BERTopic stack — before committing. Much of the
storage mechanism (per-model constrained pgvector columns + HNSW) already shipped via hybrid search;
the open part is whether to add the heavy `bertopic`/`hdbscan`/`umap-learn` topic stack.

### UMAP in the base image
Add `numba`/`umap-learn` (~tens of MB, incl. llvmlite) to the base image so UMAP layouts work
out-of-the-box, vs keeping UMAP opt-in (the current state). *Tradeoff: convenience vs image weight.*

### Auto-prune old-model embedding rows on model switch
When the active embedding model changes, keep the previous model's chunk vectors (fast switch-back)
or prune them (smaller DB). Currently kept.

### Migration squash before first real-data release (AUDIT_EXT R2)
Squash the Alembic migration chain into a clean baseline **now** (tidy history, before real data
exists) vs **after** data exists (then forward-only). One-time cleanup vs churn risk.

### SQLite → Postgres test convergence (D27)
Direction is decided (opportunistically converge the API/flow suite onto Postgres), open question is
*when* to execute. *Tradeoff: removes dual-path complexity + divergence risk (AUDIT_EXT R1) vs slower
default test runs.*

### Frozen / deferred stances (revisit only if raised)
- **D25** — embedding-model registry uses runtime `ALTER TABLE` (web-admin DDL). **Frozen**; revisit
  only on an incident.
- **D26** — hand-rolled BM25F+ vs Postgres FTS. **Frozen**; re-evaluate FTS only if a D13-class
  rebuild problem recurs (D13 already de-risked).
- **D33** — per-section BM25 scores for lexical-only hits. **Deferred**; semantic/hybrid already show
  the matching section.
- **D34** — `summary_provider` selection UX. **Skipped for now** (owner can't validate the UX without
  a PC on hand); revisit later.

### Dropped / de-scoped (recorded so they aren't re-raised as open work)
- **B8** — the spec-named `/search` API shape (`GET /search`, `POST /search/advanced`,
  `GET /search/suggestions`): not selected; the unified `POST /search` with `mode:` shipped instead.
- **B9** — sealed emergency-recovery token: not selected.
- **Real ML extraction (Nougat/Marker, M7)** — the `full_ml`/`nougat`/`marker` provider seam was
  deliberately pruned as non-functional (AUDIT D35). A genuine integration is optional-future, not
  active backlog.
