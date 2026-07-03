import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { getTheme } from './index';
import {
  activeThemeId,
  activeVizTheme,
  allThemeOptions,
  customThemeOptions,
  ensureThemeLoaded,
  loadCustomThemes,
  readCachedThemeId,
  reconcileTheme,
  setTheme,
  themeOptions,
  type ThemeApi,
  type ThemeOption,
  THEME_BG_STORAGE_KEY,
  THEME_STORAGE_KEY,
} from './store';
import type { Theme } from './types';
import { resolveThemeById } from '../viz/theme';

// A resolved custom theme (dark) with a distinct categorical palette so we can assert the live
// restyle picks it up. Structurally identical to a bundled theme.
const CUSTOM_THEME: Theme = {
  id: 'ocean-dusk',
  name: 'Ocean Dusk',
  mode: 'dark',
  temperature: 'cool',
  tokens: {
    surface: { base: '#0d1b2a', raised: '#12263a', overlay: '#1b3a55', sunken: '#081420', hover: '#163049' },
    ink: { strong: '#e0e8f0', normal: '#c3d0dd', muted: '#8fa3b5', inverse: '#0d1b2a' },
    border: { normal: '#22384a', strong: '#33506a', focus: '#4aa3df' },
    accent: {
      primary: '#4aa3df',
      'primary-strong': '#2e86c4',
      secondary: '#c3d0dd',
      link: '#4aa3df',
      note: '#a06fd6',
      'note-bg': '#1e1633',
      'note-border': '#3a2c5c',
    },
    status: {
      success: '#4fbf7a',
      'success-bg': '#0f2a1a',
      'success-border': '#245c3a',
      warning: '#e0b64a',
      'warning-bg': '#2e2410',
      'warning-border': '#5c4a20',
      danger: '#e05a68',
      'danger-bg': '#2e1418',
      'danger-border': '#5c2833',
      info: '#4aa3df',
      'info-bg': '#0f242e',
      'info-border': '#245060',
    },
    radius: { sm: '6px', md: '8px' },
    font: { family: 'Inter, sans-serif' },
  },
  graph: {
    surface: '#0d1b2a',
    text: '#c3d0dd',
    axis_line: '#33506a',
    split_line: '#22384a',
    grid: '#22384a',
    node_default: '#8fa3b5',
    edge: '#33506a',
    tooltip_bg: '#e0e8f0',
    tooltip_text: '#0d1b2a',
    warning_ring: '#e05a68',
    font: 'Inter, sans-serif',
    categorical: ['#cf7020', '#4a7fd0', '#e04a68', '#1a9a9a'],
    sequential: ['#cf7020', '#4a7fd0'],
    diverging: { low: '#cf7020', mid: '#22384a', high: '#e04a68' },
  },
};

const OPTION: ThemeOption = {
  id: 'ocean-dusk',
  name: 'Ocean Dusk',
  mode: 'dark',
  temperature: 'cool',
  swatch: { surface: '#0d1b2a', primary: '#4aa3df', accents: CUSTOM_THEME.graph.categorical },
};

function fakeApi(overrides: Partial<ThemeApi> = {}): ThemeApi {
  return {
    listThemes: async () => [OPTION],
    getTheme: async () => CUSTOM_THEME,
    ...overrides,
  };
}

describe('theme store options', () => {
  it('exposes a data-driven option per bundled theme with mode + temperature', () => {
    expect(themeOptions.length).toBeGreaterThanOrEqual(4);
    for (const opt of themeOptions) {
      expect(getTheme(opt.id)).toBeDefined();
      expect(['light', 'dark']).toContain(opt.mode);
      expect(opt.temperature).toBeTruthy();
      expect(opt.swatch.accents.length).toBeGreaterThan(0);
    }
    // Both modes are represented so the picker can group Light / Dark.
    expect(themeOptions.some((t) => t.mode === 'light')).toBe(true);
    expect(themeOptions.some((t) => t.mode === 'dark')).toBe(true);
  });
});

describe('setTheme live switch', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('updates data-theme, caches locally, and publishes the resolved VizTheme the renderers use', () => {
    setTheme('mocha-cool');
    expect(document.documentElement.getAttribute('data-theme')).toBe('mocha-cool');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('mocha-cool');
    // The cached background is the theme's surface base (drives the no-flash boot script).
    expect(localStorage.getItem(THEME_BG_STORAGE_KEY)).toBe(getTheme('mocha-cool')!.tokens.surface.base);

    expect(get(activeThemeId)).toBe('mocha-cool');
    // The store's VizTheme matches exactly what the ECharts/Cytoscape renderers would resolve.
    expect(get(activeVizTheme)).toEqual(resolveThemeById('mocha-cool'));
    expect(get(activeVizTheme).categorical).toEqual(getTheme('mocha-cool')!.graph.categorical);

    // Switching again re-maps the resolved VizTheme (the live-restyle path).
    setTheme('latte-warm');
    expect(get(activeVizTheme)).toEqual(resolveThemeById('latte-warm'));
    expect(get(activeVizTheme).mode).toBe('light');
  });

  it('falls back to the default theme for an unknown id', () => {
    setTheme('does-not-exist');
    expect(get(activeThemeId)).toBe('latte-warm');
  });
});

describe('reconcileTheme (server value)', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('adopts the server theme when this device has no cached choice', () => {
    expect(readCachedThemeId()).toBeNull();
    reconcileTheme('mocha-warm');
    expect(get(activeThemeId)).toBe('mocha-warm');
    expect(readCachedThemeId()).toBe('mocha-warm');
  });

  it('keeps the local cache when present (localStorage wins over the server)', () => {
    setTheme('latte-cool');
    reconcileTheme('mocha-warm');
    expect(get(activeThemeId)).toBe('latte-cool');
  });

  it('ignores a null / unknown server theme', () => {
    setTheme('latte-warm');
    localStorage.clear();
    reconcileTheme(null);
    reconcileTheme('bogus');
    expect(get(activeThemeId)).toBe('latte-warm');
  });
});

describe('custom themes (P4) — merge into the picker + live apply', () => {
  beforeEach(() => {
    localStorage.clear();
    customThemeOptions.set([]);
    setTheme('latte-warm');
  });
  afterEach(() => {
    localStorage.clear();
    customThemeOptions.set([]);
  });

  it('merges fetched custom themes into allThemeOptions alongside the bundled ones', async () => {
    expect(get(allThemeOptions)).toHaveLength(themeOptions.length);
    await loadCustomThemes(fakeApi());
    const merged = get(allThemeOptions);
    expect(merged).toHaveLength(themeOptions.length + 1);
    expect(merged.some((o) => o.id === 'ocean-dusk')).toBe(true);
    // Bundled options still lead the list.
    expect(merged.slice(0, themeOptions.length)).toEqual(themeOptions);
  });

  it('ensureThemeLoaded registers a fetched custom theme so it applies + restyles live', async () => {
    expect(getTheme('ocean-dusk')).toBeUndefined();
    await ensureThemeLoaded(fakeApi(), 'ocean-dusk');
    expect(getTheme('ocean-dusk')).toEqual(CUSTOM_THEME);

    setTheme('ocean-dusk');
    expect(document.documentElement.getAttribute('data-theme')).toBe('ocean-dusk');
    expect(get(activeThemeId)).toBe('ocean-dusk');
    // The renderers re-read the custom theme's graph palette (the live-restyle path).
    expect(get(activeVizTheme)).toEqual(resolveThemeById('ocean-dusk'));
    expect(get(activeVizTheme).categorical).toEqual(CUSTOM_THEME.graph.categorical);
  });

  it('boots the persisted custom theme when there is no local cache', async () => {
    // No localStorage cache (the beforeEach setTheme wrote one) → the server-persisted custom slug
    // is resolved + applied.
    localStorage.clear();
    await loadCustomThemes(fakeApi(), 'ocean-dusk');
    expect(get(activeThemeId)).toBe('ocean-dusk');
    expect(getTheme('ocean-dusk')).toBeDefined();
  });

  it('is best-effort: a failed list fetch leaves the bundled themes intact', async () => {
    await loadCustomThemes(
      fakeApi({
        listThemes: async () => {
          throw new Error('offline');
        },
      }),
    );
    expect(get(allThemeOptions)).toHaveLength(themeOptions.length);
    expect(get(activeThemeId)).toBe('latte-warm');
  });
});
