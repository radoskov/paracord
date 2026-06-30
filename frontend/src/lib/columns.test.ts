import { describe, expect, it } from 'vitest';

import {
  DEFAULT_VISIBLE,
  defaultColumnPrefs,
  exceedsSoftCap,
  normalizeColumnPrefs,
  SOFT_COLUMN_CAP,
  visibleColumnDefs,
} from './columns';

describe('column prefs registry + validation', () => {
  it('defaults to the 6 v1 columns and excludes opt-in keywords', () => {
    const prefs = defaultColumnPrefs();
    expect(prefs.visible).toEqual(['title', 'year', 'venue', 'status', 'added_at', 'doi']);
    expect(prefs.visible).not.toContain('keywords');
    expect(DEFAULT_VISIBLE.length).toBe(SOFT_COLUMN_CAP);
  });

  it('drops unknown ids and dedupes order', () => {
    const prefs = normalizeColumnPrefs({
      order: ['title', 'bogus', 'year', 'title'],
      visible: ['title', 'year', 'authors'],
      sort: { key: 'year', order: 'asc' },
    });
    expect(prefs.order).not.toContain('bogus');
    expect(prefs.order.filter((id) => id === 'title')).toHaveLength(1);
    // 'authors' is not a valid v1 column → dropped from visible.
    expect(prefs.visible).not.toContain('authors');
  });

  it('appends registry columns missing from a partial order', () => {
    const prefs = normalizeColumnPrefs({ order: ['doi'], visible: ['doi'] });
    // Every registry column still appears so a newly-added column is never lost.
    expect(prefs.order).toContain('title');
    expect(prefs.order).toContain('keywords');
    expect(prefs.order[0]).toBe('doi'); // user order is honoured first
  });

  it('forces the always-on title column visible', () => {
    const prefs = normalizeColumnPrefs({
      order: ['title', 'year'],
      visible: ['year'], // tried to hide title
      sort: { key: 'title', order: 'asc' },
    });
    expect(prefs.visible).toContain('title');
  });

  it('falls back to default sort for an unknown sort key', () => {
    const prefs = normalizeColumnPrefs({ sort: { key: 'nonsense', order: 'asc' } });
    expect(prefs.sort).toEqual({ key: 'updated_at', order: 'desc' });
  });

  it('returns defaults for garbage input', () => {
    expect(normalizeColumnPrefs(null)).toEqual(defaultColumnPrefs());
    expect(normalizeColumnPrefs(42)).toEqual(defaultColumnPrefs());
  });

  it('flags exceeding the soft cap without blocking', () => {
    const prefs = normalizeColumnPrefs({
      order: ['title', 'year', 'venue', 'status', 'added_at', 'doi', 'keywords'],
      visible: ['title', 'year', 'venue', 'status', 'added_at', 'doi', 'keywords'],
    });
    expect(prefs.visible).toHaveLength(7);
    expect(exceedsSoftCap(prefs)).toBe(true);
  });

  it('produces ordered visible defs honouring order then visibility', () => {
    const prefs = normalizeColumnPrefs({
      order: ['doi', 'title', 'year'],
      visible: ['title', 'doi'],
    });
    const defs = visibleColumnDefs(prefs);
    expect(defs.map((d) => d.id)).toEqual(['doi', 'title']);
  });
});
