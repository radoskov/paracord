# Feature Runbooks

Consolidated runbook (2026-07-08). Per-feature operational how-tos: theming, AI providers, and the local agent + teleport. Each former runbook is preserved in full.

## Contents
- Theming runbook — adding & customizing themes
- AI providers & models — choosing/enabling embedding, summary, and topic engines
- Local Agent Runbook — registration & file-access flow
- Teleport Runbook — required safety checks


---

<!-- consolidated from docs/runbooks/theming.md -->

## Theming runbook — adding & customizing themes

PaRacORD themes are defined in **YAML** and drive both the GUI (CSS custom properties) and the
data visualizations (charts + the citation network). One YAML file is one theme. There are two ways
to add a theme:

1. **Bundled** — copy a `frontend/themes/*.yaml`, edit, and rebuild. Ships in the app image; needs
   repo access + a build.
2. **Custom (runtime)** — an owner/admin pastes YAML into **Admin → Themes**. No rebuild; stored in
   the database and immediately available in everyone's picker.

The theming system is specified in [`SPECIFICATION.md` §33.4](../../SPECIFICATION.md) (the original
`THEMING_DESIGN.md` design brief is archived in `documentation_archive.zip`). The full YAML schema is
documented below; this runbook is the operational how-to.

---

## The YAML schema (roles you must provide)

```yaml
id: my-theme            # slug: lowercase letters/digits/hyphens (the data-theme id + picker id)
name: "My Theme"        # display name in the picker
mode: dark              # light | dark  — drives data-theme + the chart light/dark mode
temperature: cool       # warm | cool | custom — grouping label only

# Optional named ramp. Any "palette.<key>" reference in tokens/graph is resolved to its hex here,
# so you can define a colour once and reuse it. Omit it and just write hex everywhere.
palette:
  base: "#1e1e2e"
  blue: "#89b4fa"
  # …

# REQUIRED role tokens → become CSS custom properties (--surface-base, --ink-strong, …).
tokens:
  surface: {base: "#…", raised: "#…", overlay: "#…", sunken: "#…", hover: "#…"}
  ink:     {strong: "#…", normal: "#…", muted: "#…", inverse: "#…"}
  border:  {normal: "#…", strong: "#…", focus: "#…"}
  accent:  {primary: "#…", primary-strong: "#…", secondary: "#…", link: "#…",
            note: "#…", note-bg: "#…", note-border: "#…"}
  status:  {success: "#…", success-bg: "#…", success-border: "#…",
            warning: "#…", warning-bg: "#…", warning-border: "#…",
            danger:  "#…", danger-bg: "#…", danger-border: "#…",
            info:    "#…", info-bg: "#…", info-border: "#…"}
  radius:  {sm: "6px", md: "8px"}
  font:    {family: "Inter, ui-sans-serif, system-ui, sans-serif"}

# The DATA palette for charts + the network. `categorical` is required; the rest default from the
# tokens if omitted (see below).
graph:
  surface: "#…"                 # chart background (may differ from the GUI surface for clarity)
  categorical: ["#…", "#…", …]  # validated, CVD-safe, FIXED order (a 9th series folds to "Other")
  sequential: ["#…", …]         # one hue light→dark (heatmap / density)
  diverging: {low: "#…", mid: "#…", high: "#…"}  # two poles + neutral mid
  # optional presentational keys (default from tokens if omitted):
  #   text, axis_line, split_line, grid, node_default, edge, tooltip_bg, tooltip_text,
  #   warning_ring, font
```

### Token roles (what each drives)

- **surface** — page/panel/overlay/inset backgrounds + the hover fill.
- **ink** — text: `strong` (headings), `normal` (body), `muted` (secondary), `inverse` (on-accent).
- **border** — `normal`/`strong` dividers + `focus` (focus ring / active accent border).
- **accent** — `primary` (+`primary-strong` for the pressed/hover state), `secondary`, `link`, and
  the decorative `note` trio (`note`/`note-bg`/`note-border`) for AI/semantic/tag chips.
- **status** — `success`/`warning`/`danger`/`info`, each an emphasis colour plus a `-bg` tint and a
  `-border` tint (badges/panels theme with no per-component literals). Status colours are reserved
  and never reused as a data series.
- **radius / font** — shape + typography (non-colour tokens are allowed).

### The `graph` data palette + the readability requirement

The GUI chrome (tokens) and the data palette (`graph`) are **separate concerns**: pretty UI accents
often fail as categorical *graph* colours. Every data palette should be **validated for
readability** — the dataviz palette validator checks four things against the chart `surface`:

- OKLCH **lightness band** (marks sit in the mode's categorical band),
- OKLCH **chroma floor** (no series reads as grey),
- WCAG **contrast** ≥ 3:1 vs the surface,
- **CVD ΔE** between adjacent categorical entries (target ≥ 12; the 8–12 floor is allowed ONLY
  because the graph/network views ship a legend + selective labels + a surface ring on overlapping
  nodes — the required secondary encoding).

For a **bundled** theme this is a build check — run the validator and iterate the hues until it
passes:

```
node dataviz/scripts/validate_palette.js '["#cf7020","#4a7fd0", …]' --surface '#211e2a' --mode dark
```

(The theme tests in `frontend/src/lib/theme/theme.test.ts` re-run the check on every bundled
theme's `graph.categorical`, so a regression can't ship.) `graph.categorical` is assigned in fixed
order (never cycled), `sequential` is one hue light→dark, and `diverging` is two poles + a neutral
mid.

---

## Adding a BUNDLED theme (P1 pipeline, needs a rebuild)

1. Copy an existing file, e.g. `cp frontend/themes/mocha-warm.yaml frontend/themes/my-theme.yaml`.
2. Edit `id`, `name`, `mode`, `temperature`, the palette/tokens and the `graph` block.
3. Validate the data palette (see above) until it passes for your `graph.surface`.
4. Compile: `cd frontend && npm run themes:build` (writes `src/lib/theme/themes.generated.ts`; this
   also runs automatically on `npm run build` via `prebuild`).
5. The theme now appears in the picker automatically (the picker is data-driven from
   `bundledThemes`). Add its `id` to `KNOWN_THEME_IDS` in `backend/app/core/themes.py` so the
   backend accepts it as a per-user preference.
6. Run `make frontend-check` (tests + build) and commit the new YAML + regenerated
   `themes.generated.ts`.

---

## Adding a CUSTOM theme at runtime (P4, no rebuild)

As owner or admin: **Admin → Themes**.

1. Paste the full theme YAML (copy a bundled file as a starting point).
2. **Save theme.** The server validates on load:
   - **Rejected (400)** — malformed YAML, a missing required token role, a bad `id`/`mode`, or an
     `id` that collides with a bundled theme.
   - **Accepted with warnings** — a `graph.categorical` palette that fails the readability check is
     **not** rejected (a user may accept it); the warnings are shown in the Themes tab so you can
     decide whether to fix and re-upload. (The Python readability check is best-effort; the bundled
     build-time validator remains authoritative for shipped themes.)
3. Re-uploading with the same `id` **replaces** the theme.
4. The theme appears in everyone's picker (Profile → Appearance) alongside the four bundled ones;
   selecting it restyles the whole running app — GUI, open charts and the citation network — live,
   and can be saved as a per-user preference.
5. Delete a theme from the same tab; anyone using it falls back to the default on next load.

Create and delete are recorded as audit events (`theme.uploaded` / `theme.deleted`). The canonical
YAML lives in the `custom_themes` table, so custom themes are included in the normal database
backup.

---

<!-- consolidated from docs/runbooks/ai_providers.md -->

## AI providers & models (Admin → AI & Models)

PaRacORD's semantic search, summaries, and topic modeling run on **dependency-free lexical
baselines by default** (hash-BOW embeddings, extractive summaries, TF-IDF topics). Heavier local
providers are **opt-in and configured from the web UI** (Admin → AI & Models), not from a config
file. Owner only.

## What you can choose (Admin → AI & Models)

| Engine | Default | Heavier options |
|--------|---------|-----------------|
| Embeddings (semantic search) | `hash_bow` | `sentence_transformers`, `ollama` |
| Summaries | `extractive` | `local_llm` (Ollama) |
| Topics | `tfidf` | `embedding` / `bertopic` (deterministic embedding clustering) |

Each provider shows **available** or a one-line hint for how to enable it. Changing the **embedding
model** automatically queues a **reindex** (vectors are stored per provider+model); the panel shows
`indexed / total` coverage for the active model and has a **Reindex** button.

## Enabling the heavier providers

Two independent things: the **runtime** (a Python package or a daemon) and the **model weights**.

### Ollama (recommended — fully GUI-drivable)

Ollama needs **no Python dependency**, only a reachable daemon. It powers both embedding
(`ollama`) and `local_llm` summaries.

```bash
make up-ai          # starts the Ollama service (compose `ai` profile)
```

Then in Admin → AI & Models: set **Ollama URL** (default `http://ollama:11434` in compose, or
`http://localhost:11434`), **Pull model** (e.g. `nomic-embed-text` for embeddings, `qwen3:4b` for
summaries), select the provider, and **Save**. Pulls run as background jobs — watch the Jobs tab.

### sentence-transformers (Python package)

The Python package is **not** in the base image (immutable images; no runtime `pip install`). Enable
it by rebuilding with the AI extra (uncomment `sentence-transformers` in
`backend/requirements.txt`, or use an `ai` build target), then redeploy. Once importable, the panel
marks it **available**; selecting a model downloads its weights into the model-cache on first use /
via **Pull model** (`provider = sentence_transformers`).

## How the config is applied

The choices are stored in the single-row `ai_config` table (migration `0018`) and overlaid on the
static `Settings` defaults at request time (`app/services/ai_config.py`). An empty table reproduces
the exact out-of-the-box baseline behavior, so the GUI never has to be touched to get a working
system — it only *upgrades* the engines when you choose to.

---

<!-- consolidated from docs/runbooks/local_agent.md -->

## Local Agent Runbook

The local agent scans only configured roots. It sends manifests to the server and can teleport selected PDFs to the managed server library.

## Registration flow

1. Owner creates an agent bootstrap token on the server.
2. Workstation runs `paracord-agent register`.
3. Server returns an agent ID and token.
4. Agent stores token in a user-readable-only token file.
5. Future requests use the scoped token.

## File access flow

- Server may request a known `local_file_id`.
- Agent resolves the ID through its local index.
- Agent verifies the file is still inside an allowed root.
- Agent streams or uploads the file.
- Agent refuses raw path requests.

---

<!-- consolidated from docs/runbooks/teleport.md -->

## Teleport Runbook

Teleport means copying a PDF from a workstation/agent or server allowed root into the server managed-library store.

## Required checks

1. User is authenticated and authorized.
2. File ID is known to the server.
3. Agent confirms the file is available and inside an allowed root.
4. Server receives file in chunks.
5. Server computes SHA-256.
6. Server compares computed hash to manifest hash.
7. Server writes the file to content-addressed storage.
8. Server creates or updates File, Location, and ImportBatch records.
9. Audit event is written.

Teleport must not delete the original file unless a future explicit feature is added.
