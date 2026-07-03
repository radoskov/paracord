import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { getTheme } from './index';
import {
  activeThemeId,
  activeVizTheme,
  readCachedThemeId,
  reconcileTheme,
  setTheme,
  themeOptions,
  THEME_BG_STORAGE_KEY,
  THEME_STORAGE_KEY,
} from './store';
import { resolveThemeById } from '../viz/theme';

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
