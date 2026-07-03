// Shared visualization theme (D38 P2). One accessible, Seaborn-like palette plus light/dark
// surface colors and fonts, applied to every chart so the whole viz module reads as one system.
// P3-P5 renderers reuse this — do not hardcode colors in a renderer.

export interface VizTheme {
  mode: 'light' | 'dark';
  background: string;
  text: string;
  axisLine: string;
  splitLine: string;
  tooltipBg: string;
  tooltipText: string;
  fontFamily: string;
  // Accessible categorical palette (muted, Seaborn "deep"-like) for color_group encoding.
  categorical: string[];
}

const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

// Colorblind-aware categorical palette (Seaborn "deep", reordered for max separation).
const CATEGORICAL = [
  '#4c72b0',
  '#dd8452',
  '#55a868',
  '#c44e52',
  '#8172b3',
  '#937860',
  '#da8bc3',
  '#8c8c8c',
  '#ccb974',
  '#64b5cd',
];

const LIGHT: VizTheme = {
  mode: 'light',
  background: '#ffffff',
  text: '#21303d',
  axisLine: '#bcc7d2',
  splitLine: '#eef2f6',
  tooltipBg: '#1f2a36',
  tooltipText: '#ffffff',
  fontFamily: FONT_FAMILY,
  categorical: CATEGORICAL,
};

const DARK: VizTheme = {
  mode: 'dark',
  background: '#141a21',
  text: '#e6edf3',
  axisLine: '#3a4650',
  splitLine: '#232c35',
  tooltipBg: '#e6edf3',
  tooltipText: '#141a21',
  fontFamily: FONT_FAMILY,
  categorical: CATEGORICAL,
};

/** Resolve the shared theme for a light/dark mode. */
export function resolveTheme(mode: 'light' | 'dark' = 'light'): VizTheme {
  return mode === 'dark' ? DARK : LIGHT;
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
