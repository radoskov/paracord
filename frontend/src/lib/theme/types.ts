// Theme model. Themes are authored as YAML under `frontend/themes/` and compiled
// into `themes.generated.ts` by `scripts/build-themes.mjs`. These interfaces are the
// single source of truth for the compiled shape; both the CSS emitter (`css.ts`) and
// the chart theme bridge (`../viz/theme.ts`) consume them.

export type ThemeMode = 'light' | 'dark';

/** Role tokens — become CSS custom properties on `[data-theme="<id>"]`. */
export interface ThemeTokens {
  surface: {
    base: string;
    raised: string;
    overlay: string;
    sunken: string;
    hover: string;
    /** Selected/active list-row background — accent-tinted, distinct from both base and hover. */
    selected: string;
    'selected-border': string;
    'selected-text': string;
  };
  ink: { strong: string; normal: string; muted: string; inverse: string };
  border: { normal: string; strong: string; focus: string };
  accent: {
    primary: string;
    'primary-strong': string;
    secondary: string;
    link: string;
    /** Decorative "note" accent (AI/semantic/tag chips) + its tint bg/border. */
    note: string;
    'note-bg': string;
    'note-border': string;
  };
  /**
   * Status roles. Each of success/warning/danger/info carries an emphasis colour
   * (readable colored text/dot on the surface), a tint background and a tint border
   * so badges/panels theme without per-component literals.
   */
  status: {
    success: string;
    'success-bg': string;
    'success-border': string;
    warning: string;
    'warning-bg': string;
    'warning-border': string;
    danger: string;
    'danger-bg': string;
    'danger-border': string;
    info: string;
    'info-bg': string;
    'info-border': string;
  };
  radius: { sm: string; md: string };
  font: { family: string };
}

/** Chart/network palette. Feeds `VizTheme` for ECharts + Cytoscape renderers. */
export interface ThemeGraph {
  surface: string;
  text: string;
  axis_line: string;
  split_line: string;
  /** Grid / secondary line colour for the network view. */
  grid: string;
  /** Default node fill (uncoloured / external nodes) for the network view. */
  node_default: string;
  /** Edge colour for the network view. */
  edge: string;
  tooltip_bg: string;
  tooltip_text: string;
  /** Ring colour for nodes carrying a review warning (graph depth §8.9). */
  warning_ring: string;
  font: string;
  /** Validated CVD-safe categorical palette (fixed order). */
  categorical: string[];
  /** One-hue light→dark ramp for sequential encodings. */
  sequential: string[];
  /** Two poles + neutral mid for diverging encodings. */
  diverging: { low: string; mid: string; high: string };
}

export interface Theme {
  id: string;
  name: string;
  mode: ThemeMode;
  temperature: string;
  tokens: ThemeTokens;
  graph: ThemeGraph;
}
