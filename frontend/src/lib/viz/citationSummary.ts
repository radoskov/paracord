// Citation-summary data mapping (D38 P4). Pure, synchronous helpers that turn a CitationSummary
// into view-ready shapes — kept out of the Svelte component so they are unit-testable in jsdom
// without importing the heavy echarts bundle. `buildChronologicalOption` produces a plain ECharts
// bar option (structural, not typed against echarts) for the year-distribution chart, reusing the
// shared viz theme so it matches every other chart in the module.

import type { CitationSummary, YearCount } from '../../api/client';
import type { EChartsOptionLike } from './registry';
import type { VizTheme } from './theme';

// Human-readable label for a year bucket (the unknown-year bucket has a null year).
export function yearLabel(entry: YearCount): string {
  return entry.year === null ? 'Unknown' : String(entry.year);
}

// Build the year-distribution bar chart option from the chronological block. Empty input yields an
// option with no data (the caller decides whether to render at all).
export function buildChronologicalOption(
  summary: CitationSummary,
  theme: VizTheme,
): EChartsOptionLike {
  const labels = summary.chronological.map(yearLabel);
  const counts = summary.chronological.map((e) => e.work_count);
  return {
    backgroundColor: theme.background,
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBg,
      textStyle: { color: theme.tooltipText },
    },
    grid: { left: 48, right: 16, top: 16, bottom: 48 },
    xAxis: {
      type: 'category',
      data: labels,
      name: 'Publication year',
      nameLocation: 'middle',
      nameGap: 30,
      axisLine: { lineStyle: { color: theme.axisLine } },
      axisLabel: { color: theme.text },
    },
    yAxis: {
      type: 'value',
      name: 'Papers',
      minInterval: 1,
      axisLine: { lineStyle: { color: theme.axisLine } },
      splitLine: { lineStyle: { color: theme.splitLine } },
    },
    series: [
      {
        type: 'bar',
        name: 'Papers',
        color: theme.categorical[0],
        data: counts,
      },
    ],
  };
}
