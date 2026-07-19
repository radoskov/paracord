import { describe, expect, it } from 'vitest';

import {
  columnWidthPercents,
  DEFAULT_VISIBLE,
  defaultColumnPrefs,
  exceedsSoftCap,
  LIBRARY_COLUMNS,
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
    expect(prefs.sort).toEqual([{ key: 'updated_at', order: 'desc' }]);
  });

  it('registers the batch-12 count columns as sortable, opt-in extras', () => {
    const prefs = normalizeColumnPrefs({});
    for (const id of [
      'arxiv_id',
      'reference_count',
      'citation_count',
      'local_reference_count',
      'local_citation_count',
    ]) {
      expect(prefs.order).toContain(id); // present in the registry
      expect(prefs.visible).not.toContain(id); // but hidden by default
    }
    // The count columns (and Files) are accepted as valid sort keys.
    for (const key of [
      'file_count',
      'reference_count',
      'citation_count',
      'local_reference_count',
      'local_citation_count',
    ] as const) {
      // A v1 single-object sort is still accepted (back-compat) and becomes a one-element list.
      expect(normalizeColumnPrefs({ sort: { key, order: 'desc' } }).sort[0].key).toBe(key);
    }
  });

  it('accepts the doi / shelves / racks / rows columns as sortable keys', () => {
    for (const key of ['doi', 'shelves', 'racks', 'rows'] as const) {
      expect(normalizeColumnPrefs({ sort: { key, order: 'asc' } }).sort[0].key).toBe(key);
    }
  });

  it('registers a rows column (opt-in) in the registry', () => {
    const prefs = normalizeColumnPrefs({});
    expect(prefs.order).toContain('rows');
    expect(prefs.visible).not.toContain('rows');
  });

  it('normalizes a multi-column sort list, deduping keys and clamping directions', () => {
    const prefs = normalizeColumnPrefs({
      sort: [
        { key: 'year', order: 'desc' },
        { key: 'title', order: 'asc' },
        { key: 'year', order: 'asc' }, // duplicate key → dropped (first wins)
        { key: 'bogus', order: 'asc' }, // unknown key → dropped
      ],
    });
    expect(prefs.sort).toEqual([
      { key: 'year', order: 'desc' },
      { key: 'title', order: 'asc' },
    ]);
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

describe('column width ratios + divider toggle', () => {
  it('defaults: every column has a width ratio and dividers are on', () => {
    const prefs = normalizeColumnPrefs({});
    for (const def of LIBRARY_COLUMNS) {
      expect(prefs.widths[def.id]).toBe(def.width);
    }
    expect(prefs.dividers).toBe(true);
  });

  it('keeps valid custom widths, clamps out-of-range ones, drops unknown ids', () => {
    const prefs = normalizeColumnPrefs({
      widths: { title: 50, year: 0, doi: 500, bogus: 10, venue: 'NaN' },
    });
    expect(prefs.widths.title).toBe(50);
    expect(prefs.widths.year).toBe(2); // clamped to MIN
    expect(prefs.widths.doi).toBe(80); // clamped to MAX
    expect(prefs.widths.venue).toBe(14); // invalid → registry default
    expect('bogus' in prefs.widths).toBe(false);
  });

  it('round-trips the dividers toggle and defaults it to true for legacy blobs', () => {
    expect(normalizeColumnPrefs({ dividers: false }).dividers).toBe(false);
    expect(normalizeColumnPrefs({ order: ['title'] }).dividers).toBe(true);
  });

  it('columnWidthPercents divides the ratios into percentages over the shown set', () => {
    const prefs = normalizeColumnPrefs({
      visible: ['title', 'year'],
      widths: { title: 30, year: 10 },
    });
    const defs = visibleColumnDefs(prefs);
    const pct = columnWidthPercents(defs, prefs.widths);
    expect(pct.title).toBe(75);
    expect(pct.year).toBe(25);
  });

  it('columnWidthPercents redistributes when the shown set changes (no re-tuning needed)', () => {
    const widths = normalizeColumnPrefs({ widths: { title: 30, year: 10, doi: 10 } }).widths;
    const two = columnWidthPercents(
      visibleColumnDefs(normalizeColumnPrefs({ visible: ['title', 'year'], widths })),
      widths,
    );
    const three = columnWidthPercents(
      visibleColumnDefs(normalizeColumnPrefs({ visible: ['title', 'year', 'doi'], widths })),
      widths,
    );
    expect(two.title).toBeGreaterThan(three.title); // same ratio, more columns → smaller share
    expect(three.title + three.year + three.doi).toBeCloseTo(100, 0);
  });
});
