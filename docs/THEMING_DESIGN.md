# PaRacORD — Theming design + workplan (2026-07-03)

A theming system for the GUI **and** the visualizations (charts + networks). Goal: start simple
but build the substrate so themes can grow into rich, hand-editable customization within reason.
Themes are defined in **YAML** (hand-editable), compiled to CSS custom properties for the GUI and a
JS theme object for the chart/graph renderers — one source of truth. First release: **4 cozy pastel
themes** (Catppuccin-inspired) — 2 light + 2 dark, each in a warm and a cool variant — designed
above all for **readability, especially of the graphs and networks**.

Owner brief (2026-07-03): simple to start, extensible; YAML basis; 4 themes (2 dark / 2 light, each
warm + cool); "nice and cozy" pastel à la Catppuccin (https://catppuccin.com/palette/); readability
first for the visualizations.

---

## Current state (what we build on)
- The GUI already uses CSS custom properties (`var(--…)`) but ad-hoc, with no unified token set and
  no `:root` palette; scoped Svelte styles reference a handful of vars.
- A `data-theme` attribute on `document.documentElement` is already the light/dark signal the chart
  pages read (`VisualizationsPage`/`CitationSummaryPage` check `data-theme === 'dark'`).
- `frontend/src/lib/viz/theme.ts` (`VizTheme`, `resolveTheme(mode)`) is a **hardcoded** 2-mode
  (light/dark) chart theme with a single Seaborn categorical palette — the graphs' current theming.
- No theme *picker*, no persistence, no warm/cool, no YAML.

So the substrate is half-present (CSS vars + `data-theme` + a JS chart theme) but unformalized. The
work is to unify it behind YAML-driven tokens and add the 4 themes + a switcher.

---

## Key design finding — validate the DATA palette separately (evidence-based)
Catppuccin's named accent colors are gorgeous for **UI chrome** but, run through the dataviz palette
validator, they **fail as categorical *graph* palettes**:
- **Dark** (Mocha accents on `#1e1e2e`): lightness-band FAIL (accents sit L≈0.76–0.92, too light for
  categorical marks), chroma floor FAIL (teal/yellow/pink read gray), CVD WARN (green↔peach ΔE 11,
  in the 8–12 floor). Contrast passes.
- **Light** (Latte accents on `#eff1f5`): chroma FAIL (teal reads gray), **CVD FAIL** (green↔peach
  ΔE 7.1 under protanopia), contrast WARN (peach/yellow/pink < 3:1 on the light surface).

**Consequence for the design:** split the two roles.
- **GUI chrome** (surfaces, ink, borders, accents, status) = Catppuccin pastels directly — cozy.
- **Data palette** (`graph.categorical`, sequential, diverging) = a **validated derivative** of the
  theme's hues: chroma deepened, lightness pulled into the mode's categorical band, order chosen so
  adjacent series separate under CVD. Each theme's data palette MUST pass
  `dataviz/scripts/validate_palette.js` (CVD ≥ 12 target; the 8–12 floor is allowed ONLY because the
  graph/network views already ship the required secondary encoding — a legend + selective direct
  labels + a 2px surface ring on overlapping nodes). This keeps the graphs readable, not just pretty.

---

## The four themes
Naming: `<base>-<temperature>` — `latte-warm`, `latte-cool` (light); `mocha-warm`, `mocha-cool`
(dark). "Warm" vs "cool" is expressed two ways: (a) a subtle undertone in the surface ramp (warm =
faint rosé/amber tint; cool = faint blue/teal tint), and (b) the *lead* accents and categorical
order (warm leads with rosewater/peach/yellow/maroon; cool leads with blue/sky/teal/sapphire/
lavender). Catppuccin conveniently ships both warm and cool named hues, so both variants stay on-
palette and cozy.

| Theme | Mode | Surface (base) | Ink (text) | Warm/cool cue |
|---|---|---|---|---|
| `latte-warm` | light | Latte `#eff1f5` + faint rosé tint | `#4c4f69` | rosewater/peach/maroon accents |
| `latte-cool` | light | Latte `#eff1f5` + faint blue tint | `#4c4f69` | blue/sapphire/teal accents |
| `mocha-warm` | dark | Mocha `#1e1e2e` + faint warm tint | `#cdd6f4` | peach/rosewater/yellow accents |
| `mocha-cool` | dark | Mocha `#1e1e2e` + faint cool tint | `#cdd6f4` | blue/sky/lavender accents |

(Frappé `#303446` / Macchiato `#24273a` are candidate alternates for a *softer* dark if Mocha reads
too deep — decide during P2 by eye + contrast check. The dark data palette is designed against the
chosen dark surface, never flipped from the light one.)

Each theme's data palette is finalized in P2 by iterating candidate hues through the validator until
it passes for that theme's surface — the raw Catppuccin sets above are the starting point, not the
answer.

---

## YAML theme schema (hand-editable, extensible)
One file per theme, e.g. `frontend/themes/mocha-warm.yaml` (bundled) or a user drop-in. Shape:

```yaml
id: mocha-warm
name: "Mocha (warm)"
mode: dark            # light | dark  (drives data-theme + chart mode)
temperature: warm     # warm | cool   (metadata / grouping in the picker)

# Named ramp the theme draws from (Catppuccin flavor colors) — optional but enables reuse.
palette:
  base: "#1e1e2e"; mantle: "#181825"; crust: "#11111b"
  text: "#cdd6f4"; subtext1: "#bac2de"; subtext0: "#a6adc8"
  surface2: "#585b70"; surface1: "#45475a"; surface0: "#313244"
  overlay: "#6c7086"
  rosewater: "#f5e0dc"; peach: "#fab387"; yellow: "#f9e2af"; green: "#a6e3a1"
  teal: "#94e2d5"; sky: "#89dceb"; blue: "#89b4fa"; lavender: "#b4befe"
  mauve: "#cba6f7"; red: "#f38ba8"; maroon: "#eba0ac"; pink: "#f5c2e7"

# Role tokens → become CSS custom properties (--surface, --ink-strong, …) on [data-theme=mocha-warm]
tokens:
  surface: {base: palette.base, raised: palette.surface0, overlay: palette.surface1, sunken: palette.mantle}
  ink: {strong: palette.text, normal: palette.subtext1, muted: palette.subtext0, inverse: "#1e1e2e"}
  border: {normal: palette.surface1, strong: palette.surface2, focus: palette.blue}
  accent: {primary: palette.peach, secondary: palette.rosewater, link: palette.blue}
  status: {success: palette.green, warning: palette.yellow, danger: palette.red, info: palette.sky}
  radius: {sm: "6px", md: "10px"}      # non-color tokens allowed too (density/shape)
  font: {family: "…", scale: "1.0"}

# The VALIDATED data palette for charts + networks (its own concern; must pass the validator).
graph:
  surface: palette.base            # chart background (may differ from GUI surface if clearer)
  grid: palette.surface1
  node_default: palette.overlay
  edge: palette.surface2
  text: palette.subtext1
  categorical: ["#…", "#…", …]     # validated, CVD-safe, deepened from the accents; fixed order
  sequential: ["#…", …]            # one-hue light→dark ramp
  diverging: {low: "#…", mid: palette.surface1, high: "#…"}  # two poles + neutral mid
  warning_ring: palette.red        # node warning badge (graph depth §8.9)
```

Rules baked into the schema: `graph.categorical` is assigned in fixed order (never cycled; a 9th
series folds to "Other"); `sequential` is one hue light→dark; `diverging` is two poles + a neutral
mid; status colors are reserved and never reused as a series. Unknown keys are ignored (forward-
compatible), missing keys fall back to the default theme (so a hand-written partial theme is valid).

---

## Workplan

**P1 — Token pipeline + refactor (no visual change).** Extract today's ad-hoc CSS vars into a
complete design-token set (`--surface-*`, `--ink-*`, `--border-*`, `--accent-*`, `--status-*`,
plus radius/font) declared under `[data-theme="<id>"]` selectors. Add a small build/runtime step
that reads the YAML theme(s) and emits (a) the CSS custom properties and (b) a JS theme object;
refactor `lib/viz/theme.ts` to consume the JS object instead of its hardcoded 2-mode palette. Port
the *current* look as one theme so nothing changes visually yet. Deliverable: the app themed by
tokens, one theme, byte-identical appearance.

**P2 — The four themes.** Author `latte-warm/cool`, `mocha-warm/cool` YAML. For each: map tokens to
the GUI + to `VizTheme` (ECharts) + to the Cytoscape stylesheet (node fill/label/edge/grid/warning
ring). **Design + validate each `graph.categorical` palette** with `dataviz/scripts/validate_palette.js`
against that theme's surface (CVD ≥ 12; 8–12 only with the existing legend+labels). Check WCAG AA on
text tokens and ≥3:1 on marks. Deliverable: 4 switchable, validated themes.

**P3 — Switcher + persistence.** A theme picker (grouped light/dark, warm/cool) in Profile/Settings;
persist per-user server-side (mirror `papers_per_page`) + a localStorage cache for no-flash on load;
set `data-theme` on `<html>`; charts re-read on change (viz pages already read `data-theme` — hook a
store/event so open charts restyle live without a full rebuild). Optional "Follow system"
(`prefers-color-scheme`) picking the warm-cool pair's light/dark member.

**P4 — Custom / hand-edited themes.** Support a user-supplied YAML theme (a `themes/` drop-in dir
and/or an admin upload) that appears in the picker; validate on load (schema + run the categorical
palette check, warn — don't hard-fail — if it doesn't pass, since a user may accept it); document the
schema (this file + a short runbook). A theme is now portable YAML: shareable/exportable.

Sequencing: P1 → P2 → P3 → P4. P1–P3 are the "simple but extensible" first release; P4 unlocks the
"complex customization" the owner wants without over-building it up front.

---

## Future additions (what else this feature can grow into)
- **Accessibility variants**: a high-contrast theme and an explicit colorblind-safe data palette
  (the validator already gates CVD; add a dedicated CVD-max theme). Respect `prefers-reduced-motion`
  for graph/force-layout animation, and a forced-colors / print mode.
- **Density + typography tokens**: compact/comfortable spacing, font family + scale — already
  reserved in the schema, expose in the picker.
- **Per-view / per-encoding palettes**: a distinct categorical set for topic vs shelf vs status
  coloring in the graphs, so different `color_by` modes don't collide.
- **Auto light/dark**: follow system, or time-of-day switching between a theme's light/dark members.
- **In-app theme editor**: live-edit tokens with preview, export the YAML — turns "hand-edit a file"
  into a GUI while keeping YAML as the interchange format.
- **Theme gallery / import-export**: share themes as YAML; a small bundled gallery beyond the first 4
  (e.g. the other Catppuccin flavors, a solarized pair, a monochrome).
- **Status/semantic consistency**: drive toasts, badges, the queue-health semaphore, and graph
  warning rings from the same `status` tokens so state color is consistent everywhere.
- **Chart-surface independence**: allow a graph background slightly different from the GUI surface
  when a near-neutral field reads better for dense networks (schema already separates `graph.surface`).

## Notes
- Keep YAML as the source of truth; CSS vars + the JS theme object are *generated*, never hand-kept
  in parallel.
- The dataviz validator (`dataviz/scripts/validate_palette.js`) is a required build check for every
  bundled theme's data palette — wire it into `make frontend-check` or a pre-commit step so a theme
  that regresses readability can't ship silently.
