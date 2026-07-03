import { describe, expect, it } from 'vitest';

import { colorForGroup, resolveTheme, type VizTheme } from './theme';

// Theming P1: `resolveTheme` now derives from the YAML-compiled theme objects instead of
// a hardcoded palette. These baselines are the EXACT pre-refactor constants that used to
// live in theme.ts — resolveTheme must still return them byte-for-byte for both modes.

const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

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

const OLD_LIGHT: VizTheme = {
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

const OLD_DARK: VizTheme = {
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

describe('resolveTheme', () => {
  it('returns the pre-refactor light theme byte-for-byte', () => {
    expect(resolveTheme('light')).toEqual(OLD_LIGHT);
  });

  it('returns the pre-refactor dark theme byte-for-byte', () => {
    expect(resolveTheme('dark')).toEqual(OLD_DARK);
  });

  it('defaults to light', () => {
    expect(resolveTheme()).toEqual(OLD_LIGHT);
  });
});

describe('colorForGroup', () => {
  const theme = resolveTheme('light');

  it('maps a null group to the first categorical color', () => {
    expect(colorForGroup(theme, null, [])).toBe(CATEGORICAL[0]);
  });

  it('maps groups by ordered index and cycles', () => {
    const groups = ['a', 'b', 'c'];
    expect(colorForGroup(theme, 'b', groups)).toBe(CATEGORICAL[1]);
    expect(colorForGroup(theme, 'missing', groups)).toBe(CATEGORICAL[0]);
  });
});
