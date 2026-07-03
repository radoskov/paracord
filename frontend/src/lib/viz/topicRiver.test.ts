import { describe, expect, it } from 'vitest';

import type { VizPayload } from '../../api/client';
import { getRenderer, registeredViewTypes } from './registry';
import { resolveTheme } from './theme';
import { topicRiverRenderer } from './topicRiver';

function makePayload(overrides: Partial<VizPayload> = {}): VizPayload {
  return {
    view_type: 'topic_river',
    axes: null,
    axis_options: null,
    legend: { color_by: 'cluster', groups: ['1. attention', '2. vision'] },
    edges: null,
    matrix: null,
    series: {
      years: [2019, 2020, 2021],
      topics: [
        { label: '1. attention', values: [1.0, 0.5, 0.25] },
        { label: '2. vision', values: [0.0, 0.5, 0.75] },
      ],
    },
    notes: [],
    nodes: [],
    ...overrides,
  };
}

describe('topic_river renderer', () => {
  const theme = resolveTheme('light');

  it('registers itself in the view registry', () => {
    expect(registeredViewTypes()).toContain('topic_river');
    expect(getRenderer('topic_river')).toBe(topicRiverRenderer);
  });

  it('maps years onto a boundary-gap-free category axis', () => {
    const option = topicRiverRenderer.buildOption(makePayload(), theme);
    const xAxis = option.xAxis as { data: string[]; boundaryGap: boolean };
    expect(xAxis.data).toEqual(['2019', '2020', '2021']);
    expect(xAxis.boundaryGap).toBe(false);
  });

  it('builds one stacked area series per topic', () => {
    const option = topicRiverRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{ type: string; name: string; stack: string; data: number[] }>;
    expect(series).toHaveLength(2);
    expect(series.map((s) => s.name)).toEqual(['1. attention', '2. vision']);
    expect(series.every((s) => s.type === 'line' && s.stack === 'total')).toBe(true);
    expect(series[0].data).toEqual([1.0, 0.5, 0.25]);
  });

  it('caps the topic-share axis at 0..1', () => {
    const option = topicRiverRenderer.buildOption(makePayload(), theme);
    const yAxis = option.yAxis as { min: number; max: number };
    expect(yAxis.min).toBe(0);
    expect(yAxis.max).toBe(1);
  });

  it('returns an empty option when there is no series', () => {
    const option = topicRiverRenderer.buildOption(makePayload({ series: null }), theme);
    expect(option.series).toEqual([]);
  });
});
