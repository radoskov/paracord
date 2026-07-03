import { describe, expect, it } from 'vitest';

import { bundledThemes } from '../theme/themes.generated';
import { colorForGroup, resolveTheme, resolveThemeById } from './theme';

// Theming P2: VizTheme is derived from each theme's `graph` block. These tests check the
// mapping is complete for every bundled theme and that resolveThemeById selects the
// ACTIVE theme (the id the chart pages read from <html data-theme>).

describe('resolveThemeById', () => {
  it('maps every bundled theme graph block onto a complete VizTheme', () => {
    for (const theme of bundledThemes) {
      const viz = resolveThemeById(theme.id);
      expect(viz.mode).toBe(theme.mode);
      expect(viz.background).toBe(theme.graph.surface);
      expect(viz.text).toBe(theme.graph.text);
      expect(viz.categorical).toEqual(theme.graph.categorical);
      expect(viz.sequential).toEqual(theme.graph.sequential);
      expect(viz.diverging).toEqual(theme.graph.diverging);
      expect(viz.nodeDefault).toBe(theme.graph.node_default);
      expect(viz.edge).toBe(theme.graph.edge);
      expect(viz.grid).toBe(theme.graph.grid);
      expect(viz.warningRing).toBe(theme.graph.warning_ring);
    }
  });

  it('falls back to the first bundled theme for an unknown/null id', () => {
    expect(resolveThemeById(null).background).toBe(bundledThemes[0].graph.surface);
    expect(resolveThemeById('nope').background).toBe(bundledThemes[0].graph.surface);
  });
});

describe('resolveTheme (by mode)', () => {
  it('returns a theme of the requested mode', () => {
    expect(resolveTheme('light').mode).toBe('light');
    expect(resolveTheme('dark').mode).toBe('dark');
    expect(resolveTheme().mode).toBe('light');
  });
});

describe('colorForGroup', () => {
  const theme = resolveTheme('light');

  it('maps a null group to the first categorical color', () => {
    expect(colorForGroup(theme, null, [])).toBe(theme.categorical[0]);
  });

  it('maps groups by ordered index and cycles', () => {
    const groups = ['a', 'b', 'c'];
    expect(colorForGroup(theme, 'b', groups)).toBe(theme.categorical[1]);
    expect(colorForGroup(theme, 'missing', groups)).toBe(theme.categorical[0]);
  });
});
