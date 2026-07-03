import { describe, expect, it } from 'vitest';

import type { CitationSummary } from '../../api/client';
import { buildChronologicalOption, yearLabel } from './citationSummary';
import { resolveTheme } from './theme';

function makeSummary(overrides: Partial<CitationSummary> = {}): CitationSummary {
  return {
    scope_work_count: 3,
    most_cited_local: [],
    most_cited_external: [],
    frequently_cited_missing: [],
    bridge_papers: [],
    isolated_papers: [],
    chronological: [
      { year: 2018, work_count: 1 },
      { year: 2020, work_count: 2 },
      { year: null, work_count: 1 },
    ],
    bridge_method: 'brandes_betweenness_undirected',
    computed_at: '2026-07-03T00:00:00Z',
    version: 'abc',
    notes: [],
    ...overrides,
  };
}

describe('yearLabel', () => {
  it('labels a known year and the unknown-year bucket', () => {
    expect(yearLabel({ year: 2020, work_count: 2 })).toBe('2020');
    expect(yearLabel({ year: null, work_count: 1 })).toBe('Unknown');
  });
});

describe('buildChronologicalOption', () => {
  const theme = resolveTheme('light');

  it('maps year buckets onto the category axis and counts onto the bar series', () => {
    const option = buildChronologicalOption(makeSummary(), theme);
    expect((option.xAxis as { data: string[] }).data).toEqual(['2018', '2020', 'Unknown']);
    const series = option.series as Array<{ type: string; data: number[] }>;
    expect(series[0].type).toBe('bar');
    expect(series[0].data).toEqual([1, 2, 1]);
  });

  it('handles an empty chronological block without throwing', () => {
    const option = buildChronologicalOption(makeSummary({ chronological: [] }), theme);
    expect((option.xAxis as { data: string[] }).data).toEqual([]);
    expect((option.series as Array<{ data: number[] }>)[0].data).toEqual([]);
  });
});
