import { describe, expect, it } from 'vitest';

import { renderThemeCss, tokenEntries } from './css';
import { applyTheme, getTheme, themeForMode } from './index';
import { bundledThemes } from './themes.generated';

// Theming P1 guarantees NO visual change. These tests pin the ported `default` theme's
// emitted token VALUES to the exact colours the app used before tokenisation, so any
// future edit that shifts the look fails here.

// The `--pg-*` ad-hoc vars + hardcoded colours the P1 refactor replaced, keyed by the
// role token that now supplies each value. Values are the pre-refactor literals.
const OLD_VALUES: Record<string, string> = {
  '--surface-base': '#eef1f4', //         was --pg-bg / body background
  '--surface-raised': '#fbfcfd', //       was --pg-surface / .card background
  '--surface-overlay': '#ffffff', //      was --pg-secondary-bg / input `white`
  '--surface-sunken': '#f4f6f9', //       .empty / chip background
  '--surface-hover': '#eef2f6', //        was --pg-secondary-hover
  '--ink-strong': '#1f2a36', //           was --pg-text
  '--ink-normal': '#203142', //           body colour
  '--ink-muted': '#64717f', //            was --pg-muted / .muted
  '--ink-inverse': '#ffffff', //          was --pg-on-primary
  '--border-normal': '#cbd5e1', //        was --pg-border
  '--border-strong': '#d8dee6', //        .card / header border
  '--border-focus': '#2563eb',
  '--accent-primary': '#2d3e50', //       was --pg-primary
  '--accent-primary-strong': '#1f2a36', //was --pg-primary-hover
  '--accent-secondary': '#21303d', //     was --pg-secondary-text
  '--accent-link': '#2563eb',
  '--status-success': '#166534',
  '--status-warning': '#b45309',
  '--status-danger': '#b3261e', //        was --pg-danger / .danger
  '--status-info': '#1d4ed8',
  '--radius-sm': '6px', //                button / input radius
  '--radius-md': '8px', //                .card radius
  '--font-family':
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
};

describe('default theme tokens', () => {
  const theme = getTheme('default');

  it('is bundled', () => {
    expect(theme).toBeDefined();
    expect(theme?.mode).toBe('light');
  });

  it('emits exactly the pre-refactor token values (no visual change)', () => {
    const map = Object.fromEntries(tokenEntries(theme!.tokens));
    expect(map).toEqual(OLD_VALUES);
  });

  it('scopes the token block to [data-theme="default"]', () => {
    const css = renderThemeCss(theme!);
    expect(css.startsWith('[data-theme="default"] {')).toBe(true);
    expect(css).toContain('--surface-base: #eef1f4;');
    expect(css).toContain('--accent-primary: #2d3e50;');
  });

  it('applyTheme sets data-theme and injects the token style element', () => {
    applyTheme('default');
    expect(document.documentElement.getAttribute('data-theme')).toBe('default');
    const style = document.getElementById('paracord-theme-tokens');
    expect(style?.textContent).toContain('--surface-base: #eef1f4;');
  });
});

describe('theme registry', () => {
  it('resolves a light and a dark theme by mode', () => {
    expect(themeForMode('light').id).toBe('default');
    expect(themeForMode('dark').id).toBe('default-dark');
  });

  it('every bundled theme has the full role-token set', () => {
    for (const t of bundledThemes) {
      const names = tokenEntries(t.tokens).map(([n]) => n);
      expect(names).toContain('--surface-base');
      expect(names).toContain('--ink-strong');
      expect(names).toContain('--status-danger');
    }
  });
});
