// Shared visualization theme (D38 P2). One accessible palette plus surface colors and
// fonts, applied to every chart so the whole viz module reads as one system.
// P3-P5 renderers reuse this — do not hardcode colors in a renderer.
//
// Theming P1: the values are now derived from the YAML-compiled theme objects
// (`lib/theme`) — the same source that drives the GUI tokens — instead of a hardcoded
// palette here. `resolveTheme(mode)` returns the bundled theme for that mode's `graph`.

import { getTheme } from '../theme';
import { bundledThemes } from '../theme/themes.generated';
import type { Theme } from '../theme/types';

export interface VizTheme {
  mode: 'light' | 'dark';
  background: string;
  text: string;
  axisLine: string;
  splitLine: string;
  tooltipBg: string;
  tooltipText: string;
  fontFamily: string;
  // Validated CVD-safe categorical palette (fixed order) for color_group encoding.
  categorical: string[];
  // One-hue light→dark ramp for sequential (heatmap / density) encodings.
  sequential: string[];
  // Two poles + neutral mid for diverging encodings.
  diverging: { low: string; mid: string; high: string };
  // Network (Cytoscape) colours derived from the theme's `graph` block.
  nodeDefault: string;
  edge: string;
  grid: string;
  warningRing: string;
}

/** Map a theme's `graph` section onto the chart-facing VizTheme shape. */
function toVizTheme(theme: Theme): VizTheme {
  const g = theme.graph;
  return {
    mode: theme.mode,
    background: g.surface,
    text: g.text,
    axisLine: g.axis_line,
    splitLine: g.split_line,
    tooltipBg: g.tooltip_bg,
    tooltipText: g.tooltip_text,
    fontFamily: g.font,
    categorical: g.categorical,
    sequential: g.sequential,
    diverging: g.diverging,
    nodeDefault: g.node_default,
    edge: g.edge,
    grid: g.grid,
    warningRing: g.warning_ring,
  };
}

function themeForMode(mode: 'light' | 'dark'): Theme {
  return bundledThemes.find((t) => t.mode === mode) ?? bundledThemes[0];
}

/** Resolve the shared theme for a light/dark mode (first bundled theme of that mode). */
export function resolveTheme(mode: 'light' | 'dark' = 'light'): VizTheme {
  return toVizTheme(themeForMode(mode));
}

/**
 * Resolve the VizTheme for the ACTIVE theme id (the `data-theme` on <html>). This is
 * what the chart pages use so each of the bundled themes' `graph` blocks drives the
 * charts — falls back to the first bundled theme for an unknown/absent id.
 */
export function resolveThemeById(id: string | null): VizTheme {
  const theme = (id ? getTheme(id) : undefined) ?? bundledThemes[0];
  return toVizTheme(theme);
}

/** Deterministic color for a color_group value, given the ordered group list from the payload. */
export function colorForGroup(
  theme: VizTheme,
  group: string | null,
  groups: string[],
): string {
  if (group === null) return theme.categorical[0];
  const index = groups.indexOf(group);
  return theme.categorical[(index < 0 ? 0 : index) % theme.categorical.length];
}
