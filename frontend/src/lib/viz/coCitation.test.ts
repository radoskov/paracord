import { describe, expect, it } from 'vitest';

import type { VizPayload } from '../../api/client';
import { coCitationRenderer } from './coCitation';
import { getRenderer, registeredViewTypes } from './registry';
import { resolveTheme } from './theme';

function makePayload(overrides: Partial<VizPayload> = {}): VizPayload {
  return {
    view_type: 'co_citation',
    axes: null,
    axis_options: null,
    legend: { color_by: 'status', groups: ['read', 'unread'] },
    series: null,
    matrix: null,
    edges: [
      { source: 'a', target: 'b', weight: 2 },
      { source: 'b', target: 'c', weight: 1 },
    ],
    notes: [],
    nodes: [
      { id: 'a', x: null, y: null, size: 1, color_group: 'read', shape: 'in_library', label: 'A', meta: { degree: 1, year: 2020 } },
      { id: 'b', x: null, y: null, size: 2, color_group: 'unread', shape: 'in_library', label: 'B', meta: { degree: 2, year: 2019 } },
      { id: 'c', x: null, y: null, size: 1, color_group: 'read', shape: 'in_library', label: 'C', meta: { degree: 1, year: 2018 } },
    ],
    ...overrides,
  };
}

describe('co_citation renderer', () => {
  const theme = resolveTheme('light');

  it('registers itself in the view registry', () => {
    expect(registeredViewTypes()).toContain('co_citation');
    expect(getRenderer('co_citation')).toBe(coCitationRenderer);
  });

  it('builds a single graph series with force layout and one category per color group', () => {
    const option = coCitationRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{ type: string; layout: string; categories: unknown[] }>;
    expect(series).toHaveLength(1);
    expect(series[0].type).toBe('graph');
    expect(series[0].layout).toBe('force');
    expect(series[0].categories).toHaveLength(2);
  });

  it('maps nodes to graph data with work-id names and category indices', () => {
    const option = coCitationRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{
      data: Array<{ name: string; category: number; symbolSize: number }>;
    }>;
    const data = series[0].data;
    const b = data.find((d) => d.name === 'b');
    // `name` is the work id (drives click-to-open); 'unread' is legend group index 1.
    expect(b?.category).toBe(1);
    // Larger degree -> larger symbol.
    const a = data.find((d) => d.name === 'a');
    expect(b?.symbolSize).toBeGreaterThan(a?.symbolSize ?? 0);
  });

  it('maps edges to links with weight-scaled widths', () => {
    const option = coCitationRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{
      links: Array<{ source: string; target: string; value: number; lineStyle: { width: number } }>;
    }>;
    const links = series[0].links;
    expect(links).toHaveLength(2);
    const heavy = links.find((l) => l.source === 'a' && l.target === 'b');
    const light = links.find((l) => l.source === 'b' && l.target === 'c');
    // weight 2 (max) -> wider than weight 1 (min).
    expect(heavy?.lineStyle.width).toBeGreaterThan(light?.lineStyle.width ?? 0);
  });

  it('produces a node tooltip with the paper title and degree', () => {
    const option = coCitationRenderer.buildOption(makePayload(), theme);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    const html = formatter({ data: { node: makePayload().nodes[1] } });
    expect(html).toContain('B');
    expect(html).toContain('Linked papers: 2');
  });

  it('produces an edge tooltip with the shared count', () => {
    const option = coCitationRenderer.buildOption(makePayload(), theme);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    expect(formatter({ dataType: 'edge', data: { value: 3 } })).toContain('Shared: 3');
  });
});
