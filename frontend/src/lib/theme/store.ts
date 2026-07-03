// Reactive theme state + persistence for the live theme switcher (P3). The picker and the
// visualization components subscribe to `activeThemeId` / `activeVizTheme`; changing it restyles
// the WHOLE running app with no reload — GUI tokens flip via `applyTheme` (data-theme + injected
// CSS vars, already reactive) and the ECharts/Cytoscape views re-read the resolved theme.
//
// Persistence priority for boot: localStorage cache → server `theme` (from /auth/me) → default.
// The cache is written on every change and read before first paint (see main.ts / index.html) for
// a no-flash boot; `reconcileTheme` adopts the server value when there's no local cache.

import { derived, get, writable } from 'svelte/store';

import { applyTheme, DEFAULT_THEME_ID, getTheme, registerCustomTheme } from './index';
import { bundledThemes } from './themes.generated';
import type { Theme, ThemeMode } from './types';
import { resolveThemeById, type VizTheme } from '../viz/theme';

export const THEME_STORAGE_KEY = 'paracord-theme';
// Cached surface colour so the inline head script can paint the correct background before the
// token stylesheet is injected — this is what actually kills the light↔dark boot flash.
export const THEME_BG_STORAGE_KEY = 'paracord-theme-bg';
export const THEME_FOLLOW_STORAGE_KEY = 'paracord-theme-follow';

export interface ThemeOption {
  id: string;
  name: string;
  mode: ThemeMode;
  temperature: string;
  /** Representative colours for a swatch preview (surface + primary + a few graph accents). */
  swatch: { surface: string; primary: string; accents: string[] };
}

/** Data-driven picker list — authoring a 5th theme YAML makes it appear here automatically. */
export const themeOptions: ThemeOption[] = bundledThemes.map((t) => ({
  id: t.id,
  name: t.name,
  mode: t.mode,
  temperature: t.temperature,
  swatch: {
    surface: t.tokens.surface.base,
    primary: t.tokens.accent.primary,
    accents: t.graph.categorical.slice(0, 4),
  },
}));

/**
 * Admin-uploaded custom themes (P4), fetched from `GET /themes` on boot and merged into the picker
 * alongside the bundled themes. Each item is a ready-to-render `ThemeOption` (the backend already
 * shapes id/name/mode/temperature + a swatch). Empty until `loadCustomThemes` runs.
 */
export const customThemeOptions = writable<ThemeOption[]>([]);

/** Bundled + custom themes for the picker — reactive so a newly-uploaded theme appears live. */
export const allThemeOptions = derived(customThemeOptions, ($custom) => [
  ...themeOptions,
  ...$custom,
]);

/** Minimal backend surface the theme store needs (satisfied by `ApiClient`; keeps the store testable). */
export interface ThemeApi {
  listThemes(): Promise<ThemeOption[]>;
  getTheme(id: string): Promise<Theme>;
}

/**
 * Ensure a theme's full object is registered so it can be applied. Bundled + already-fetched custom
 * themes are no-ops; an unresolved custom theme is fetched from the backend and registered.
 */
export async function ensureThemeLoaded(api: ThemeApi, id: string): Promise<void> {
  if (getTheme(id)) return;
  registerCustomTheme(await api.getTheme(id));
}

/**
 * Fetch the custom themes for the picker and, when the theme the user actually wants (localStorage
 * cache → server-persisted) is a custom one, resolve + register + apply it so a custom theme boots
 * live exactly like a bundled one. Best-effort: a failed fetch leaves the bundled themes intact.
 */
export async function loadCustomThemes(
  api: ThemeApi,
  persistedThemeId?: string | null,
): Promise<void> {
  let list: ThemeOption[];
  try {
    list = await api.listThemes();
  } catch {
    return;
  }
  customThemeOptions.set(list);
  const wantId = readCachedThemeId() ?? persistedThemeId ?? null;
  if (wantId && list.some((o) => o.id === wantId)) {
    try {
      await ensureThemeLoaded(api, wantId);
      // Apply if it isn't already the live theme (initTheme may have fallen back to the default
      // while the custom object was still unresolved).
      if (get(activeThemeId) !== wantId) setTheme(wantId);
    } catch {
      // Leave the fallback theme applied if the resolved object can't be fetched.
    }
  }
}

/** The active theme id — drives `data-theme` and every subscribed viz component's palette. */
export const activeThemeId = writable<string>(DEFAULT_THEME_ID);

/** The fully-resolved active theme object (GUI tokens + graph palette). */
export const activeTheme = derived(
  activeThemeId,
  ($id): Theme => getTheme($id) ?? getTheme(DEFAULT_THEME_ID) ?? bundledThemes[0],
);

/** The active theme mapped to the chart-facing `VizTheme` — what the renderers re-read on change. */
export const activeVizTheme = derived(activeThemeId, ($id): VizTheme => resolveThemeById($id));

/** Whether the app follows the OS light/dark preference (device-local, not server-persisted). */
export const followSystem = writable<boolean>(false);

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // localStorage may be unavailable (private mode) — the store still works in-memory.
  }
}

function cacheTheme(theme: Theme): void {
  safeSet(THEME_STORAGE_KEY, theme.id);
  safeSet(THEME_BG_STORAGE_KEY, theme.tokens.surface.base);
}

/** Read the cached theme id from localStorage (null when unset/unavailable). */
export function readCachedThemeId(): string | null {
  return safeGet(THEME_STORAGE_KEY);
}

/**
 * Switch the active theme: apply the GUI tokens + `data-theme`, cache for no-flash boot, and
 * publish to the store so subscribed viz components restyle live. Falls back to the default for an
 * unknown id. Returns the applied theme.
 */
export function setTheme(id: string | null | undefined): Theme {
  const theme = applyTheme(id ?? DEFAULT_THEME_ID);
  cacheTheme(theme);
  activeThemeId.set(theme.id);
  return theme;
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false;
}

/** The bundled theme for a given mode + temperature (used when following the OS appearance). */
function themeByModeTemperature(mode: ThemeMode, temperature: string): Theme | undefined {
  return bundledThemes.find((t) => t.mode === mode && t.temperature === temperature);
}

/** Re-pick the light/dark member of the current temperature pair from the OS preference. */
function applySystemMode(): void {
  const current = get(activeTheme);
  const mode: ThemeMode = systemPrefersDark() ? 'dark' : 'light';
  const next = themeByModeTemperature(mode, current.temperature) ?? current;
  setTheme(next.id);
}

let mediaQuery: MediaQueryList | null = null;
function onSystemChange(): void {
  if (get(followSystem)) applySystemMode();
}

/**
 * Enable/disable following the OS appearance. When enabled, the current temperature pair's
 * light/dark member is chosen from `prefers-color-scheme` and kept in sync with OS changes.
 * A manual theme pick disables this (the picker calls `setTheme` + `setFollowSystem(false)`).
 */
export function setFollowSystem(on: boolean): void {
  safeSet(THEME_FOLLOW_STORAGE_KEY, on ? '1' : '0');
  followSystem.set(on);
  if (on) applySystemMode();
}

/**
 * Boot the theme before first paint: prefer the localStorage cache, else the default. Wires the
 * OS-preference listener and, when follow-system was enabled, resolves the mode from the OS.
 * Call once from main.ts; reconcile with the server value via `reconcileTheme` after /auth/me.
 */
export function initTheme(): Theme {
  const follow = safeGet(THEME_FOLLOW_STORAGE_KEY) === '1';
  followSystem.set(follow);
  const applied = setTheme(readCachedThemeId() ?? DEFAULT_THEME_ID);

  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', onSystemChange);
  }
  if (follow) {
    applySystemMode();
    return get(activeTheme);
  }
  return applied;
}

/**
 * Reconcile the persisted server theme with the local cache once /auth/me returns. Per the boot
 * priority the localStorage cache (a deliberate choice on this device) wins when present;
 * otherwise adopt the server value. No-op while following the OS appearance.
 */
export function reconcileTheme(serverThemeId: string | null | undefined): void {
  if (get(followSystem)) return;
  if (readCachedThemeId()) return;
  if (serverThemeId && getTheme(serverThemeId)) setTheme(serverThemeId);
}
