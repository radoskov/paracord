# Workplan — Summary effort levels, no-PDF honesty, jobs, tags, graphs (2026-07-16)

> **Status (2026-07-16):** ✅ ALL DONE — Jobs (§2), No-PDF honesty (§1.3), Effort levels + cache
> matrix (§1.1–1.2), LaTeX rendering (§1.4), Graph quick wins (§4.1–4.4, §4.7), pan/zoom fix (§4.8),
> Insights citing papers + separate ref/citation caps + external styling (§4.5–4.6), per-shelf/rack
> tags (§3). The ONE explicit follow-up left: the Library-view tag *filter* being scope-aware (§3 Q7,
> lower-priority) — the WorkDetail dropdown + Tag-tab management shipped. All 12 design questions
> resolved. Each item shipped with tests + green `frontend-check`; migrations 0074 + 0075 on the live DB.


Scope: a large UX batch spanning summarization (effort levels + caching + no-PDF handling +
LaTeX), the Jobs tab, per-shelf/rack tags, and both graph systems. This document sorts each
item into **ALL CLEAR** (well-specified, implement as described, recommendation noted) vs
**NEEDS DISCUSSION** (a design decision, ambiguity, or risk to settle before coding).

> Architectural note that recurs below: there are **two separate graph systems**.
> - **Insights citation/topic graph** — `services/citation_graph.py` → `endpoints/graph.py` →
>   force-directed `components/CitationGraph.svelte` (mounted only by `InsightsPage`).
> - **Per-paper Reference graph** — `services/reference_graph.py` → `works.py` →
>   `components/ReferenceGraphModal.svelte` + `lib/viz/referenceGraph.ts` (scatter/year layout,
>   grouped nodes). Co-citation and temporal map are further separate `lib/viz/*.ts` builders.
>
> Several requests below apply to one of these and not the other; each item says which.

---

## Part 1 — Summaries

### 1.1 Detailed-summary effort levels — MOSTLY CLEAR, one cache decision to settle

**Request.** Three effort levels for the detailed summary:
1. **Fast** — group sections into 4 buckets and summarise each bucket as one call:
   (a) title, abstract, introduction, related work; (b) methods, implementation, experiments,
   datasets; (c) results, discussion, conclusion, appendix; (d) other. Because real section
   names vary, the **first LLM call categorises each section title into one of the 4 buckets**,
   then one summary call per non-empty bucket.
2. **Section-wise** — one paragraph per top-level section. If top-level sections < 3, drop one
   level and use that sectioning **iff** the resulting section count < 10.
3. **Deep** — current behaviour (per subsection).

UI: horizontal radio buttons to the left of the "Generate detailed"/"Regenerate" button
(paper view); in Insights Scope summary, radios appear **below** the source dropdown, only when
a "detailed" option is selected (short has no effort level).

**Current state.** `summarize_work(..., detail="short"|"detailed")` is the only axis today
(`summarization.py:339`); detailed iterates GROBID sections via `iter_work_sections`
(`:399-408`). No effort concept exists.

**Approach (clear part).**
- Extend the detail axis to `short | detailed_fast | detailed_section | detailed_deep`
  (keep `detailed` as an alias mapping to `detailed_deep` for backward compat / the existing
  stored `local_llm_detailed` rows).
- New helpers in `summarization.py`: a bucket-categoriser (`detailed_fast`) and a top-level /
  one-level-down section selector (`detailed_section`). `detailed_deep` reuses today's path.
- The categoriser is one extra LLM call returning `{section_label: bucket}`; on failure or
  no-LLM, fall back to a keyword heuristic on the section labels (methods/results/etc.) so it
  degrades gracefully.

**→ DISCUSSION — the caching matrix (§1.2).** How these variants persist is the one real
decision; see next item.

---

### 1.2 Summary cache matrix (effort × model) — NEEDS DISCUSSION

**Request.** Each paper carries a small cache: 4 effort levels (short, detailed-fast,
detailed-section, detailed-deep) × one entry per model used (up to 5 models). Selecting a
combination in the paper view shows the cached entry (with Regenerate) or a Generate button;
Insights likewise fetches from cache / creates / regenerates.

**Current state — the blocker.** The `summaries` table has **no uniqueness constraint**
(`models/ai.py:23-47`). Dedup is procedural: `summarize_work`/`summarize_scope`
**delete-then-insert** on `(entity_type, entity_id, summary_type)` (`:431-437`, `:688-694`).
`model_name` is stored but is **not** part of the key — so regenerating with a different model
**overwrites** the previous model's row today. A cache that keeps multiple models cannot exist
under the current keying.

**Recommended design (my proposal — please confirm):**
1. Encode effort in `summary_type`: `local_llm` (short), `local_llm_detailed_fast`,
   `local_llm_detailed_section`, `local_llm_detailed_deep`. One-time migration renames existing
   `local_llm_detailed` → `local_llm_detailed_deep`.
2. Change the dedup key to include the model:
   `(entity_type, entity_id, summary_type, model_name)`. Add a real `UniqueConstraint` for it
   and switch to upsert-style replace. Regenerating the *same* effort+model replaces in place;
   a *different* model coexists.
3. Eviction: cap at **5 distinct models per (entity, summary_type)**, evicting the
   oldest by `created_at` (LRU). So worst case ~4 effort × 5 models = 20 LLM rows per work,
   plus the cheap abstract/extractive rows.

**DECIDED (2026-07-16):**
- **(Q1) Model selection — DECIDED.** The model used to *generate/regenerate* always comes from
  the current AI-config; the user cannot pick which model creates a summary. But both the paper
  view and Insights offer a **read-only history**: a "history" popup with a dropdown over the
  existing `(effort × model)` combinations, so past models' summaries can be *viewed* (not
  regenerated under an old model). The main view shows the entry for `(selected effort, current
  model)` with Generate/Regenerate; the popup exposes the rest.
- **(Q2) Eviction — DECIDED.** The full 20-row matrix: **one entry per (effort × model), 4
  efforts × 5 models**. LRU-evict the oldest *model* once a 6th distinct model appears (per
  effort level).
- **(Q3) Scope summaries — DECIDED: same principle.** Scope (library/shelf/rack) summaries are
  cached keyed on `(scope_type, scope_id, summary_type, model)`. Selecting a scope + effort
  shows the cached entry for the current model if it exists (with **Regenerate** and a footer
  giving generation source + date); otherwise the current "generate" interface is shown. Same
  read-only history popup applies.

---

### 1.3 No-PDF honesty (title-only / abstract-only) — ALL CLEAR

**Request.** Stop the silent fall-back to title/abstract:
- **Title-only** papers cannot be summarised locally. In a Scope summary they are collected
  into a single shared paragraph (together with abstract-less papers).
- **Abstract-but-no-PDF** papers can be summarised, but the prompt must let the model know it
  is working from an abstract so it frames the output correctly — without obsessing over saying
  "this is an abstract". These are also grouped into one shared paragraph in Scope summaries.
- **Full-text** papers processed as today.
- The scope footer must give a breakdown, e.g. "3 papers with PDFs, 2 abstract-only,
  1 title-only", plus the **model used** and the **date/time of generation**. (Today the
  frontend prints `local_llm · 6 papers · qwen3:4b · per-paper digests synthesized`.)

**Approach.** `_work_source` (`summarization.py:145-166`) already returns a `labels` list that
distinguishes abstract vs body — that is the single detection hook. Classify each work into
`full_text | abstract_only | title_only`. In `summarize_work`, title-only raises a typed
"cannot summarise (title only)" rather than an extractive-of-the-title; abstract-only uses an
abstract-framed prompt. In `summarize_scope`, partition the scope: full-text papers go through
the normal map step; abstract-only and title-only are each folded into a single synthesised
paragraph. Store the breakdown counts in `Summary.params` and stamp generation time (the row
already has `created_at`); the frontend footer (`InsightsPage.svelte:430`) renders the richer
string.

---

### 1.4 LaTeX / equation rendering — NEEDS DISCUSSION

**Request.** Summaries sometimes copy equations; as plain text they look bad
(`Â = D^{-1/2}ÃD^{-1/2}`, `g_θ(Λ)`, `O(|E|F C)`). Add a **plain / fancy** toggle where fancy
renders the maths; plain is the fallback for garbled characters. You noted some equations
arrive already unicode-rendered (`O(N²τ⁻¹)`) and asked how we even detect an equation.

**The honest answer to "how do we detect it".** The LLM emits maths as **inline plain text**,
*not* delimited LaTeX — there are no `$…$` markers to key off, and some of it is already
unicode (superscripts/Greek). So there is nothing reliable to auto-detect after the fact.
Two viable strategies:

- **(A) Ask the model to delimit.** Add to the prompt: "write any mathematical expression in
  LaTeX between `$…$` (inline) or `$$…$$` (display)." Then render with a bundled KaTeX
  (offline, no CDN — CSP-safe) and show raw text in plain mode. Clean, but only affects
  *newly generated* summaries and depends on the model complying.
- **(B) Heuristic post-detection.** Regex for tokens that look mathematical (`^`, `_{...}`,
  Greek letters, `\frac`, balanced parens with operators) and wrap them. Works on existing
  summaries but is fuzzy — false positives on prose, false negatives on unicode-rendered maths.

**DECIDED (2026-07-16):** Do **A + a light B heuristic**. Prompt the model to delimit maths in
`$…$`/`$$…$$` and render with **bundled offline KaTeX** (KaTeX approved). The heuristic only
auto-wraps the **egregious** non-delimited cases that read badly (e.g. `\frac`, `^{…}`, `_{…}`
with braces, backslash-commands) — it deliberately leaves easy inline tokens alone (`N_i`,
`O(n)` stay as-is). Plain/fancy toggle defaults to **fancy** with instant fallback to plain.
Accepted that full rendering is best on summaries generated after this change; the heuristic
gives partial improvement on older ones.

---

## Part 2 — Jobs

### 2.1 All non-trivial summaries visible in Jobs, with progress + cancel — ALL CLEAR

**Findings.**
- **Insights/scope summaries only enqueue above a size threshold** (`ai.py:111`,
  `scope_size > effective_ai_scope_job_threshold`); below it they run **inline in the request**,
  which is why only "whole library" shows in the Jobs tab. Fix: always attempt
  `enqueue_scope_summary` (keep the existing `job_id is None → inline` fallback for a down
  queue). Progress + cancel already work for scope jobs.
- **Single-paper detailed** already enqueues (`works.py:2370`), **but** `summarize_work_job`
  (`jobs.py:750`) does *not* wire `progress_cb`/`cancel_cb` and doesn't catch `JobCancelled` —
  so its Jobs row shows a Stop button that does nothing and no N/M. Fix: mirror
  `summarize_scope_job` (`jobs.py:856-875`), and thread a per-section/per-chunk progress count
  through the detailed summariser.
- **Short single-paper** stays inline (as requested).

### 2.2 "stopping…" stuck after finish → show "stopped" — ALL CLEAR

**Root cause.** `stopping` is derived only from `meta["cancel_requested"]` (`queue.py:970`),
which is never cleared; the frontend `{#if job.stopping}` branch (`JobsPage.svelte:267`) wins
over the status badge, so a finished/failed cancelled job shows "stopping…" forever.
**Fix (backend, single source of truth):** only report `stopping=True` when `cancel_requested`
AND the job is still active (`status in queued/started/…`); once terminal, the row shows its
real terminal state. Optionally surface an explicit "stopped" label for a cancelled-then-
finished job (distinguish from natural completion) — small addition.

---

## Part 3 — Tags per shelf/rack — NEEDS DISCUSSION (large, but well-specified)

**Request.** Tags can be assigned (in the Tag tab) to multiple shelves and/or racks, or none
(= available everywhere). Then:
- Tag tab: filter tags by shelf/rack.
- Paper view: the add-tag dropdown shows only tags valid for the paper's shelf/rack (union of
  the paper's shelf-tags and rack-tags; global tags always included).
- Library view: if the user narrows "any shelf"/"any rack" to a specific one, the tag filter
  dropdown shows only tags valid for that shelf/rack (additive union).

**Current state.** `TagLink` is polymorphic and *already* lets a tag be attached to a
work/shelf/rack — but that means "this shelf is tagged X", not "tag X is offered for papers
here". This feature needs a **new, separate** association. Hierarchy is many-to-many
throughout: rack → shelves → works (`ShelfWork`, `RackShelf` in `models/organization.py`).

**Approach.** New tables `tag_shelves(tag_id, shelf_id)` and `tag_racks(tag_id, rack_id)`,
cascade-on-delete. Convention: **a tag with zero scope rows is global**; any scope rows restrict
it. New endpoints to get/set a tag's scope; a new resolver `GET /works/{id}/assignable-tags`
computing the union from `ShelfWork + RackShelf + tag_shelves + tag_racks`. Frontend: scope
multi-select in `TagsPage.svelte`; filter the add-tag `<select>` in `WorkDetail.svelte:1475`
(the paper's `locations` with nested racks are already loaded client-side); scope-aware tag
options in `LibraryPage.svelte`.

**DECIDED (2026-07-16):**
- **(Q5)** Zero scope rows = **global** (the convention; no `Tag` schema flag).
- **(Q6)** A paper qualifies for a **rack-scoped** tag if **any shelf it's on belongs to that
  rack** (transitive rack→shelf membership).
- **(Q7)** Library tag filter is **lower priority** — WorkDetail dropdown first; Library filter
  is a follow-up (may land after the rest of this batch).

---

## Part 4 — Graphs

### 4.1 Reference-graph window title includes paper title — ALL CLEAR
`ReferenceGraphModal.svelte:324` hard-codes `title="Reference graph"`. Plumb the base paper's
title (already present in the graph payload / `WorkDetail`) into the modal header, e.g.
"Reference graph — <title>".

### 4.2 Reference-graph edges: thicker + 3 colours — ALL CLEAR
Distinguish **reference** (base→cited), **citing** (citer→base), and **ref-ref** (edges among
neighbours) with three colours and slightly thicker/more-visible lines, in
`lib/viz/referenceGraph.ts`. (Citing papers are already included here — `includeCiting=true`.)
Legend updated to match.

### 4.3 Grouped-node interactions (Reference graph & temporal map) — ALL CLEAR
Today a grouped node's tooltip lists up to 10 members with per-member "open/import" links, an
"Import all N" affordance, and "…and N more" *text* (`referenceGraph.ts:427-454`). Add:
- **Ctrl-click / middle-click a member → append that citation to the Batch-import box without
  switching tabs** (external papers only). Plain click keeps today's behaviour (switch to
  Import / open in Library). This mirrors the existing `pendingImportText` store but skips the
  `#import` navigation.
- **Make "…and N more" clickable** to reveal more members inline (up to ~10–15 total if they
  fit) — for all papers, not just external.

**DECIDED (Q8):** proceed as proposed — bind `auxclick`/`mousedown` button 1 + `preventDefault`;
ctrl-click documented as primary, middle-click best-effort.

### 4.4 Insights "Build graph" button state — ALL CLEAR
The build button lives in `CitationGraph.svelte:567` with only an internal `busy` flag; failures
are swallowed. Add explicit building / done / failed surfacing (spinner + message), matching the
Topics/Summary busy pattern already in `InsightsPage`.

### 4.5 Insights citation graph — add citing papers + typed/coloured edges — NEEDS DISCUSSION
**Finding.** The Insights citation graph currently contains **references only** (papers the
scope cites); there is **no citing-paper inclusion** and edges are typed only
`local_match | external` (`citation_graph.py:139-144, 186-192`). Adding citing papers means a
second walk (`ReferenceCitation.cited_work_id in scope`) plus a new edge type, then colouring
reference vs citing edges differently (internal + external both).

**DECIDED (Q9):** use **only existing, manually fetched citation data**. Include citing papers
where we have them; where none were fetched, that half is simply absent. Flag coverage in the UI
so an empty citing half reads as "not fetched", not "none exist".

### 4.6 Insights external nodes ignore size/color scheme — NEEDS (small) DISCUSSION
**Finding.** By design, the backend computes pagerank/betweenness/color_group for **local nodes
only**; external nodes are forced into a fixed "external" category + diamond shape
(`CitationGraph.svelte:213,218-219,301`) and carry `0` for pagerank/betweenness (so they shrink
to min size under those metrics). `citation_count` is never set for externals either.
**Approach.** Populate `citation_count` for external nodes (we often have it from Crossref/OA
metadata) and let externals participate in **degree** and **citation-count** sizing, and in the
colour scheme where a value exists. Betweenness/pagerank remain local-only (they're
scope-relative and meaningless for leaf externals) — externals fall back to a neutral style
under those two.
**DECIDED (Q10):** keep pagerank/betweenness local-only; make degree + citation-count + colour
apply to externals. **Keep the diamond shape** for externals (only size/colour conform, shape
still marks them as external).

### 4.7 Insights external-paper limit → even distribution — ALL CLEAR (implement your algorithm)
**Finding.** Today it's a single **global** top-N-by-incoming-weight cap
(`citation_graph.py:194-215`) — the first/most-connected papers can indeed eat the whole budget.
**Implement your algorithm as specified:**
- `A = ceil((L/2)/N)` absolute per-paper (≥1 each), assigned greedily; if `L < N` the budget is
  exhausted, remaining papers get none and no relative pass runs.
- Remainder `E = L − A·N`; if `E > 0`, relative share `R_i = (E/S)·C_i` (`C_i` = paper i's ref
  count, `S = ΣC_i`).
**DECIDED (Q11):** round `R_i` and redistribute the remainder (largest-remainder) so totals
sum to ≤ L.

### 4.8 Insights graph pan/zoom intermittently freezes — NEEDS DISCUSSION (root cause known, fix has risk)
**Root cause identified.** Every restyle/filter/size toggle bumps `revision`
(`CitationGraph.svelte:472-480`), which triggers a **full non-merge** `setOption(opt, true)`
(`:322-324`). A non-merge replace **rebuilds the series, restarts the force simulation, and
resets the roam (zoom/pan) transform**; separately `scheduleForceFit()` (`:379-387`) fires a
view reset ~1.6 s after a build. While the sim re-springs, pan/zoom "fights" it and appears
frozen; a tab refocus / reset nudges it. `fitView` already uses *merge* setOption deliberately.
**Fix approach.** Route restyle-only changes (sizeBy/colorBy/hide toggles) through **merge**
`setOption` (no series rebuild, no sim restart, roam preserved), reserve non-merge for genuine
data reloads, and stop `scheduleForceFit` from stealing the user's roam after the first fit.
**DECIDED (Q12):** proceed with caution. If the auto-fit ("view all") is what triggers the
freeze, **keep "view all" simple** — drop the 1.6 s deferred `scheduleForceFit` and do an
immediate, cheap fit; the deferred re-fit is lower priority than a stable pan/zoom.

---

## Sequencing (proposed, once decisions land)
1. Jobs fixes (§2.1, §2.2) — small, unblocks testing everything else's progress UI.
2. No-PDF honesty + footer breakdown (§1.3) — self-contained.
3. Summary effort levels + cache matrix (§1.1, §1.2) — after Q1–Q3.
4. LaTeX rendering (§1.4) — after Q4; independent.
5. Graph quick wins (§4.1–4.4, §4.7) — mostly self-contained.
6. Graph heavier items (§4.5, §4.6, §4.8) — after Q9–Q12.
7. Tags per shelf/rack (§3) — after Q5–Q7; largest single feature, migration + endpoints + 3 UIs.

Each chunk commits separately (`area: description`, no Co-Authored-By), updates PROGRESS.md and
the handoff note. Verification: `make ready-full && make e2e`; migrations applied to the live DB
via `docker compose exec api alembic -c backend/alembic.ini upgrade head`; worker restarted for
job-code changes.

## Decisions needed before coding (summary)
- ~~**Q1–Q3** summary cache~~ — DECIDED: current-model generation + read-only history popup;
  full 20-row (4 effort × 5 model) matrix, LRU per effort; scope summaries cached the same way.
- ~~**Q4** LaTeX~~ — DECIDED: prompt-delimited maths + bundled KaTeX + light heuristic for
  egregious non-delimited cases; new summaries render fully, older ones partially.
- ~~**Q5–Q7** tags~~ — DECIDED: zero rows = global; rack tag via any-shelf-in-rack; Library
  filter is a lower-priority follow-up.
- ~~**Q9–Q10** Insights graph~~ — DECIDED: citing papers from existing fetched data only (flag
  coverage); externals get degree/citation-count/colour but keep the diamond shape.
- ~~**Q11–Q12**~~ — DECIDED: round + largest-remainder redistribute; proceed on pan/zoom with
  caution, simplify "view all" (no 1.6 s deferred fit) if it's the trigger.

**All decisions resolved (2026-07-16). Ready to implement per the sequencing above.**
