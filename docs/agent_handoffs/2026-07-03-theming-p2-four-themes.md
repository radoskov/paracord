# Handoff — Theming P2: author + validate the 4 themes (2026-07-03)

## Task
Implement Theming Phase P2 from `docs/THEMING_DESIGN.md`: author the 4 Catppuccin-based themes
(`latte-warm`, `latte-cool`, `mocha-warm`, `mocha-cool`), migrate the ~130 remaining per-component
hardcoded colours onto role tokens, validate each theme's `graph.categorical` data palette, and
theme the ECharts charts + the Cytoscape network so the WHOLE app recolours under each theme.

## What shipped
- **4 theme YAMLs** (`frontend/themes/{latte-warm,latte-cool,mocha-warm,mocha-cool}.yaml`),
  replacing P1's `default`/`default-dark` stubs (both deleted). Latte (light) + Mocha (dark), each
  in a warm and cool variant. Boot default migrated to **`latte-warm`** (`DEFAULT_THEME_ID`).
- **Token schema extended** (`lib/theme/types.ts`): `accent.note`/`note-bg`/`note-border` (the
  purple/indigo decorative family — AI/semantic/role/tag chips) and `status.<name>-bg`/`-border`
  for success/warning/danger/info (badge/panel tints), plus graph `grid`/`node_default`/`edge`/
  `warning_ring`/`sequential`/`diverging`. `renderThemeCss` now also emits a **`--muted` alias** of
  `--ink-muted` (`aliasEntries` in `css.ts`).
- **~186 hardcoded colour literals migrated to tokens across 23 Svelte components** (P1's estimate
  was ~130 distinct; actual literal occurrences were higher). After migration **0 `#`-hex colour
  literals remain** in any Svelte component (verified by grep — only `#each`/`#if`/`#key` control
  blocks and one `rgba()` backdrop/flash + `rgba` box-shadows remain, which carry alpha and are
  theme-neutral). `ShelfPicker`'s `var(--muted, #555/#777)` now resolves to `var(--ink-muted)`.
- **Cytoscape network themed** (`components/CitationGraph.svelte`): the stylesheet's node label
  (`viz.text`), external-node fill (`viz.nodeDefault`), edge/arrow (`viz.edge`) and warning ring
  (`viz.warningRing`) now read the active theme's `graph` block via `resolveThemeById(data-theme)`;
  the removed hardcoded Okabe–Ito `COLOR_PALETTE` is replaced by the theme's validated
  `graph.categorical`. The component's own `<style>` block uses role tokens.
- **ECharts themed via the active theme**: `VizTheme` gains `sequential`/`diverging`/`nodeDefault`/
  `edge`/`grid`/`warningRing`; the similarity heatmap ramp now uses `theme.sequential`. The two
  chart pages (`VisualizationsPage`, `CitationSummaryPage`) switched from `resolveTheme('dark'|
  'light')` (the old `data-theme === 'dark'` check that could never match a theme id) to
  **`resolveThemeById(document.documentElement.getAttribute('data-theme'))`**, so each theme's
  `graph` block drives the charts.
- **Palette validator ported into the repo** (`lib/theme/paletteCheck.ts`, `validateCategorical`) —
  the four computable checks from the dataviz skill's `validate_palette.js` (OKLCH lightness band,
  chroma floor, Machado-2009 CVD ΔE, WCAG contrast). The theme test runs it against every theme.
- **`prebuild` npm script** runs `themes:build` so the codegen regenerates `themes.generated.ts`
  before `vite build` in `frontend-check`.

## The 4 validated categorical palettes + verdicts
Validated with `dataviz/scripts/validate_palette.js` against each theme's actual `graph.surface`.
Warm/cool share a hue set but differ in ORDER (warm-lead vs cool-lead) + surface undertone.

- **latte-warm** (light, surface `#faf7f2`): `#e06b00,#1f6fd6,#d23a52,#0a9aa0,#9a8400,#8f57d6,#cc489a,#2f8f3e`
  → **ALL CHECKS PASS** — lightness ✓, chroma ✓, **CVD worst adjacent ΔE 26.1 (protan)** ✓ (>12 target), contrast ✓.
- **latte-cool** (light, surface `#f7f9fc`): `#1f6fd6,#e06b00,#0a9aa0,#d23a52,#8f57d6,#9a8400,#cc489a,#2f8f3e`
  → **ALL CHECKS PASS** — **CVD ΔE 26.1** ✓, all others ✓.
- **mocha-warm** (dark, surface `#211e2a`): `#cf7020,#4a7fd0,#e04a68,#1a9a9a,#a88a20,#a55fe0,#d85fa8,#2e9a52`
  → **ALL CHECKS PASS** — **CVD ΔE 16.4** ✓ (>12), lightness in dark band ✓, chroma ✓, contrast ✓.
- **mocha-cool** (dark, surface `#1c1e30`): `#4a7fd0,#cf7020,#1a9a9a,#e04a68,#a55fe0,#a88a20,#d85fa8,#2e9a52`
  → **ALL CHECKS PASS** — **CVD ΔE 16.4** ✓, all others ✓.

All four clear the **CVD target (≥12)**, comfortably above the 8–12 floor — no theme relies on the
floor relaxation. Raw Catppuccin accents FAIL (documented in the design); these are validated
derivatives (chroma deepened, lightness pulled into the mode's band, ordered for adjacent CVD).

### Sequential + diverging ramps (also validated)
Each `sequential` ramp passes the `--ordinal` checks (one hue, monotone L, ΔL ≥ 0.06, light-end
clears surface ≥2:1):
- latte-warm peach `#df9153→#6a3a08`; latte-cool blue `#7ea6e9→#163c78`;
  mocha-warm peach `#8a5218→#f7d3b0`; mocha-cool blue `#3a5ea0→#c2d8f7`. All **PASS**.
- `diverging` = blue↔red poles + a neutral mid (light: `#dfe3ea`; dark: theme surface1).

## Contrast (WCAG) results
Verified with a luminance/contrast helper:
- **Body/muted text** (`--ink-strong/normal/muted`) on every surface (base/raised/overlay/sunken):
  light ≥ **4.77:1**, dark ≥ **4.74:1** (all ≥ 4.5 AA). `--ink-muted` was darkened to `#5f6278`
  (light) to clear AA on the sunken panels.
- **Status emphasis text** on base + on its own tint bg: all ≥ **4.8:1** (success/warning were
  darkened to `#2a7130`/`#8a5f10` to clear AA on the light themes).
- **Primary button fills** with `--ink-inverse` text: light ≥ 6.4:1, dark ≥ 7.8:1.
- **Graph marks / nodes**: every categorical mark ≥ 3:1 vs its chart surface (validator "Contrast
  vs surface" PASS for all 4 — no relief needed).

## Verification
- `make frontend-check` green: `npm ci` OK, **141 tests pass (1 pre-existing skip)**, `vite build`
  OK (prebuild regenerated `themes.generated.ts`). Theme tests: `theme.test.ts` (12) asserts every
  theme has the full role-token set non-empty + the `--muted` alias + a complete graph block, and
  runs `validateCategorical` per theme (fails if any palette regresses below band/chroma/CVD-floor);
  `viz/theme.test.ts` (5) checks `resolveThemeById` maps every theme's graph onto VizTheme.
- **Structural check**: every role token + `--muted` + the derived `status-*-bg`/`-border` +
  `accent-note*` tokens are defined non-empty for all 4 themes (theme.test.ts iterates
  `EXPECTED_TOKENS` × `bundledThemes`). **grep confirms 0 `#`-hex colour literals** remain in the
  migrated components.
- `check_secrets` clean. Backend untouched (P3 adds the persistence endpoint).

## Design decisions / deviations
- **Boot default = `latte-warm`** (chosen over aliasing `default`). No theme keeps the id `default`.
- **Warm vs cool** = shared hue set, different categorical ORDER (warm-lead: peach/red/yellow first;
  cool-lead: blue/teal first) + a faint surface undertone (warm rosé/amber, cool blue) + warm/cool
  accent leads. Both orderings validate identically (adjacency preserved).
- **Mocha kept** for the dark base (not Frappé/Macchiato) — the dark categorical validates cleanly
  against it and the chrome contrast is strong (≥9:1 body text).
- **`graph.surface` differs slightly from the GUI base** (a hair lighter/darker, near-neutral) per
  the design's allowance — reads cleaner for dense networks and all palettes validate against it.
- **`.import` teal button** (CitationSummaryPage) had no teal token → mapped to `--status-success`
  (a positive "import" action) rather than shipping an unthemed literal.
- Live theme switching is **P3** (the picker + persistence + restyling open charts). P2 reads
  `data-theme` at render/build time; charts + the network pick up the boot theme correctly.

## Next recommended task
Theming P3: theme picker (grouped light/dark, warm/cool) + server-side per-user persistence
(mirror `papers_per_page`) + localStorage no-flash cache; hook a store/event so open ECharts +
Cytoscape views restyle live on theme change (both already read `data-theme`).
