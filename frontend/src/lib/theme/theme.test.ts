import { describe, expect, it } from 'vitest';

import { renderThemeCss, tokenEntries } from './css';
import { applyTheme, getTheme, themeForMode } from './index';
import { validateCategorical } from './paletteCheck';
import { bundledThemes } from './themes.generated';

// Theming P2: four validated Catppuccin-derived themes. These tests assert every theme
// is STRUCTURALLY complete (no undefined/empty role token, the `--muted` alias present)
// and that each theme's `graph.categorical` data palette passes the CVD validator on its
// own surface — so switching themes recolours the whole app and never ships an
// unreadable graph palette.

const EXPECTED_TOKENS = [
  '--surface-base',
  '--surface-raised',
  '--surface-overlay',
  '--surface-sunken',
  '--surface-hover',
  '--ink-strong',
  '--ink-normal',
  '--ink-muted',
  '--ink-inverse',
  '--border-normal',
  '--border-strong',
  '--border-focus',
  '--accent-primary',
  '--accent-primary-strong',
  '--accent-secondary',
  '--accent-link',
  '--accent-note',
  '--accent-note-bg',
  '--accent-note-border',
  '--status-success',
  '--status-success-bg',
  '--status-success-border',
  '--status-warning',
  '--status-warning-bg',
  '--status-warning-border',
  '--status-danger',
  '--status-danger-bg',
  '--status-danger-border',
  '--status-info',
  '--status-info-bg',
  '--status-info-border',
  '--radius-sm',
  '--radius-md',
  '--font-family',
];

const EXPECTED_IDS = ['latte-warm', 'latte-cool', 'mocha-warm', 'mocha-cool'];

describe('bundled themes', () => {
  it('bundles the four P2 themes (2 light + 2 dark)', () => {
    const ids = bundledThemes.map((t) => t.id).sort();
    expect(ids).toEqual([...EXPECTED_IDS].sort());
    expect(bundledThemes.filter((t) => t.mode === 'light').map((t) => t.id).sort()).toEqual([
      'latte-cool',
      'latte-warm',
    ]);
    expect(bundledThemes.filter((t) => t.mode === 'dark').map((t) => t.id).sort()).toEqual([
      'mocha-cool',
      'mocha-warm',
    ]);
  });

  it('every theme defines the full role-token set, all non-empty', () => {
    const HEX_OR_UNIT = /^(#|var\(|-?[\d.]|Inter)/;
    for (const theme of bundledThemes) {
      const map = Object.fromEntries(tokenEntries(theme.tokens));
      for (const name of EXPECTED_TOKENS) {
        expect(map[name], `${theme.id} ${name}`).toBeDefined();
        expect(String(map[name]).trim().length, `${theme.id} ${name} non-empty`).toBeGreaterThan(0);
        expect(String(map[name]), `${theme.id} ${name} looks like a value`).toMatch(HEX_OR_UNIT);
      }
    }
  });

  it('emits the --muted alias (→ --ink-muted) for every theme', () => {
    for (const theme of bundledThemes) {
      const css = renderThemeCss(theme);
      expect(css, theme.id).toContain(`--muted: ${theme.tokens.ink.muted};`);
      expect(css.startsWith(`[data-theme="${theme.id}"] {`)).toBe(true);
    }
  });

  it('every theme carries a complete graph block (network + ramps)', () => {
    for (const g of bundledThemes.map((t) => t.graph)) {
      expect(g.categorical.length).toBeGreaterThanOrEqual(8);
      expect(g.sequential.length).toBeGreaterThanOrEqual(3);
      for (const v of [
        g.surface,
        g.text,
        g.node_default,
        g.edge,
        g.grid,
        g.warning_ring,
        g.diverging.low,
        g.diverging.mid,
        g.diverging.high,
      ]) {
        expect(v).toMatch(/^#/);
      }
    }
  });
});

describe('categorical data-palette validation', () => {
  // Records each theme's validator verdict; a regression that pushes a palette below the
  // 8–12 CVD floor (or out of the lightness/chroma bands) fails here.
  for (const theme of bundledThemes) {
    it(`${theme.id} categorical palette passes on its surface`, () => {
      const report = validateCategorical(theme.graph.categorical, {
        mode: theme.mode,
        surface: theme.graph.surface,
      });
      expect(report.offBand, `${theme.id} lightness band`).toEqual([]);
      expect(report.lowChroma, `${theme.id} chroma floor`).toEqual([]);
      expect(report.cvdState, `${theme.id} CVD ${report.worstCvd.toFixed(1)}`).not.toBe('fail');
      expect(report.worstCvd).toBeGreaterThanOrEqual(8);
      expect(report.ok).toBe(true);
    });
  }
});

describe('theme registry', () => {
  it('resolves a light and a dark theme by mode', () => {
    expect(themeForMode('light').mode).toBe('light');
    expect(themeForMode('dark').mode).toBe('dark');
  });

  it('looks themes up by id', () => {
    expect(getTheme('mocha-warm')?.name).toBe('Mocha (warm)');
    expect(getTheme('nope')).toBeUndefined();
  });

  it('applyTheme sets data-theme and injects the token style element', () => {
    applyTheme('latte-warm');
    expect(document.documentElement.getAttribute('data-theme')).toBe('latte-warm');
    const style = document.getElementById('paracord-theme-tokens');
    expect(style?.textContent).toContain('--surface-base:');
    expect(style?.textContent).toContain('--muted:');
  });

  it('falls back to the default theme for an unknown id', () => {
    const theme = applyTheme('does-not-exist');
    expect(theme.id).toBe('latte-warm');
  });
});
