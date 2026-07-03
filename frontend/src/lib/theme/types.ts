// Theme model. Themes are authored as YAML under `frontend/themes/` and compiled
// into `themes.generated.ts` by `scripts/build-themes.mjs`. These interfaces are the
// single source of truth for the compiled shape; both the CSS emitter (`css.ts`) and
// the chart theme bridge (`../viz/theme.ts`) consume them.

export type ThemeMode = 'light' | 'dark';

/** Role tokens — become CSS custom properties on `[data-theme="<id>"]`. */
export interface ThemeTokens {
  surface: { base: string; raised: string; overlay: string; sunken: string; hover: string };
  ink: { strong: string; normal: string; muted: string; inverse: string };
  border: { normal: string; strong: string; focus: string };
  accent: { primary: string; 'primary-strong': string; secondary: string; link: string };
  status: { success: string; warning: string; danger: string; info: string };
  radius: { sm: string; md: string };
  font: { family: string };
}

/** Chart/network palette. Feeds `VizTheme` for ECharts + Cytoscape renderers. */
export interface ThemeGraph {
  surface: string;
  text: string;
  axis_line: string;
  split_line: string;
  tooltip_bg: string;
  tooltip_text: string;
  font: string;
  categorical: string[];
}

export interface Theme {
  id: string;
  name: string;
  mode: ThemeMode;
  temperature: string;
  tokens: ThemeTokens;
  graph: ThemeGraph;
}
