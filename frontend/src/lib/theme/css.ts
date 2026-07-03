// Emit a theme's role tokens as a CSS custom-property block scoped to
// `[data-theme="<id>"]`. Pure and deterministic so it can be snapshot-tested and
// injected at runtime (see `index.ts`). Variable names follow `--<group>-<key>`,
// e.g. surface.base -> `--surface-base`, font.family -> `--font-family`.

import type { Theme, ThemeTokens } from './types';

/** Ordered `[--var, value]` pairs for a theme's tokens (stable for snapshots). */
export function tokenEntries(tokens: ThemeTokens): Array<[string, string]> {
  const entries: Array<[string, string]> = [];
  for (const [group, values] of Object.entries(tokens)) {
    for (const [key, value] of Object.entries(values as Record<string, string>)) {
      entries.push([`--${group}-${key}`, value]);
    }
  }
  return entries;
}

/** A `[data-theme="<id>"] { … }` rule declaring every role token. */
export function renderThemeCss(theme: Theme): string {
  const body = tokenEntries(theme.tokens)
    .map(([name, value]) => `  ${name}: ${value};`)
    .join('\n');
  return `[data-theme="${theme.id}"] {\n${body}\n}`;
}
