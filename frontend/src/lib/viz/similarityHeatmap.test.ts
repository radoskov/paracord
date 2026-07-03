import { describe, expect, it } from 'vitest';

import type { VizPayload } from '../../api/client';
import { getRenderer, registeredViewTypes } from './registry';
import { similarityHeatmapRenderer } from './similarityHeatmap';
import { resolveTheme } from './theme';

function makePayload(overrides: Partial<VizPayload> = {}): VizPayload {
  return {
    view_type: 'similarity_heatmap',
    axes: null,
    axis_options: null,
    legend: null,
    edges: null,
    series: null,
    matrix: {
      labels: ['A', 'B'],
      ids: ['id-a', 'id-b'],
      values: [
        [1.0, 0.5],
        [0.5, 1.0],
      ],
    },
    notes: [],
    nodes: [],
    ...overrides,
  };
}

describe('similarity_heatmap renderer', () => {
  const theme = resolveTheme('light');

  it('registers itself in the view registry', () => {
    expect(registeredViewTypes()).toContain('similarity_heatmap');
    expect(getRenderer('similarity_heatmap')).toBe(similarityHeatmapRenderer);
  });

  it('labels both category axes with the matrix labels', () => {
    const option = similarityHeatmapRenderer.buildOption(makePayload(), theme);
    expect((option.xAxis as { data: string[] }).data).toEqual(['A', 'B']);
    expect((option.yAxis as { data: string[] }).data).toEqual(['A', 'B']);
  });

  it('emits one heatmap cell per matrix entry as [col, row, value]', () => {
    const option = similarityHeatmapRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{ type: string; data: [number, number, number][] }>;
    expect(series[0].type).toBe('heatmap');
    expect(series[0].data).toHaveLength(4);
    // Diagonal cell (0,0) carries the 1.0 self-similarity.
    expect(series[0].data).toContainEqual([0, 0, 1.0]);
    // Off-diagonal [col=1,row=0] = values[0][1] = 0.5.
    expect(series[0].data).toContainEqual([1, 0, 0.5]);
  });

  it('caps the visualMap at 1 and floors it at min(0, matrix min)', () => {
    const option = similarityHeatmapRenderer.buildOption(
      makePayload({
        matrix: { labels: ['A', 'B'], ids: ['a', 'b'], values: [[1.0, -0.2], [-0.2, 1.0]] },
      }),
      theme,
    );
    const vm = option.visualMap as { min: number; max: number };
    expect(vm.max).toBe(1);
    expect(vm.min).toBeCloseTo(-0.2);
  });

  it('produces a tooltip naming both papers and the similarity', () => {
    const option = similarityHeatmapRenderer.buildOption(makePayload(), theme);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    const html = formatter({ value: [1, 0, 0.5] });
    expect(html).toContain('A');
    expect(html).toContain('B');
    expect(html).toContain('0.500');
  });

  it('returns an empty option when there is no matrix', () => {
    const option = similarityHeatmapRenderer.buildOption(makePayload({ matrix: null }), theme);
    expect(option.series).toEqual([]);
  });
});
