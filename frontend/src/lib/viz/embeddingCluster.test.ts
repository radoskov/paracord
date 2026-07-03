import { describe, expect, it } from 'vitest';

import type { VizPayload } from '../../api/client';
import { embeddingClusterRenderer } from './embeddingCluster';
import { getRenderer, registeredViewTypes } from './registry';
import { resolveTheme } from './theme';

function makePayload(overrides: Partial<VizPayload> = {}): VizPayload {
  return {
    view_type: 'embedding_cluster',
    axes: { x: { key: 'component_1', label: 'Component 1' }, y: { key: 'component_2', label: 'Component 2' } },
    axis_options: null,
    legend: { color_by: 'cluster', groups: ['1. attention', '2. vision'] },
    edges: null,
    notes: [],
    nodes: [
      { id: 'a', x: -1.2, y: 0.4, size: 3, color_group: '1. attention', shape: 'in_library', label: 'A', meta: { cluster: '1. attention', year: 2020 } },
      { id: 'b', x: 1.1, y: -0.3, size: 1, color_group: '2. vision', shape: 'in_library', label: 'B', meta: { cluster: '2. vision', year: 2018 } },
      // Unplaceable (null coordinate) → excluded from the plot.
      { id: 'c', x: null, y: null, size: 0, color_group: '1. attention', shape: 'in_library', label: 'C', meta: {} },
    ],
    ...overrides,
  };
}

describe('embedding_cluster renderer', () => {
  const theme = resolveTheme('light');

  it('registers itself in the view registry', () => {
    expect(registeredViewTypes()).toContain('embedding_cluster');
    expect(getRenderer('embedding_cluster')).toBe(embeddingClusterRenderer);
  });

  it('labels the axes as the two fixed PCA components', () => {
    const option = embeddingClusterRenderer.buildOption(makePayload(), theme);
    expect((option.xAxis as { name: string }).name).toBe('Component 1');
    expect((option.yAxis as { name: string }).name).toBe('Component 2');
  });

  it('splits nodes into one scatter series per cluster and excludes unplaceable points', () => {
    const option = embeddingClusterRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{ type: string; name: string; data: unknown[] }>;
    const scatter = series.filter((s) => s.type === 'scatter');
    expect(scatter.map((s) => s.name).sort()).toEqual(['1. attention', '2. vision']);
    // '1. attention' has a (placed) + c (unplaceable, excluded) → 1 point.
    const attention = scatter.find((s) => s.name === '1. attention');
    expect(attention?.data).toHaveLength(1);
    expect((attention?.data[0] as { name: string }).name).toBe('a');
  });

  it('produces a tooltip with the paper title and cluster', () => {
    const option = embeddingClusterRenderer.buildOption(makePayload(), theme);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    const html = formatter({ data: { node: makePayload().nodes[0] } });
    expect(html).toContain('A');
    expect(html).toContain('Cluster: 1. attention');
  });

  it('falls back to a single series when no clusters are reported', () => {
    const option = embeddingClusterRenderer.buildOption(
      makePayload({ legend: { color_by: 'cluster', groups: [] } }),
      theme,
    );
    const series = option.series as Array<{ type: string; name: string }>;
    const scatter = series.filter((s) => s.type === 'scatter');
    expect(scatter).toHaveLength(1);
    expect(scatter[0].name).toBe('Papers');
    expect(option.legend).toBeUndefined();
  });
});
