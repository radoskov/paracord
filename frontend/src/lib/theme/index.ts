// Theme registry + runtime application. Bundled themes come from the YAML-compiled
// `themes.generated.ts`. `applyTheme` injects the active theme's token CSS and sets
// `data-theme` on <html> — the same attribute the chart pages already read.

import { renderThemeCss } from './css';
import { bundledThemes } from './themes.generated';
import type { Theme, ThemeMode } from './types';

export type { Theme, ThemeMode, ThemeTokens, ThemeGraph } from './types';
export { renderThemeCss, tokenEntries, aliasEntries } from './css';
export { bundledThemes };

// Boot default (P2): the warm light theme. P3 wires the picker + persistence; until
// then the app boots to this theme.
export const DEFAULT_THEME_ID = 'latte-warm';

// Runtime registry of custom (admin-uploaded, P4) themes, resolved from the backend and merged in
// alongside the compiled bundled themes. Populated by the theme store's `loadCustomThemes`; keyed
// by theme id so `getTheme`/`applyTheme`/`resolveThemeById` treat a custom theme exactly like a
// bundled one (renderThemeCss + VizTheme bridge need no special-casing).
const customThemes = new Map<string, Theme>();

/** Register (or replace) a resolved custom theme so it can be applied like a bundled one. */
export function registerCustomTheme(theme: Theme): void {
  customThemes.set(theme.id, theme);
}

/** All currently-known themes (bundled first, then registered custom ones). */
export function allThemes(): Theme[] {
  return [...bundledThemes, ...customThemes.values()];
}

export function getTheme(id: string): Theme | undefined {
  return bundledThemes.find((t) => t.id === id) ?? customThemes.get(id);
}

/** First bundled theme for a mode (used by the chart theme bridge). */
export function themeForMode(mode: ThemeMode): Theme {
  return bundledThemes.find((t) => t.mode === mode) ?? bundledThemes[0];
}

const STYLE_ELEMENT_ID = 'paracord-theme-tokens';

/**
 * Apply a theme: inject its token CSS once and set `<html data-theme>`. Falls back to
 * the default theme for an unknown id. Safe to call repeatedly (updates in place).
 */
export function applyTheme(id: string = DEFAULT_THEME_ID): Theme {
  const theme = getTheme(id) ?? getTheme(DEFAULT_THEME_ID) ?? bundledThemes[0];
  let style = document.getElementById(STYLE_ELEMENT_ID) as HTMLStyleElement | null;
  if (!style) {
    style = document.createElement('style');
    style.id = STYLE_ELEMENT_ID;
    document.head.appendChild(style);
  }
  style.textContent = renderThemeCss(theme);
  document.documentElement.setAttribute('data-theme', theme.id);
  return theme;
}
